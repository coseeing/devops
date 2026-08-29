from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from ipaddress import IPv4Address

from botocore.exceptions import ClientError
from vms_portal.costs import CostService
from vms_portal.ec2 import VmInstance


def vm(instance_id: str) -> VmInstance:
    return VmInstance(
        instance_id,
        instance_id,
        IPv4Address("10.0.0.4"),
        None,
        "m5.xlarge",
        "stopped",
        datetime(2026, 8, 20, tzinfo=UTC),
    )


def row(*values: str):
    return {"Data": [{"VarCharValue": value} for value in values]}


class FakeAthena:
    def __init__(self, *, state="SUCCEEDED", reason="", pages=None):
        self.state = state
        self.reason = reason
        self.pages = pages or [{"ResultSet": {"Rows": []}}]
        self.start_calls = []
        self.execution_calls = []
        self.result_calls = []
        self.start_error = None

    def start_query_execution(self, **kwargs):
        self.start_calls.append(kwargs)
        if self.start_error:
            raise self.start_error
        return {"QueryExecutionId": "query-123"}

    def get_query_execution(self, **kwargs):
        self.execution_calls.append(kwargs)
        return {
            "QueryExecution": {
                "Status": {
                    "State": self.state,
                    "StateChangeReason": self.reason,
                }
            }
        }

    def get_query_results(self, **kwargs):
        self.result_calls.append(kwargs)
        index = 0 if "NextToken" not in kwargs else int(kwargs["NextToken"])
        result = dict(self.pages[index])
        if index + 1 < len(self.pages):
            result["NextToken"] = str(index + 1)
        return result


def service(client, **kwargs):
    return CostService(
        client,
        database="vms_portal_costs",
        table="cur2",
        workgroup="vms-portal-costs",
        sleeper=lambda _: None,
        **kwargs,
    )


def test_batches_visible_instances_into_one_sixty_day_effective_cost_query() -> None:
    client = FakeAthena(
        pages=[
            {
                "ResultSet": {
                    "Rows": [
                        row(
                            "instance_id",
                            "amount",
                            "currency",
                            "period_start",
                            "period_end",
                        ),
                        row(
                            "i-11111111111111111",
                            "12.50",
                            "USD",
                            "2026-07-01",
                            "2026-08-28",
                        ),
                        row(
                            "i-22222222222222222",
                            "0",
                            "USD",
                            "2026-08-01",
                            "2026-08-28",
                        ),
                    ]
                }
            }
        ]
    )

    result = service(client).get_costs(
        [vm("i-11111111111111111"), vm("i-22222222222222222")],
        datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert len(client.start_calls) == 1
    query = client.start_calls[0]["QueryString"]
    assert 'FROM "vms_portal_costs"."cur2"' in query
    assert "SavingsPlanCoveredUsage" in query
    assert "savings_plan_savings_plan_effective_cost" in query
    assert "DiscountedUsage" in query
    assert "reservation_effective_cost" in query
    assert "line_item_unblended_cost" in query
    assert "WHEN line_item_line_item_type = 'Usage'" in query
    assert "ELSE CAST(0 AS DECIMAL(38,18))" in query
    assert "2026-07-01" in query and "2026-08-30" in query
    assert client.start_calls[0]["WorkGroup"] == "vms-portal-costs"
    assert result["i-11111111111111111"].status == "ready"
    assert result["i-11111111111111111"].amount == Decimal("12.50")
    assert result["i-11111111111111111"].period_start == date(2026, 7, 1)
    assert result["i-22222222222222222"].status == "ready"
    assert result["i-22222222222222222"].amount == Decimal(0)


def test_pages_results_and_marks_instances_without_history_not_ready() -> None:
    client = FakeAthena(
        pages=[
            {"ResultSet": {"Rows": [row("headers")]}},
            {
                "ResultSet": {
                    "Rows": [
                        row(
                            "i-11111111111111111",
                            "1.00",
                            "USD",
                            "2026-08-01",
                            "2026-08-28",
                        )
                    ]
                }
            },
        ]
    )

    result = service(client).get_costs(
        [vm("i-11111111111111111"), vm("i-22222222222222222")],
        datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert len(client.result_calls) == 2
    assert result["i-11111111111111111"].status == "ready"
    assert result["i-22222222222222222"].status == "not_ready"


def test_missing_cur_table_is_not_ready_but_other_query_failure_is_failed(
    caplog,
) -> None:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    missing = service(FakeAthena(state="FAILED", reason="TABLE_NOT_FOUND: cur2"))
    failed = service(FakeAthena(state="FAILED", reason="GENERIC_INTERNAL_ERROR"))

    assert (
        missing.get_costs([vm("i-11111111111111111")], now)[
            "i-11111111111111111"
        ].status
        == "not_ready"
    )
    assert (
        failed.get_costs([vm("i-11111111111111111")], now)["i-11111111111111111"].status
        == "failed"
    )
    assert "query-123" in caplog.text


def test_athena_api_failure_is_failed_and_cached_for_six_hours(caplog) -> None:
    client = FakeAthena()
    client.start_error = ClientError(
        {
            "Error": {"Code": "AccessDeniedException", "Message": "denied"},
            "ResponseMetadata": {"RequestId": "request-123"},
        },
        "StartQueryExecution",
    )
    costs = service(client)
    instances = [vm("i-11111111111111111")]
    now = datetime(2026, 8, 29, tzinfo=UTC)

    first = costs.get_costs(instances, now)
    second = costs.get_costs(instances, now.replace(hour=5))

    assert first[instances[0].instance_id].status == "failed"
    assert second == first
    assert len(client.start_calls) == 1
    assert "AccessDeniedException" in caplog.text
    assert "request-123" in caplog.text


def test_malformed_athena_result_is_reported_as_failed() -> None:
    client = FakeAthena(
        pages=[
            {
                "ResultSet": {
                    "Rows": [
                        row("headers"),
                        row("i-11111111111111111", "not-a-number", "USD", "", ""),
                    ]
                }
            }
        ]
    )

    result = service(client).get_costs(
        [vm("i-11111111111111111")], datetime(2026, 8, 29, tzinfo=UTC)
    )

    assert result["i-11111111111111111"].status == "failed"
