from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Protocol

# Must match the subject issued by scripts/nvda-remote-cert.
CA_SUBJECT = "CN=Coseeing NVDA Remote CA"
INSTALLER_FILENAME = "nvda-remote-ca-install.cmd"

_log = logging.getLogger(__name__)


class NvdaRemoteFormatError(ValueError):
    """The NVDA Remote secret does not match the expected schema."""


class SecretsClient(Protocol):
    def get_secret_value(self, **kwargs: str) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class NvdaRemoteInfo:
    vpn_host: IPv4Address
    vm_host: IPv4Address
    port: int
    ca_der: bytes

    @property
    def ca_sha256(self) -> str:
        digest = hashlib.sha256(self.ca_der).hexdigest().upper()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))

    def installer_cmd(self) -> str:
        """A double-click installer that trusts the CA for the current user.

        CurrentUser\\Root needs no administrator rights; Windows asks the user
        to confirm once. Older course CAs with the same subject are replaced.
        """
        der_base64 = base64.b64encode(self.ca_der).decode("ascii")
        script = (
            "$ErrorActionPreference='Stop'; "
            f"$subject='{CA_SUBJECT}'; "
            "$c=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 "
            f"(,[Convert]::FromBase64String('{der_base64}')); "
            "if ($c.Subject -ne $subject) { throw 'Unexpected certificate subject' }; "
            "$s=New-Object System.Security.Cryptography.X509Certificates.X509Store 'Root','CurrentUser'; "
            "$s.Open('ReadWrite'); "
            "try { "
            "foreach ($o in @($s.Certificates | Where-Object { $_.Subject -eq $subject -and $_.Thumbprint -ne $c.Thumbprint })) { $s.Remove($o) }; "
            "if (-not ($s.Certificates | Where-Object { $_.Thumbprint -eq $c.Thumbprint })) { $s.Add($c) } "
            "} finally { $s.Close() }; "
            "Write-Host ('NVDA Remote CA installed. SHA-1 thumbprint: ' + $c.Thumbprint)"
        )
        lines = [
            "@echo off",
            "rem Trust the course NVDA Remote CA for the current Windows user.",
            f"rem CA SHA-256 fingerprint: {self.ca_sha256}",
            f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{script}"',
            "if errorlevel 1 echo NVDA Remote CA installation failed.",
            "pause",
        ]
        return "\r\n".join(lines) + "\r\n"


def parse_info(raw: str) -> NvdaRemoteInfo:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError
        port = data["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise TypeError
        der_base64 = data["ca_der_base64"]
        if not isinstance(der_base64, str) or not 100 <= len(der_base64) <= 4096:
            raise TypeError
        ca_der = base64.b64decode(der_base64, validate=True)
        if not ca_der.startswith(b"\x30"):
            raise TypeError
        return NvdaRemoteInfo(
            IPv4Address(data["vpn_host"]),
            IPv4Address(data["vm_host"]),
            port,
            ca_der,
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise NvdaRemoteFormatError(
            "NVDA Remote secret does not match the required schema"
        ) from exc


class NvdaRemoteSource:
    """Reads NVDA Remote connection details; failures only hide the feature."""

    def __init__(
        self,
        client: SecretsClient | None,
        secret_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        refresh_seconds: float = 300,
    ) -> None:
        self._client = client
        self._secret_id = secret_id
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._info: NvdaRemoteInfo | None = None
        self._next_refresh = 0.0

    def get(self) -> NvdaRemoteInfo | None:
        if self._client is None or not self._secret_id:
            return None
        with self._lock:
            now = self._clock()
            if now >= self._next_refresh:
                self._next_refresh = now + self._refresh_seconds
                try:
                    response = self._client.get_secret_value(SecretId=self._secret_id)
                    self._info = parse_info(str(response.get("SecretString", "")))
                except Exception:  # noqa: BLE001 - keep the portal usable
                    _log.warning("NVDA Remote details unavailable", exc_info=True)
            return self._info
