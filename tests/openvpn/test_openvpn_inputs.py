from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/validate-openvpn-inputs.py"
FILTERS = ROOT / "ansible_yaml/filter_plugins/openvpn_filters.py"


def load_filters():
    spec = importlib.util.spec_from_file_location("openvpn_filters", FILTERS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def valid_args() -> tuple[str, ...]:
    return (
        "--endpoint",
        "vpn.coseeing.org",
        "--vpn-cidr",
        "10.250.0.0/24",
        "--windows-cidr",
        "10.0.8.0/24",
        "--vpc-cidr",
        "10.0.0.0/16",
        "--client-days",
        "30",
    )


def test_filters_return_canonical_network_and_netmask() -> None:
    module = load_filters()
    assert module.cidr_network("10.0.8.21/24") == "10.0.8.0"
    assert module.cidr_netmask("10.0.8.21/24") == "255.255.255.0"


def test_validator_emits_normalized_json() -> None:
    result = run_validator(*valid_args())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "client_days": 30,
        "endpoint": "vpn.coseeing.org",
        "vpn_cidr": "10.250.0.0/24",
        "vpc_cidr": "10.0.0.0/16",
        "windows_cidr": "10.0.8.0/24",
        "windows_netmask": "255.255.255.0",
        "windows_network": "10.0.8.0",
    }


def test_validator_rejects_vpn_overlap_with_vpc() -> None:
    args = list(valid_args())
    args[args.index("10.250.0.0/24")] = "10.0.8.0/24"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: VPN CIDR overlaps VPC CIDR\n"


def test_validator_rejects_vpn_overlap_with_windows() -> None:
    args = list(valid_args())
    args[args.index("10.250.0.0/24")] = "10.0.8.0/25"
    result = run_validator(*args)
    assert result.returncode == 2
    # Windows is required to be inside VPC, so this overlap is also a VPC overlap.
    assert result.stderr == "error: VPN CIDR overlaps VPC CIDR\n"


def test_validator_rejects_public_vpn_cidr() -> None:
    args = list(valid_args())
    args[args.index("10.250.0.0/24")] = "198.51.100.0/24"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: VPN CIDR must be a private IPv4 network\n"


def test_validator_rejects_vpn_prefix_shorter_than_16() -> None:
    args = list(valid_args())
    args[args.index("10.250.0.0/24")] = "10.0.0.0/15"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: VPN CIDR prefix length must be between /16 and /29\n"


def test_validator_rejects_vpn_prefix_longer_than_29() -> None:
    args = list(valid_args())
    args[args.index("10.250.0.0/24")] = "10.250.0.0/30"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: VPN CIDR prefix length must be between /16 and /29\n"


def test_validator_rejects_url_or_port_as_endpoint() -> None:
    args = list(valid_args())
    args[args.index("vpn.coseeing.org")] = "https://vpn.coseeing.org:1194"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: endpoint must be a hostname or IPv4 address without a port\n"


def test_validator_limits_client_lifetime() -> None:
    args = list(valid_args())
    args[args.index("30")] = "366"
    result = run_validator(*args)
    assert result.returncode == 2
    assert result.stderr == "error: client-days must be between 1 and 365\n"
