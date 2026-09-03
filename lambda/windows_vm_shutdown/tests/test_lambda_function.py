from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "lambda_function.py"
SPEC = importlib.util.spec_from_file_location("windows_vm_shutdown", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages


class FakeEc2:
    def __init__(self, pages):
        self.paginator = FakePaginator(pages)
        self.stop_calls = []
        self.error = None

    def get_paginator(self, name):
        assert name == "describe_instances"
        return self.paginator

    def stop_instances(self, **kwargs):
        self.stop_calls.append(kwargs)
        if self.error:
            raise self.error


def page(*instance_ids):
    return {
        "Reservations": [
            {"Instances": [{"InstanceId": instance_id} for instance_id in instance_ids]}
        ]
    }


def test_no_running_managed_instances_is_success() -> None:
    ec2 = FakeEc2([])

    result = module.stop_managed_instances(ec2)

    assert result == {"matched": 0, "stopped": 0}
    assert ec2.stop_calls == []
    assert ec2.paginator.calls == [
        {
            "Filters": [
                {"Name": "tag:VmPortalManaged", "Values": ["true"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        }
    ]


def test_stops_all_instances_in_bounded_batches() -> None:
    instance_ids = [f"i-{index:017x}" for index in range(1001)]
    ec2 = FakeEc2([page(*instance_ids)])

    result = module.stop_managed_instances(ec2)

    assert result == {"matched": 1001, "stopped": 1001}
    assert [len(call["InstanceIds"]) for call in ec2.stop_calls] == [1000, 1]


def test_stop_failure_is_logged_and_propagated(caplog) -> None:
    ec2 = FakeEc2([page("i-1234567890abcdef0")])
    ec2.error = RuntimeError("stop failed")

    with pytest.raises(RuntimeError, match="stop failed"):
        module.stop_managed_instances(ec2)

    assert "matched=1 stopped=0" in caplog.text
