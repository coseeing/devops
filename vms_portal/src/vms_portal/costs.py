from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError


class CostUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstanceCost:
    instance_id: str
    amount: Decimal
    currency: str
    estimated: bool
    retrieved_at: datetime


class CostService:
    def __init__(self, client: Any, cache_seconds: int = 21_600) -> None:
        self._client = client
        self._cache_seconds = cache_seconds
        self._cache_key: frozenset[str] = frozenset()
        self._cache_at: datetime | None = None
        self._cache: dict[str, InstanceCost] = {}

    def get_costs(
        self, instance_ids: Sequence[str], now: datetime
    ) -> Mapping[str, InstanceCost]:
        now = now.astimezone(UTC)
        key = frozenset(instance_ids)
        if not key:
            return {}
        if (
            self._cache_at is not None
            and key == self._cache_key
            and (now - self._cache_at).total_seconds() < self._cache_seconds
        ):
            return dict(self._cache)
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
                    {"Dimensions": {"Key": "RESOURCE_ID", "Values": sorted(key)}},
                ]
            },
            "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            "Metrics": ["UnblendedCost"],
        }
        amounts = {instance_id: Decimal(0) for instance_id in key}
        currencies = {instance_id: "USD" for instance_id in key}
        estimated = {instance_id: False for instance_id in key}
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
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise CostUnavailable(f"Cost Explorer unavailable: {code}") from exc
        result = {
            instance_id: InstanceCost(
                instance_id,
                amounts[instance_id],
                currencies[instance_id],
                estimated[instance_id],
                now,
            )
            for instance_id in key
        }
        self._cache_key = key
        self._cache_at = now
        self._cache = result
        return dict(result)
