from __future__ import annotations

import json

from vms_portal.audit import AuditEvent, AuditLogger


def test_audit_serializes_only_allowlisted_fields() -> None:
    emitted: list[str] = []
    logger = AuditLogger(emitted.append)

    logger.emit(
        AuditEvent(
            event="vm.stop.accepted",
            result="accepted",
            request_id="req-1",
            username="admin",
            role="admin",
            source_ip="203.0.113.1",
            instance_id="i-123",
            public_ip="198.51.100.9",
            previous_state="running",
            details={"password": "must-not-log", "category": "accepted"},
        )
    )

    parsed = json.loads(emitted[0])
    assert parsed["event"] == "vm.stop.accepted"
    assert parsed["details"] == {"category": "accepted"}
    assert "must-not-log" not in emitted[0]
