from __future__ import annotations

import pytest
from vms_portal.config import ConfigurationError, Settings


def test_missing_secret_id_prevents_startup() -> None:
    with pytest.raises(ConfigurationError, match="AUTH_SECRET_ID"):
        Settings.from_env({})


def test_defaults_are_safe_and_region_is_fixed() -> None:
    settings = Settings.from_env({"AUTH_SECRET_ID": "prod/vms-portal/auth"})

    assert settings.aws_region == "ap-northeast-1"
    assert settings.managed_tag_key == "VmPortalManaged"
    assert settings.managed_tag_value == "true"
    assert settings.session_cookie_name == "vms_portal_session"
    assert settings.cost_cache_seconds == 21_600
    assert str(settings.assignments_db_path) == "/data/vms-portal/data/portal.db"
    assert settings.trusted_proxy_ips == ("127.0.0.1", "::1")


def test_invalid_numeric_setting_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="COST_CACHE_SECONDS"):
        Settings.from_env(
            {
                "AUTH_SECRET_ID": "prod/vms-portal/auth",
                "COST_CACHE_SECONDS": "six-hours",
            }
        )


def test_proxy_ips_are_trimmed_and_empty_entries_removed() -> None:
    settings = Settings.from_env(
        {
            "AUTH_SECRET_ID": "prod/vms-portal/auth",
            "TRUSTED_PROXY_IPS": "127.0.0.1, 172.18.0.2, ,::1",
        }
    )

    assert settings.trusted_proxy_ips == ("127.0.0.1", "172.18.0.2", "::1")


def test_assignments_database_path_can_be_overridden() -> None:
    settings = Settings.from_env(
        {
            "AUTH_SECRET_ID": "prod/vms-portal/auth",
            "ASSIGNMENTS_DB_PATH": "/tmp/portal.db",
        }
    )

    assert str(settings.assignments_db_path) == "/tmp/portal.db"


def test_assignments_database_path_must_be_absolute() -> None:
    with pytest.raises(ConfigurationError, match="ASSIGNMENTS_DB_PATH"):
        Settings.from_env(
            {
                "AUTH_SECRET_ID": "prod/vms-portal/auth",
                "ASSIGNMENTS_DB_PATH": "relative/portal.db",
            }
        )
