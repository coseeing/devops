from __future__ import annotations

import json

import pytest
from argon2 import PasswordHasher
from vms_portal.secrets import SecretCache, SecretFormatError, SecretUnavailable


class FakeSecretsClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.error: Exception | None = None

    def get_secret_value(self, **kwargs: str) -> dict[str, str]:
        self.calls += 1
        if self.error:
            raise self.error
        return {"SecretString": json.dumps(self.payload), "VersionId": f"v{self.calls}"}


def payload(password: str = "correct horse", version: int = 1) -> dict[str, object]:
    hasher = PasswordHasher()
    return {
        "accounts": [
            {
                "username": "admin",
                "role": "admin",
                "password_hash": hasher.hash(password),
            },
            {
                "username": "user",
                "role": "user",
                "password_hash": hasher.hash("user password"),
            },
        ],
        "session_key": "x" * 64,
        "auth_version": version,
    }


def test_startup_loads_both_roles_and_verifies_argon2_password() -> None:
    now = [0.0]
    cache = SecretCache(FakeSecretsClient(payload()), "prod/vms", clock=lambda: now[0])

    snapshot = cache.load_startup()

    assert {record.role for record in snapshot.credentials.values()} == {
        "admin",
        "user",
    }
    assert cache.verify_password("admin", "correct horse").role == "admin"
    assert cache.verify_password("admin", "wrong") is None


def test_invalid_secret_never_includes_secret_value_in_error() -> None:
    bad = payload()
    bad["session_key"] = "visible-secret-value"
    bad["accounts"] = []
    cache = SecretCache(FakeSecretsClient(bad), "prod/vms")

    with pytest.raises(SecretFormatError) as error:
        cache.load_startup()

    assert "visible-secret-value" not in str(error.value)


def test_refreshes_after_five_minutes_and_observes_auth_version() -> None:
    now = [0.0]
    client = FakeSecretsClient(payload(version=1))
    cache = SecretCache(client, "prod/vms", clock=lambda: now[0])
    cache.load_startup()
    client.payload = payload(version=2)

    now[0] = 299.0
    assert cache.snapshot_for_auth().auth_version == 1
    now[0] = 300.0
    assert cache.snapshot_for_auth().auth_version == 2
    assert client.calls == 2


def test_refresh_failure_fails_closed_after_fifteen_minutes() -> None:
    now = [0.0]
    client = FakeSecretsClient(payload())
    cache = SecretCache(client, "prod/vms", clock=lambda: now[0])
    cache.load_startup()
    client.error = RuntimeError("network contains no secret")

    now[0] = 300.0
    assert cache.snapshot_for_auth().auth_version == 1
    now[0] = 1_201.0
    with pytest.raises(SecretUnavailable):
        cache.snapshot_for_auth()
