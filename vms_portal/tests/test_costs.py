from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import boto3
from botocore.stub import Stubber
from vms_portal.costs import CostService


def client():
    return boto3.client(
        "ce",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_session_token="test",
    )


def test_costs_use_trailing_fourteen_days_resource_grouping_and_decimal_sum() -> None:
    ce = client()
    stubber = Stubber(ce)
    expected = {
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
                {"Dimensions": {"Key": "RESOURCE_ID", "Values": ["i-a", "i-b"]}},
            ]
        },
        "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
        "Metrics": ["UnblendedCost"],
    }
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
                                "UnblendedCost": {"Amount": "0.10", "Unit": "USD"}
                            },
                        }
                    ],
                },
                {
                    "TimePeriod": {"Start": "2026-08-07", "End": "2026-08-08"},
                    "Estimated": True,
                    "Groups": [
                        {
                            "Keys": ["i-a"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "0.20", "Unit": "USD"}
                            },
                        },
                        {
                            "Keys": ["i-b"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "1.25", "Unit": "USD"}
                            },
                        },
                    ],
                },
            ]
        },
        expected,
    )

    with stubber:
        costs = CostService(ce).get_costs(
            ["i-b", "i-a"], datetime(2026, 8, 20, 12, tzinfo=UTC)
        )

    assert costs["i-a"].amount == Decimal("0.30")
    assert costs["i-a"].estimated is True
    assert costs["i-b"].amount == Decimal("1.25")


def test_cost_cache_avoids_second_api_request_for_six_hours() -> None:
    ce = client()
    stubber = Stubber(ce)
    stubber.add_response(
        "get_cost_and_usage_with_resources",
        {"ResultsByTime": []},
        {
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
                    {"Dimensions": {"Key": "RESOURCE_ID", "Values": ["i-a"]}},
                ]
            },
            "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            "Metrics": ["UnblendedCost"],
        },
    )
    service = CostService(ce)

    with stubber:
        first = service.get_costs(["i-a"], datetime(2026, 8, 20, 1, tzinfo=UTC))
        second = service.get_costs(["i-a"], datetime(2026, 8, 20, 6, 59, tzinfo=UTC))

    assert first == second
