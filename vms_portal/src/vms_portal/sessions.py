from __future__ import annotations

import hmac
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass

from itsdangerous import BadData, URLSafeSerializer


class InvalidSession(ValueError):
    """The session token is invalid, stale, or expired."""


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    username: str
    role: str
    auth_version: int
    issued_at: float
    last_seen: float
    csrf_token: str


class SessionManager:
    def __init__(
        self,
        signing_key: str,
        *,
        idle_seconds: float = 1_800,
        absolute_seconds: float = 28_800,
    ) -> None:
        self._serializer = URLSafeSerializer(signing_key, salt="vms-portal-session-v1")
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds

    def issue(self, username: str, role: str, auth_version: int, now: float) -> str:
        return self._serializer.dumps(
            {
                "u": username,
                "r": role,
                "v": auth_version,
                "iat": now,
                "seen": now,
                "csrf": new_csrf_token(),
            }
        )

    def validate(
        self, token: str, current_auth_version: int, now: float
    ) -> SessionIdentity:
        try:
            payload = self._serializer.loads(token)
            identity = SessionIdentity(
                username=str(payload["u"]),
                role=str(payload["r"]),
                auth_version=int(payload["v"]),
                issued_at=float(payload["iat"]),
                last_seen=float(payload["seen"]),
                csrf_token=str(payload["csrf"]),
            )
        except (BadData, KeyError, TypeError, ValueError) as exc:
            raise InvalidSession("invalid session") from exc
        if identity.auth_version != current_auth_version:
            raise InvalidSession("session version is stale")
        if now - identity.last_seen > self._idle_seconds:
            raise InvalidSession("session idle timeout")
        if now - identity.issued_at > self._absolute_seconds:
            raise InvalidSession("session absolute timeout")
        if identity.role not in {"admin", "user"}:
            raise InvalidSession("invalid role")
        return identity

    def refresh(self, identity: SessionIdentity, now: float) -> str:
        return self._serializer.dumps(
            {
                "u": identity.username,
                "r": identity.role,
                "v": identity.auth_version,
                "iat": identity.issued_at,
                "seen": now,
                "csrf": identity.csrf_token,
            }
        )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def validate_csrf(expected: str, supplied: str) -> bool:
    return bool(expected and supplied) and hmac.compare_digest(expected, supplied)


class LoginLimiter:
    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: float = 600,
        block_seconds: float = 900,
        max_sources: int = 10_000,
    ) -> None:
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._block_seconds = block_seconds
        self._max_sources = max_sources
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def register_failure(self, source_ip: str, now: float) -> None:
        self._prune(source_ip, now)
        failures = self._failures[source_ip]
        failures.append(now)
        if len(failures) >= self._max_failures:
            self._blocked_until[source_ip] = now + self._block_seconds
        if len(self._failures) > self._max_sources:
            oldest = next(iter(self._failures))
            self._failures.pop(oldest, None)
            self._blocked_until.pop(oldest, None)

    def register_success(self, source_ip: str) -> None:
        self._failures.pop(source_ip, None)
        self._blocked_until.pop(source_ip, None)

    def is_blocked(self, source_ip: str, now: float) -> bool:
        until = self._blocked_until.get(source_ip, 0)
        if until <= now:
            self._blocked_until.pop(source_ip, None)
            self._prune(source_ip, now)
            return False
        return True

    def _prune(self, source_ip: str, now: float) -> None:
        failures = self._failures.get(source_ip)
        if failures is None:
            return
        cutoff = now - self._window_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(source_ip, None)
