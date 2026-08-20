from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class SecretFormatError(ValueError):
    """The configured secret does not satisfy the authentication schema."""


class SecretUnavailable(RuntimeError):
    """No sufficiently fresh authentication secret is available."""


class SecretsClient(Protocol):
    def get_secret_value(self, **kwargs: str) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class CredentialRecord:
    username: str
    role: Literal["admin", "user"]
    password_hash: str


@dataclass(frozen=True, slots=True)
class AuthSnapshot:
    credentials: Mapping[str, CredentialRecord]
    session_key: str
    auth_version: int
    version_id: str


class SecretCache:
    def __init__(
        self,
        client: SecretsClient,
        secret_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        refresh_seconds: float = 300,
        max_stale_seconds: float = 900,
    ) -> None:
        self._client = client
        self._secret_id = secret_id
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._max_stale_seconds = max_stale_seconds
        self._hasher = PasswordHasher()
        self._dummy_hash = self._hasher.hash("vms-portal-unknown-user")
        self._lock = threading.RLock()
        self._snapshot: AuthSnapshot | None = None
        self._next_refresh = 0.0
        self._first_failure: float | None = None

    def load_startup(self) -> AuthSnapshot:
        with self._lock:
            snapshot = self._fetch()
            self._accept(snapshot, self._clock())
            return snapshot

    def snapshot_for_auth(self) -> AuthSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise SecretUnavailable("authentication secret has not loaded")
            now = self._clock()
            if now >= self._next_refresh:
                try:
                    self._accept(self._fetch(), now)
                except Exception as exc:
                    if self._first_failure is None:
                        self._first_failure = now
                    self._next_refresh = now + 60
                    if now - self._first_failure > self._max_stale_seconds:
                        raise SecretUnavailable(
                            "authentication secret is stale"
                        ) from exc
            if (
                self._first_failure is not None
                and now - self._first_failure > self._max_stale_seconds
            ):
                raise SecretUnavailable("authentication secret is stale")
            return self._snapshot

    def verify_password(self, username: str, password: str) -> CredentialRecord | None:
        snapshot = self.snapshot_for_auth()
        record = snapshot.credentials.get(username)
        candidate_hash = record.password_hash if record else self._dummy_hash
        try:
            valid = self._hasher.verify(candidate_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False
        return record if record is not None and valid else None

    def _accept(self, snapshot: AuthSnapshot, now: float) -> None:
        self._snapshot = snapshot
        self._next_refresh = now + self._refresh_seconds
        self._first_failure = None

    def _fetch(self) -> AuthSnapshot:
        response = self._client.get_secret_value(SecretId=self._secret_id)
        raw = response.get("SecretString")
        if not isinstance(raw, str):
            raise SecretFormatError("secret must contain SecretString JSON")
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SecretFormatError("secret must be valid JSON") from exc
        return _parse_snapshot(data, response.get("VersionId", "unknown"))


def _parse_snapshot(data: object, version_id: str) -> AuthSnapshot:
    try:
        if not isinstance(data, dict):
            raise TypeError
        accounts = data["accounts"]
        session_key = data["session_key"]
        auth_version = data["auth_version"]
        if not isinstance(accounts, list) or len(accounts) != 2:
            raise TypeError
        if not isinstance(session_key, str) or len(session_key) < 32:
            raise TypeError
        if (
            not isinstance(auth_version, int)
            or isinstance(auth_version, bool)
            or auth_version < 1
        ):
            raise TypeError
        credentials: dict[str, CredentialRecord] = {}
        roles: set[str] = set()
        for account in accounts:
            if not isinstance(account, dict):
                raise TypeError
            username = account["username"]
            role = account["role"]
            password_hash = account["password_hash"]
            if not isinstance(username, str) or not username or username in credentials:
                raise TypeError
            if role not in {"admin", "user"} or role in roles:
                raise TypeError
            if not isinstance(password_hash, str) or not password_hash.startswith(
                "$argon2id$"
            ):
                raise TypeError
            credentials[username] = CredentialRecord(username, role, password_hash)
            roles.add(role)
        if roles != {"admin", "user"}:
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise SecretFormatError(
            "secret does not match the required authentication schema"
        ) from exc
    return AuthSnapshot(credentials, session_key, auth_version, str(version_id))
