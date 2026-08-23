from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _non_negative_decimal(
    env: Mapping[str, str], name: str, default: str
) -> Decimal:
    try:
        value = Decimal(env.get(name, default))
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} must be a non-negative decimal") from exc
    if not value.is_finite() or value < 0:
        raise ConfigurationError(f"{name} must be a non-negative decimal")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    auth_secret_id: str
    aws_region: str = "ap-northeast-1"
    managed_tag_key: str = "VmPortalManaged"
    managed_tag_value: str = "true"
    session_cookie_name: str = "vms_portal_session"
    trusted_proxy_ips: tuple[str, ...] = ("127.0.0.1", "::1")
    cost_cache_seconds: int = 21_600
    public_ipv4_hourly_usd: Decimal = Decimal("0.005")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        secret_id = env.get("AUTH_SECRET_ID", "").strip()
        if not secret_id:
            raise ConfigurationError("AUTH_SECRET_ID is required")
        proxy_ips = tuple(
            entry.strip()
            for entry in env.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
            if entry.strip()
        )
        if not proxy_ips:
            raise ConfigurationError("TRUSTED_PROXY_IPS must contain at least one IP")
        return cls(
            auth_secret_id=secret_id,
            trusted_proxy_ips=proxy_ips,
            cost_cache_seconds=_positive_int(env, "COST_CACHE_SECONDS", 21_600),
            public_ipv4_hourly_usd=_non_negative_decimal(
                env, "PUBLIC_IPV4_HOURLY_USD", "0.005"
            ),
        )
