from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Address

import boto3
from botocore.stub import Stubber
from vms_portal.costs import CostService
from vms_portal.ec2 import VmInstance


def client():
    return boto3.client(
        "ce",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_session_token="test",
    )


def vm(
    instance_id: str,
    *,
    state: str = "running",
    allocation_id: str | None = None,
    eip_created_at: datetime | None = None,
) -> VmInstance:
    return VmInstance(
        instance_id,
        f"windows-{instance_id}",
        IPv4Address("198.51.100.9"),
        "m5.xlarge",
        state,
        datetime(2026, 8, 1, tzinfo=UTC),
        allocation_id,
        eip_created_at,
    )


def expected_request(instance_ids: list[str]) -> dict[str, object]:
    return {
        "TimePeriod": {"Start": "2026-08-06", "End": "2026-08-20"},
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


def test_costs_combine_ec2_actual_and_stopped_vm_eip_estimate() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_response(
        "get_cost_and_usage_with_resources",
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-08-06", "End": "2026-08-07"},
                    "Estimated": False,
                    "Groups": [
                        {
                            "Keys": ["i-a"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "0.30", "Unit": "USD"}
                            },
                        }
                    ],
                }
            ]
        },
        expected_request(["i-a"]),
    )
    instance = vm(
        "i-a",
        state="stopped",
        allocation_id="eipalloc-a",
        eip_created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    with stubber:
        cost = CostService(ce, public_ipv4_hourly_usd=Decimal("0.005")).get_costs(
            [instance], datetime(2026, 8, 20, 12, tzinfo=UTC)
        )["i-a"]

    assert cost.ec2_amount == Decimal("0.30")
    assert cost.eip_amount == Decimal("0.180")
    assert cost.total_amount == Decimal("0.480")


def test_eip_estimate_is_capped_at_fourteen_days() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_response(
        "get_cost_and_usage_with_resources",
        {"ResultsByTime": []},
        expected_request(["i-a"]),
    )
    instance = vm(
        "i-a",
        allocation_id="eipalloc-a",
        eip_created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    with stubber:
        cost = CostService(ce).get_costs(
            [instance], datetime(2026, 8, 20, 12, tzinfo=UTC)
        )["i-a"]

    assert cost.eip_amount == Decimal("1.680")


def test_missing_eip_is_zero_but_missing_eip_timestamp_is_unavailable() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_response(
        "get_cost_and_usage_with_resources",
        {"ResultsByTime": []},
        expected_request(["i-no-eip", "i-no-time"]),
    )
    instances = [
        vm("i-no-eip"),
        vm("i-no-time", allocation_id="eipalloc-no-time"),
    ]

    with stubber:
        costs = CostService(ce).get_costs(
            instances, datetime(2026, 8, 20, 12, tzinfo=UTC)
        )

    assert costs["i-no-eip"].eip_amount == Decimal(0)
    assert costs["i-no-eip"].total_amount == Decimal(0)
    assert costs["i-no-time"].eip_amount is None
    assert costs["i-no-time"].total_amount is None


def test_cost_explorer_failure_keeps_eip_estimate_available() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_client_error(
        "get_cost_and_usage_with_resources",
        service_error_code="AccessDeniedException",
        service_message="denied",
        expected_params=expected_request(["i-a"]),
    )
    instance = vm(
        "i-a",
        allocation_id="eipalloc-a",
        eip_created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    with stubber:
        cost = CostService(ce).get_costs(
            [instance], datetime(2026, 8, 20, 12, tzinfo=UTC)
        )["i-a"]

    assert cost.ec2_amount is None
    assert cost.eip_amount == Decimal("0.180")
    assert cost.total_amount is None


def test_cost_cache_avoids_second_api_request_for_six_hours() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_response(
        "get_cost_and_usage_with_resources",
        {"ResultsByTime": []},
        expected_request(["i-a"]),
    )
    service = CostService(ce)
    instance = vm("i-a")

    with stubber:
        first = service.get_costs([instance], datetime(2026, 8, 20, 1, tzinfo=UTC))
        second = service.get_costs(
            [instance], datetime(2026, 8, 20, 6, 59, tzinfo=UTC)
        )

    assert first == second
