from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

_DETAIL_KEYS = frozenset({"assignee", "category", "error_code", "aws_request_id"})


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event: str
    result: str
    request_id: str
    username: str | None = None
    role: str | None = None
    source_ip: str | None = None
    instance_id: str | None = None
    public_ip: str | None = None
    previous_state: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)


class AuditLogger:
    def __init__(self, sink: Callable[[str], object]) -> None:
        self._sink = sink

    def emit(self, event: AuditEvent) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event.event,
            "result": event.result,
            "request_id": event.request_id,
            "username": event.username,
            "role": event.role,
            "source_ip": event.source_ip,
            "instance_id": event.instance_id,
            "public_ip": event.public_ip,
            "previous_state": event.previous_state,
            "details": {
                key: value
                for key, value in event.details.items()
                if key in _DETAIL_KEYS
            },
        }
        self._sink(json.dumps(record, separators=(",", ":"), sort_keys=True))
