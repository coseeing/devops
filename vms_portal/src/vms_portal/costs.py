from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from .ec2 import VmInstance


@dataclass(frozen=True, slots=True)
class InstanceCost:
    instance_id: str
    ec2_amount: Decimal | None
    eip_amount: Decimal | None
    currency: str
    estimated: bool
    retrieved_at: datetime

    @property
    def total_amount(self) -> Decimal | None:
        if self.ec2_amount is None or self.eip_amount is None:
            return None
        return self.ec2_amount + self.eip_amount


class CostService:
    def __init__(
        self,
        client: Any,
        cache_seconds: int = 21_600,
        public_ipv4_hourly_usd: Decimal = Decimal("0.005"),
    ) -> None:
        self._client = client
        self._cache_seconds = cache_seconds
        self._public_ipv4_hourly_usd = public_ipv4_hourly_usd
        self._cache_key: frozenset[tuple[str, str | None, datetime | None]] = (
            frozenset()
        )
        self._cache_at: datetime | None = None
        self._cache: dict[str, InstanceCost] = {}

    def get_costs(
        self, instances: Sequence[VmInstance], now: datetime
    ) -> Mapping[str, InstanceCost]:
        now = now.astimezone(UTC)
        key = frozenset(
            (vm.instance_id, vm.eip_allocation_id, vm.eip_created_at)
            for vm in instances
        )
        if not key:
            return {}
        if (
            self._cache_at is not None
            and key == self._cache_key
            and (now - self._cache_at).total_seconds() < self._cache_seconds
        ):
            return dict(self._cache)
        instance_ids = {vm.instance_id for vm in instances}
        end = now.date()
        start = end - timedelta(days=14)
        request: dict[str, Any] = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "DAILY",
            "Filter": {
                "And": [
                    {
                        "Dimensions": {
                            "Key": "SERVICE",
                            "Values": ["Amazon Elastic Compute Cloud - Compute"],
                        }
                    },
                    {
                        "Dimensions": {
                            "Key": "RESOURCE_ID",
                            "Values": sorted(instance_ids),
                        }
                    },
                ]
            },
            "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            "Metrics": ["UnblendedCost"],
        }
        amounts = {instance_id: Decimal(0) for instance_id in instance_ids}
        currencies = {instance_id: "USD" for instance_id in instance_ids}
        estimated = {instance_id: False for instance_id in instance_ids}
        ec2_available = True
        try:
            while True:
                response = self._client.get_cost_and_usage_with_resources(**request)
                for period in response.get("ResultsByTime", []):
                    for group in period.get("Groups", []):
                        instance_id = group["Keys"][0]
                        if instance_id not in amounts:
                            continue
                        metric = group["Metrics"]["UnblendedCost"]
                        amounts[instance_id] += Decimal(metric["Amount"])
                        currencies[instance_id] = metric["Unit"]
                        estimated[instance_id] = estimated[instance_id] or bool(
                            period.get("Estimated")
                        )
                token = response.get("NextPageToken")
                if not token:
                    break
                request["NextPageToken"] = token
        except ClientError:
            ec2_available = False
        result = {
            vm.instance_id: InstanceCost(
                vm.instance_id,
                amounts[vm.instance_id] if ec2_available else None,
                _estimate_eip_cost(vm, now, self._public_ipv4_hourly_usd),
                currencies[vm.instance_id],
                estimated[vm.instance_id],
                now,
            )
            for vm in instances
        }
        self._cache_key = key
        self._cache_at = now
        self._cache = result
        return dict(result)


def _estimate_eip_cost(
    vm: VmInstance, now: datetime, hourly_rate: Decimal
) -> Decimal | None:
    if vm.eip_allocation_id is None:
        return Decimal(0)
    if vm.eip_created_at is None:
        return None
    start = max(vm.eip_created_at.astimezone(UTC), now - timedelta(days=14))
    elapsed = now - start
    if elapsed.total_seconds() <= 0:
        return Decimal(0)
    seconds = Decimal(elapsed.days * 86_400 + elapsed.seconds) + (
        Decimal(elapsed.microseconds) / Decimal(1_000_000)
    )
    return (seconds / Decimal(3_600)) * hourly_rate
