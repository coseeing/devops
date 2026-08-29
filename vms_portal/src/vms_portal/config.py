from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


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


def _identifier(env: Mapping[str, str], name: str, default: str, pattern: str) -> str:
    value = env.get(name, default)
    if not re.fullmatch(pattern, value):
        raise ConfigurationError(f"{name} contains unsupported characters")
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
    cost_database: str = "vms_portal_costs"
    cost_table: str = "cur2"
    cost_workgroup: str = "vms-portal-costs"
    assignments_db_path: Path = Path("/data/vms-portal/data/portal.db")

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
        assignments_db_path = Path(
            env.get("ASSIGNMENTS_DB_PATH", "/data/vms-portal/data/portal.db")
        )
        if not assignments_db_path.is_absolute():
            raise ConfigurationError("ASSIGNMENTS_DB_PATH must be an absolute path")
        return cls(
            auth_secret_id=secret_id,
            trusted_proxy_ips=proxy_ips,
            cost_cache_seconds=_positive_int(env, "COST_CACHE_SECONDS", 21_600),
            cost_database=_identifier(
                env, "COST_DATABASE", "vms_portal_costs", r"[a-z0-9_]+"
            ),
            cost_table=_identifier(env, "COST_TABLE", "cur2", r"[a-z0-9_]+"),
            cost_workgroup=_identifier(
                env, "COST_WORKGROUP", "vms-portal-costs", r"[A-Za-z0-9._-]+"
            ),
            assignments_db_path=assignments_db_path,
        )
