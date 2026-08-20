from __future__ import annotations

import pytest
from vms_portal.sessions import (
    InvalidSession,
    LoginLimiter,
    SessionManager,
    new_csrf_token,
    validate_csrf,
)


def test_session_rejects_tampering_old_auth_version_and_expiry() -> None:
    manager = SessionManager("k" * 64)
    token = manager.issue("admin", "admin", auth_version=3, now=1_000)

    assert (
        manager.validate(token, current_auth_version=3, now=1_100).username == "admin"
    )
    with pytest.raises(InvalidSession):
        manager.validate(token + "x", current_auth_version=3, now=1_100)
    with pytest.raises(InvalidSession):
        manager.validate(token, current_auth_version=4, now=1_100)
    with pytest.raises(InvalidSession):
        manager.validate(token, current_auth_version=3, now=29_801)


def test_session_idle_timeout_is_thirty_minutes() -> None:
    manager = SessionManager("k" * 64)
    token = manager.issue("user", "user", auth_version=1, now=100)

    with pytest.raises(InvalidSession):
        manager.validate(token, current_auth_version=1, now=1_901)


def test_csrf_is_constant_contract() -> None:
    token = new_csrf_token()

    assert len(token) >= 32
    assert validate_csrf(token, token)
    assert not validate_csrf(token, token + "x")


def test_five_failures_block_source_for_fifteen_minutes() -> None:
    limiter = LoginLimiter()
    for offset in range(5):
        limiter.register_failure("203.0.113.4", now=float(offset))

    assert limiter.is_blocked("203.0.113.4", now=10)
    assert not limiter.is_blocked("203.0.113.4", now=905)
    limiter.register_failure("203.0.113.4", now=906)
    limiter.register_success("203.0.113.4")
    assert not limiter.is_blocked("203.0.113.4", now=907)
