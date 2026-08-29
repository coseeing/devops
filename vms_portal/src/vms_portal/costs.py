from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from botocore.exceptions import ClientError

from .ec2 import VmInstance

_INSTANCE_ID = re.compile(r"^i-[0-9a-f]+$")
_SQL_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")
_NOT_READY_REASONS = ("TABLE_NOT_FOUND", "does not exist", "not found")


@dataclass(frozen=True, slots=True)
class InstanceCost:
    instance_id: str
    status: str
    amount: Decimal | None
    currency: str
    period_start: date | None
    period_end: date | None
    retrieved_at: datetime
    query_execution_id: str | None = None


class CostService:
    def __init__(
        self,
        client: Any,
        *,
        database: str,
        table: str,
        workgroup: str,
        cache_seconds: int = 21_600,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 0.25,
        max_poll_attempts: int = 120,
        logger: logging.Logger | None = None,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(database):
            raise ValueError("invalid Athena database")
        if not _SQL_IDENTIFIER.fullmatch(table):
            raise ValueError("invalid Athena table")
        self._client = client
        self._database = database
        self._table = table
        self._workgroup = workgroup
        self._cache_seconds = cache_seconds
        self._sleeper = sleeper
        self._poll_interval_seconds = poll_interval_seconds
        self._max_poll_attempts = max_poll_attempts
        self._logger = logger or logging.getLogger(__name__)
        self._cache_key: frozenset[str] = frozenset()
        self._cache_at: datetime | None = None
        self._cache: dict[str, InstanceCost] = {}

    def get_costs(
        self, instances: Sequence[VmInstance], now: datetime
    ) -> Mapping[str, InstanceCost]:
        now = now.astimezone(UTC)
        instance_ids = frozenset(vm.instance_id for vm in instances)
        if not instance_ids:
            return {}
        if (
            self._cache_at is not None
            and instance_ids == self._cache_key
            and (now - self._cache_at).total_seconds() < self._cache_seconds
        ):
            return dict(self._cache)

        query_id: str | None = None
        try:
            response = self._client.start_query_execution(
                QueryString=self._build_query(instance_ids, now),
                QueryExecutionContext={"Database": self._database},
                WorkGroup=self._workgroup,
            )
            query_id = response["QueryExecutionId"]
            status, reason = self._wait(query_id)
            if status != "SUCCEEDED":
                result_status = (
                    "not_ready"
                    if any(
                        marker.casefold() in reason.casefold()
                        for marker in _NOT_READY_REASONS
                    )
                    else "failed"
                )
                self._logger.error(
                    "Athena cost query ended in %s: %s",
                    status,
                    reason,
                    extra={"query_execution_id": query_id},
                )
                result = self._empty(instance_ids, result_status, now, query_id)
            else:
                result = self._read_results(instance_ids, now, query_id)
        except ClientError as exc:
            metadata = exc.response.get("ResponseMetadata", {})
            self._logger.exception(
                "Athena cost API failure: %s",
                exc.response.get("Error", {}).get("Code", "unknown"),
                extra={
                    "aws_request_id": metadata.get("RequestId"),
                    "query_execution_id": query_id,
                },
            )
            result = self._empty(instance_ids, "failed", now, query_id)
        except (InvalidOperation, ValueError, KeyError, IndexError):
            self._logger.exception(
                "Athena cost result could not be parsed",
                extra={"query_execution_id": query_id},
            )
            result = self._empty(instance_ids, "failed", now, query_id)

        self._cache_key = instance_ids
        self._cache_at = now
        self._cache = result
        return dict(result)

    def _build_query(self, instance_ids: frozenset[str], now: datetime) -> str:
        if any(not _INSTANCE_ID.fullmatch(instance_id) for instance_id in instance_ids):
            raise ValueError("invalid EC2 instance ID")
        end = now.date() + timedelta(days=1)
        start = end - timedelta(days=60)
        ids = ", ".join(f"'{instance_id}'" for instance_id in sorted(instance_ids))
        periods = ", ".join(f"'{value}'" for value in _billing_periods(start, end))
        return f"""
SELECT
  line_item_resource_id AS instance_id,
  SUM(
    CASE
      WHEN line_item_line_item_type = 'SavingsPlanCoveredUsage'
        THEN savings_plan_savings_plan_effective_cost
      WHEN line_item_line_item_type = 'DiscountedUsage'
        THEN reservation_effective_cost
      ELSE line_item_unblended_cost
    END
  ) AS amount,
  MAX(line_item_currency_code) AS currency,
  MIN(CAST(line_item_usage_start_date AS DATE)) AS period_start,
  MAX(CAST(line_item_usage_start_date AS DATE)) AS period_end
FROM "{self._database}"."{self._table}"
WHERE billing_period IN ({periods})
  AND line_item_usage_start_date >= TIMESTAMP '{start.isoformat()} 00:00:00'
  AND line_item_usage_start_date < TIMESTAMP '{end.isoformat()} 00:00:00'
  AND line_item_resource_id IN ({ids})
GROUP BY line_item_resource_id
""".strip()

    def _wait(self, query_id: str) -> tuple[str, str]:
        for _ in range(self._max_poll_attempts):
            response = self._client.get_query_execution(QueryExecutionId=query_id)
            status = response["QueryExecution"]["Status"]
            state = status["State"]
            if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return state, status.get("StateChangeReason", "")
            self._sleeper(self._poll_interval_seconds)
        return "FAILED", "query polling timed out"

    def _read_results(
        self,
        instance_ids: frozenset[str],
        now: datetime,
        query_id: str,
    ) -> dict[str, InstanceCost]:
        result = self._empty(instance_ids, "not_ready", now, query_id)
        request: dict[str, str] = {"QueryExecutionId": query_id}
        first_row = True
        while True:
            response = self._client.get_query_results(**request)
            for row in response.get("ResultSet", {}).get("Rows", []):
                if first_row:
                    first_row = False
                    continue
                values = [item.get("VarCharValue", "") for item in row.get("Data", [])]
                if len(values) < 5 or values[0] not in instance_ids:
                    continue
                result[values[0]] = InstanceCost(
                    instance_id=values[0],
                    status="ready",
                    amount=Decimal(values[1]),
                    currency=values[2] or "USD",
                    period_start=date.fromisoformat(values[3]),
                    period_end=date.fromisoformat(values[4]),
                    retrieved_at=now,
                    query_execution_id=query_id,
                )
            token = response.get("NextToken")
            if not token:
                break
            request["NextToken"] = token
        return result

    @staticmethod
    def _empty(
        instance_ids: frozenset[str],
        status: str,
        now: datetime,
        query_id: str | None,
    ) -> dict[str, InstanceCost]:
        return {
            instance_id: InstanceCost(
                instance_id=instance_id,
                status=status,
                amount=None,
                currency="USD",
                period_start=None,
                period_end=None,
                retrieved_at=now,
                query_execution_id=query_id,
            )
            for instance_id in instance_ids
        }


def _billing_periods(start: date, end: date) -> list[str]:
    current = start.replace(day=1)
    last = (end - timedelta(days=1)).replace(day=1)
    result: list[str] = []
    while current <= last:
        result.append(current.strftime("%Y-%m"))
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
    return result
