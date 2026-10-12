from __future__ import annotations

import base64
import hashlib
import json

import pytest
from vms_portal.nvda_remote import (
    CA_SUBJECT,
    NvdaRemoteFormatError,
    NvdaRemoteSource,
    parse_info,
)

CA_DER = b"\x30\x82" + bytes(range(256)) * 2


def secret(**overrides) -> str:
    data = {
        "ca_der_base64": base64.b64encode(CA_DER).decode(),
        "vpn_host": "10.250.0.1",
        "vm_host": "10.0.1.10",
        "port": 6837,
    }
    data.update(overrides)
    return json.dumps(data)


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return {"SecretString": response}


def test_parse_info_reads_hosts_port_and_fingerprint() -> None:
    info = parse_info(secret())
    assert str(info.vpn_host) == "10.250.0.1"
    assert str(info.vm_host) == "10.0.1.10"
    assert info.port == 6837
    assert info.ca_der == CA_DER
    expected = hashlib.sha256(CA_DER).hexdigest().upper()
    assert info.ca_sha256.replace(":", "") == expected
    assert info.ca_sha256.count(":") == 31


@pytest.mark.parametrize(
    "overrides",
    [
        {"port": 80},
        {"port": "6837"},
        {"port": True},
        {"vpn_host": "vpn.example.com"},
        {"vm_host": "10.0.1.300"},
        {"ca_der_base64": "not base64!" * 20},
        {"ca_der_base64": base64.b64encode(b"\x31" * 200).decode()},
        {"ca_der_base64": "MA=="},
    ],
)
def test_parse_info_rejects_invalid_secrets(overrides) -> None:
    with pytest.raises(NvdaRemoteFormatError):
        parse_info(secret(**overrides))


def test_parse_info_rejects_non_object_json() -> None:
    with pytest.raises(NvdaRemoteFormatError):
        parse_info("[]")


def test_installer_is_ascii_crlf_and_embeds_only_the_ca() -> None:
    script = parse_info(secret()).installer_cmd()
    script.encode("ascii")
    assert script.startswith("@echo off\r\n")
    assert script.endswith("pause\r\n")
    assert "\n" not in script.replace("\r\n", "")
    assert base64.b64encode(CA_DER).decode() in script
    assert f"$subject='{CA_SUBJECT}'" in script
    assert "'Root','CurrentUser'" in script
    assert "LocalMachine" not in script
    # cmd.exe metacharacters outside the quoted PowerShell command would break it.
    command = script.split('-Command "', 1)[1].split('"\r\n', 1)[0]
    assert '"' not in command
    assert "%" not in command


def test_source_is_disabled_without_secret_id() -> None:
    client = FakeClient([])
    assert NvdaRemoteSource(client, "").get() is None
    assert NvdaRemoteSource(None, "prod/vms-portal/nvda-remote").get() is None
    assert client.calls == []


def test_source_caches_and_keeps_last_good_value_on_failure() -> None:
    now = [0.0]
    client = FakeClient([secret(), RuntimeError("throttled"), secret(vm_host="10.0.1.11")])
    source = NvdaRemoteSource(
        client, "prod/vms-portal/nvda-remote", clock=lambda: now[0], refresh_seconds=300
    )

    assert str(source.get().vm_host) == "10.0.1.10"
    assert str(source.get().vm_host) == "10.0.1.10"
    assert len(client.calls) == 1

    now[0] = 301
    assert str(source.get().vm_host) == "10.0.1.10"
    now[0] = 602
    assert str(source.get().vm_host) == "10.0.1.11"
    assert client.calls[0] == {"SecretId": "prod/vms-portal/nvda-remote"}


def test_source_returns_none_when_secret_was_never_readable() -> None:
    source = NvdaRemoteSource(FakeClient([RuntimeError("missing")]), "prod/vms-portal/nvda-remote")
    assert source.get() is None
