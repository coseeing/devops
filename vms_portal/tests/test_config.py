from __future__ import annotations

from decimal import Decimal

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
    assert settings.public_ipv4_hourly_usd == Decimal("0.005")
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


def test_public_ipv4_hourly_rate_can_be_overridden() -> None:
    settings = Settings.from_env(
        {
            "AUTH_SECRET_ID": "prod/vms-portal/auth",
            "PUBLIC_IPV4_HOURLY_USD": "0.00625",
        }
    )

    assert settings.public_ipv4_hourly_usd == Decimal("0.00625")


@pytest.mark.parametrize("value", ["-0.001", "NaN", "Infinity", "not-a-price"])
def test_public_ipv4_hourly_rate_must_be_a_non_negative_finite_decimal(
    value: str,
) -> None:
    with pytest.raises(ConfigurationError, match="PUBLIC_IPV4_HOURLY_USD"):
        Settings.from_env(
            {
                "AUTH_SECRET_ID": "prod/vms-portal/auth",
                "PUBLIC_IPV4_HOURLY_USD": value,
            }
        )
