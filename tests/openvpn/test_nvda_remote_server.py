from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible_yaml/roles/nvda_remote_server"
CERT_HELPER = ROOT / "scripts/nvda-remote-cert"
CA_TRUST_TEMPLATE = ROOT / "cloudformation/nvda-remote-ca-trust-template.yml"


def load_defaults() -> dict[str, object]:
    return yaml.safe_load((ROLE / "defaults/main.yml").read_text())


def load_tasks() -> list[dict[str, object]]:
    return yaml.safe_load((ROLE / "tasks/main.yml").read_text())


def named_task(name: str) -> dict[str, object]:
    return next(task for task in load_tasks() if task["name"] == name)


def test_image_is_pinned_by_digest() -> None:
    image = load_defaults()["nvda_remote_image"]
    name, _, digest = image.partition("@sha256:")
    assert name == "jmdaweb/nvda-remote-server:2.5"
    assert len(digest) == 64


def test_default_log_level_never_records_channel_keys() -> None:
    # Upstream level 3 and above log channel passwords.
    assert load_defaults()["nvda_remote_loglevel"] in (0, 1, 2)
    tasks = (ROLE / "tasks/main.yml").read_text()
    assert "nvda_remote_loglevel | int in [0, 1, 2]" in tasks
    assert "nvda_remote_cert_ips | length > 0" in tasks


def test_config_uses_ca_signed_certificate_instead_of_bundled_key() -> None:
    config = (ROLE / "templates/NVDARemoteServer.conf.j2").read_text()
    assert "port={{ nvda_remote_port }}" in config
    assert "keyfile=/data/server.key" in config
    assert "certfile=/data/server.crt" in config

    ensure = named_task("Ensure NVDA Remote private CA and server certificate")
    assert ensure["ansible.builtin.command"]["argv"] == [
        "/usr/local/libexec/nvda-remote-cert",
        "ensure",
        "{{ nvda_remote_cert_ips | join(',') }}",
    ]
    assert ensure["changed_when"] == "'issued' in nvda_remote_cert.stdout"
    assert ensure["notify"] == "Restart NVDA Remote server"


def test_ca_key_lives_outside_the_container_mount() -> None:
    defaults = load_defaults()
    assert not str(defaults["nvda_remote_ca_dir"]).startswith(
        str(defaults["nvda_remote_data_dir"])
    )


def test_container_runs_hardened_on_host_network() -> None:
    unit = (ROLE / "templates/nvda-remote-server.service.j2").read_text()
    for flag in (
        "--network host",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--volume {{ nvda_remote_data_dir }}:/data:ro",
        "--log-driver journald",
    ):
        assert flag in unit
    assert "--publish" not in unit
    assert " -p " not in unit
    assert "Requires=docker.service" in unit
    assert "After=docker.service network-online.target openvpn-course-firewall.service" in unit


def test_daily_renewal_restarts_server_only_after_reissue() -> None:
    service = (ROLE / "templates/nvda-remote-cert-renew.service.j2").read_text()
    timer = (ROLE / "templates/nvda-remote-cert-renew.timer.j2").read_text()
    assert "nvda-remote-cert ensure {{ nvda_remote_cert_ips | join(\",\") }} | grep -qx issued" in service
    assert "systemctl restart {{ nvda_remote_service_name }}" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer


def test_role_waits_until_the_relay_listens() -> None:
    wait = named_task("Wait for NVDA Remote server to listen")
    assert wait["ansible.builtin.wait_for"]["port"] == "{{ nvda_remote_port | int }}"


def test_ca_trust_association_targets_managed_windows_vms() -> None:
    text = CA_TRUST_TEMPLATE.read_text()
    assert "Type: AWS::SSM::Association" in text
    assert "Name: AWS-RunPowerShellScript" in text
    assert "Key: tag:VmPortalManaged" in text
    assert "'Root', 'LocalMachine'" in text
    assert "$subject = 'CN=Coseeing NVDA Remote CA'" in text
    # Only CA certificates with the course subject are ever removed.
    assert "$_.Subject -eq $subject -and $_.Thumbprint -ne $cert.Thumbprint" in text


def openssl3() -> str | None:
    for candidate in (os.environ.get("OPENSSL"), shutil.which("openssl")):
        if not candidate:
            continue
        version = subprocess.run(
            [candidate, "version"], capture_output=True, text=True
        ).stdout
        if version.startswith("OpenSSL 3"):
            return candidate
    return None


@pytest.fixture
def cert_env(tmp_path: Path) -> dict[str, str]:
    binary = openssl3()
    if binary is None:
        pytest.skip("OpenSSL 3 is required")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "openssl").symlink_to(binary)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "NVDA_REMOTE_CA_DIR": str(tmp_path / "ca"),
        "NVDA_REMOTE_DATA_DIR": str(tmp_path / "data"),
        "TMPDIR": str(tmp_path),
    }


def ensure(env: dict[str, str], ips: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CERT_HELPER), "ensure", ips],
        env=env, capture_output=True, text=True, timeout=120,
    )


def openssl(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["openssl", *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_cert_helper_issues_once_and_reissues_on_san_change(cert_env, tmp_path) -> None:
    first = ensure(cert_env, "10.250.0.1,10.0.1.10")
    assert first.returncode == 0, first.stderr
    assert first.stdout == "issued\n"
    ca_before = (tmp_path / "ca/ca.crt").read_bytes()
    assert oct((tmp_path / "ca/ca.key").stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "data/server.key").stat().st_mode & 0o777) == "0o640"

    again = ensure(cert_env, "10.250.0.1,10.0.1.10")
    assert again.returncode == 0, again.stderr
    assert again.stdout == ""

    changed = ensure(cert_env, "10.250.0.1,10.0.1.11")
    assert changed.stdout == "issued\n"
    assert (tmp_path / "ca/ca.crt").read_bytes() == ca_before

    ca = str(tmp_path / "ca/ca.crt")
    server = str(tmp_path / "data/server.crt")
    for ip in ("10.250.0.1", "10.0.1.11"):
        assert openssl(cert_env, "verify", "-CAfile", ca, "-verify_ip", ip, server).returncode == 0
    assert openssl(cert_env, "verify", "-CAfile", ca, "-verify_ip", "10.0.1.10", server).returncode != 0


@pytest.mark.parametrize("san", ["DNS:example.com", "IP:8.8.8.8"])
def test_ca_name_constraints_reject_public_names(cert_env, tmp_path, san) -> None:
    assert ensure(cert_env, "10.250.0.1").returncode == 0
    ca_dir = tmp_path / "ca"
    (tmp_path / "ext.cnf").write_text(f"subjectAltName={san}\n")
    assert openssl(
        cert_env, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=x",
        "-keyout", str(tmp_path / "x.key"), "-out", str(tmp_path / "x.csr"),
    ).returncode == 0
    assert openssl(
        cert_env, "x509", "-req", "-in", str(tmp_path / "x.csr"),
        "-CA", str(ca_dir / "ca.crt"), "-CAkey", str(ca_dir / "ca.key"),
        "-set_serial", "1", "-days", "1",
        "-extfile", str(tmp_path / "ext.cnf"), "-out", str(tmp_path / "x.crt"),
    ).returncode == 0
    result = openssl(cert_env, "verify", "-CAfile", str(ca_dir / "ca.crt"), str(tmp_path / "x.crt"))
    assert result.returncode != 0
    assert "permitted subtree violation" in result.stdout + result.stderr


@pytest.mark.parametrize("args", [[], ["ensure"], ["ensure", "bad"], ["ensure", "10.0.0.256"], ["other", "10.0.0.1"]])
def test_cert_helper_rejects_invalid_arguments(cert_env, tmp_path, args) -> None:
    result = subprocess.run(
        ["bash", str(CERT_HELPER), *args],
        env=cert_env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert not (tmp_path / "data/server.crt").exists()


def test_connection_details_secret_matches_portal_schema() -> None:
    text = CA_TRUST_TEMPLATE.read_text()
    assert "Type: AWS::SecretsManager::Secret" in text
    assert "Name: prod/vms-portal/nvda-remote" in text
    assert (
        '{"ca_der_base64":"${CaCertificateDerBase64}","vpn_host":"${VpnHost}",'
        '"vm_host":"${VmHost}","port":${Port}}'
    ) in text


def test_workflow_publishes_both_relay_addresses() -> None:
    workflow = (ROOT / ".github/workflows/deploy-openvpn.yml").read_text()
    assert "--query 'Reservations[0].Instances[0].PrivateIpAddress'" in workflow
    assert "next(ipaddress.ip_network(sys.argv[1]).hosts())" in workflow
    assert 'VpnHost="$VPN_SERVER_IP"' in workflow
    assert 'VmHost="$PRIVATE_IP"' in workflow


def make_expiring_ca(env: dict[str, str], ca_dir: Path, days: int) -> bytes:
    ca_dir.mkdir()
    assert openssl(
        env, "req", "-x509", "-new", "-newkey", "rsa:2048", "-nodes",
        "-days", str(days), "-subj", "/CN=Coseeing NVDA Remote CA",
        "-keyout", str(ca_dir / "ca.key"), "-out", str(ca_dir / "ca.crt"),
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    ).returncode == 0
    return (ca_dir / "ca.crt").read_bytes()


def test_deploy_rotates_a_ca_within_a_year_of_expiry(cert_env, tmp_path) -> None:
    old_ca = make_expiring_ca(cert_env, tmp_path / "ca", days=100)

    result = ensure({**cert_env, "NVDA_REMOTE_ROTATE_CA": "1"}, "10.250.0.1")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "issued\n"
    new_ca = tmp_path / "ca/ca.crt"
    assert new_ca.read_bytes() != old_ca
    assert (tmp_path / "ca/ca.crt.previous").read_bytes() == old_ca
    assert openssl(cert_env, "x509", "-in", str(new_ca), "-noout", "-checkend", str(9 * 365 * 86400)).returncode == 0
    assert openssl(cert_env, "verify", "-CAfile", str(new_ca), str(tmp_path / "data/server.crt")).returncode == 0


def test_renewal_timer_keeps_an_expiring_ca_and_warns(cert_env, tmp_path) -> None:
    old_ca = make_expiring_ca(cert_env, tmp_path / "ca", days=100)

    result = ensure(cert_env, "10.250.0.1")

    assert result.returncode == 0, result.stderr
    assert "rerun the OpenVPN deploy workflow to rotate it" in result.stderr
    assert (tmp_path / "ca/ca.crt").read_bytes() == old_ca
    assert not (tmp_path / "ca/ca.crt.previous").exists()


def test_deploy_keeps_a_ca_with_more_than_a_year_left(cert_env, tmp_path) -> None:
    old_ca = make_expiring_ca(cert_env, tmp_path / "ca", days=400)

    result = ensure({**cert_env, "NVDA_REMOTE_ROTATE_CA": "1"}, "10.250.0.1")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert (tmp_path / "ca/ca.crt").read_bytes() == old_ca


def test_only_the_deploy_play_may_rotate_the_ca() -> None:
    ensure_task = named_task("Ensure NVDA Remote private CA and server certificate")
    assert ensure_task["environment"]["NVDA_REMOTE_ROTATE_CA"] == "1"
    timer_service = (ROLE / "templates/nvda-remote-cert-renew.service.j2").read_text()
    assert "NVDA_REMOTE_ROTATE_CA" not in timer_service
    workflow = (ROOT / ".github/workflows/deploy-openvpn.yml").read_text()
    assert "openssl x509 -in /etc/nvda-remote-server/ca/ca.crt -noout -checkend 31536000" in workflow
