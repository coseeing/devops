from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from ipaddress import IPv4Address
from typing import Any

from botocore.exceptions import ClientError


class VmError(RuntimeError):
    pass


class VmNotFound(VmError):
    pass


class VmNotManaged(VmError):
    pass


class VmIpChanged(VmError):
    pass


class InvalidStateTransition(VmError):
    pass


@dataclass(frozen=True, slots=True)
class VmInstance:
    instance_id: str
    name: str
    private_ip: IPv4Address
    public_ip: IPv4Address | None
    instance_type: str
    state: str
    launch_time: datetime


_ACTIVE_STATES = ["pending", "running", "stopping", "stopped"]


class Ec2Service:
    def __init__(
        self, client: Any, tag_key: str = "VmPortalManaged", tag_value: str = "true"
    ) -> None:
        self._client = client
        self._tag_key = tag_key
        self._tag_value = tag_value

    def list_managed(self) -> list[VmInstance]:
        filters = self._managed_filters()
        paginator = self._client.get_paginator("describe_instances")
        instances = [
            vm
            for page in paginator.paginate(Filters=filters)
            for vm in _normalize_page(page)
        ]
        return sorted(instances, key=lambda vm: (vm.name.casefold(), vm.instance_id))

    def find_managed_by_instance_id(self, instance_id: str) -> VmInstance | None:
        try:
            response = self._client.describe_instances(InstanceIds=[instance_id])
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "InvalidInstanceID.NotFound"
            ):
                return None
            raise
        instances = _normalize_page(response)
        if not instances:
            return None
        raw = response["Reservations"][0]["Instances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in raw.get("Tags", [])}
        if tags.get(self._tag_key) != self._tag_value:
            return None
        if instances[0].state not in _ACTIVE_STATES:
            return None
        return instances[0]

    def start(
        self, instance_id: str, expected_public_ip: IPv4Address | None = None
    ) -> VmInstance:
        vm = self._revalidate(instance_id, expected_public_ip)
        if vm.state != "stopped":
            raise InvalidStateTransition(
                f"cannot start an instance in {vm.state} state"
            )
        response = self._client.start_instances(InstanceIds=[instance_id])
        state = response["StartingInstances"][0]["CurrentState"]["Name"]
        return replace(vm, state=state)

    def stop(
        self, instance_id: str, expected_public_ip: IPv4Address | None = None
    ) -> VmInstance:
        vm = self._revalidate(instance_id, expected_public_ip)
        if vm.state != "running":
            raise InvalidStateTransition(f"cannot stop an instance in {vm.state} state")
        response = self._client.stop_instances(InstanceIds=[instance_id])
        state = response["StoppingInstances"][0]["CurrentState"]["Name"]
        return replace(vm, state=state)

    def _managed_filters(self) -> list[dict[str, object]]:
        return [
            {"Name": f"tag:{self._tag_key}", "Values": [self._tag_value]},
            {"Name": "instance-state-name", "Values": _ACTIVE_STATES},
        ]

    def _revalidate(
        self, instance_id: str, expected_public_ip: IPv4Address | None
    ) -> VmInstance:
        response = self._client.describe_instances(InstanceIds=[instance_id])
        instances = _normalize_page(response)
        if not instances:
            raise VmNotFound("instance not found")
        raw = response["Reservations"][0]["Instances"][0]
        tags = {tag["Key"]: tag["Value"] for tag in raw.get("Tags", [])}
        if tags.get(self._tag_key) != self._tag_value:
            raise VmNotManaged("instance is not portal managed")
        vm = instances[0]
        if expected_public_ip is not None and vm.public_ip != expected_public_ip:
            raise VmIpChanged("instance public IP changed")
        return vm


def _normalize_page(page: dict[str, Any]) -> list[VmInstance]:
    result: list[VmInstance] = []
    for reservation in page.get("Reservations", []):
        for raw in reservation.get("Instances", []):
            tags = {tag["Key"]: tag["Value"] for tag in raw.get("Tags", [])}
            public_ip = raw.get("PublicIpAddress")
            result.append(
                VmInstance(
                    instance_id=raw["InstanceId"],
                    name=tags.get("Name", raw["InstanceId"]),
                    private_ip=IPv4Address(raw["PrivateIpAddress"]),
                    public_ip=IPv4Address(public_ip) if public_ip else None,
                    instance_type=raw["InstanceType"],
                    state=raw["State"]["Name"],
                    launch_time=raw["LaunchTime"],
                )
            )
    return result
