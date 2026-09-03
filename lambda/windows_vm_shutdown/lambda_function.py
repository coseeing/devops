from __future__ import annotations

import json
import logging

import boto3

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
_BATCH_SIZE = 1000


def stop_managed_instances(ec2_client):
    paginator = ec2_client.get_paginator("describe_instances")
    instance_ids = [
        instance["InstanceId"]
        for page in paginator.paginate(
            Filters=[
                {"Name": "tag:VmPortalManaged", "Values": ["true"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        for reservation in page.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    stopped = 0
    for offset in range(0, len(instance_ids), _BATCH_SIZE):
        batch = instance_ids[offset : offset + _BATCH_SIZE]
        try:
            ec2_client.stop_instances(InstanceIds=batch)
        except Exception:
            LOGGER.exception(
                "managed VM shutdown failed matched=%d stopped=%d",
                len(instance_ids),
                stopped,
                extra={"matched": len(instance_ids), "stopped": stopped},
            )
            raise
        stopped += len(batch)
    result = {"matched": len(instance_ids), "stopped": stopped}
    LOGGER.info("managed VM shutdown complete %s", json.dumps(result, sort_keys=True))
    return result


def lambda_handler(event, context):
    del event, context
    return stop_managed_instances(boto3.client("ec2"))
