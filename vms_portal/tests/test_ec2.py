from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber
from vms_portal.ec2 import Ec2Service, VmNotManaged


def client():
    return boto3.client(
        "ec2",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_session_token="test",
    )


def instance(
    *,
    instance_id: str = "i-1234567890abcdef0",
    state: str = "running",
    managed: bool = True,
):
    tags = [{"Key": "Name", "Value": "windows-a11y-demo"}]
    if managed:
        tags.append({"Key": "VmPortalManaged", "Value": "true"})
    return {
        "InstanceId": instance_id,
        "ImageId": "ami-1234567890abcdef0",
        "InstanceType": "m5.xlarge",
        "LaunchTime": datetime(2026, 8, 20, tzinfo=UTC),
        "PrivateIpAddress": "10.0.0.4",
        "PublicIpAddress": "198.51.100.9",
        "State": {"Code": 16 if state == "running" else 80, "Name": state},
        "SubnetId": "subnet-12345678",
        "VpcId": "vpc-12345678",
        "Architecture": "x86_64",
        "RootDeviceName": "/dev/sda1",
        "RootDeviceType": "ebs",
        "VirtualizationType": "hvm",
        "Tags": tags,
    }


def test_list_managed_uses_tag_and_active_state_filters() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    expected = {
        "Filters": [
            {"Name": "tag:VmPortalManaged", "Values": ["true"]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    }
    stubber.add_response(
        "describe_instances", {"Reservations": [{"Instances": [instance()]}]}, expected
    )
    with stubber:
        result = Ec2Service(ec2).list_managed()

    assert [
        (
            vm.instance_id,
            vm.name,
            str(vm.private_ip),
            str(vm.public_ip),
            vm.state,
        )
        for vm in result
    ] == [
        (
            "i-1234567890abcdef0",
            "windows-a11y-demo",
            "10.0.0.4",
            "198.51.100.9",
            "running",
        )
    ]


def test_user_lookup_requires_exact_instance_id_and_tag() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [instance()]}]},
        {"InstanceIds": ["i-1234567890abcdef0"]},
    )

    with stubber:
        found = Ec2Service(ec2).find_managed_by_instance_id("i-1234567890abcdef0")

    assert found is not None
    assert found.instance_id == "i-1234567890abcdef0"
    assert str(found.private_ip) == "10.0.0.4"


def test_user_lookup_returns_none_when_instance_id_does_not_exist() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    stubber.add_client_error(
        "describe_instances",
        service_error_code="InvalidInstanceID.NotFound",
        service_message="The instance ID does not exist",
        expected_params={"InstanceIds": ["i-00000000000000000"]},
    )

    with stubber:
        found = Ec2Service(ec2).find_managed_by_instance_id("i-00000000000000000")

    assert found is None


def test_normalization_allows_missing_dynamic_public_ip() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    expected = {
        "Filters": [
            {"Name": "tag:VmPortalManaged", "Values": ["true"]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    }
    raw = instance()
    raw.pop("PublicIpAddress")
    stubber.add_response(
        "describe_instances", {"Reservations": [{"Instances": [raw]}]}, expected
    )

    with stubber:
        result = Ec2Service(ec2).list_managed()

    assert str(result[0].private_ip) == "10.0.0.4"
    assert result[0].public_ip is None


def test_stop_revalidates_tag_and_uses_normal_stop() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [instance()]}]},
        {"InstanceIds": ["i-1234567890abcdef0"]},
    )
    stubber.add_response(
        "stop_instances",
        {
            "StoppingInstances": [
                {
                    "InstanceId": "i-1234567890abcdef0",
                    "CurrentState": {"Code": 64, "Name": "stopping"},
                    "PreviousState": {"Code": 16, "Name": "running"},
                }
            ]
        },
        {"InstanceIds": ["i-1234567890abcdef0"]},
    )

    with stubber:
        result = Ec2Service(ec2).stop("i-1234567890abcdef0")

    assert result.state == "stopping"


def test_mutation_rejects_instance_that_lost_management_tag() -> None:
    ec2 = client()
    stubber = Stubber(ec2)
    stubber.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [instance(managed=False)]}]},
        {"InstanceIds": ["i-1234567890abcdef0"]},
    )

    with stubber, pytest.raises(VmNotManaged):
        Ec2Service(ec2).stop("i-1234567890abcdef0")
