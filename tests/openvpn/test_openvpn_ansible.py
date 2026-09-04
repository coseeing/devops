from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible_yaml/roles/openvpn_server"
RENDERER = ROOT / "scripts/openvpn-course-render-profile"


def test_playbook_uses_only_openvpn_role() -> None:
    playbook = yaml.safe_load(
        (ROOT / "ansible_yaml/openvpn-server-playbook.yml").read_text()
    )
    assert playbook[0]["become"] is True
    assert playbook[0]["roles"] == ["openvpn_server"]
    rendered = str(playbook)
    assert "common/pre-common.yml" not in rendered
    assert "common/post-common.yml" not in rendered


def test_server_configuration_is_split_tunnel_and_secure() -> None:
    config = (ROLE / "templates/course.conf.j2").read_text()
    assert (
        'push "route {{ openvpn_windows_cidr | cidr_network }} '
        '{{ openvpn_windows_cidr | cidr_netmask }}"'
    ) in config
    assert "duplicate-cn" in config
    assert "tls-crypt" in config
    assert "tls-version-min 1.2" in config
    assert "data-ciphers AES-256-GCM:AES-128-GCM" in config
    assert "allow-compression no" in config
    assert "redirect-gateway" not in config
    assert "dhcp-option" not in config
    assert "client-to-client" not in config


def test_client_profile_embeds_keys_and_verifies_server_purpose() -> None:
    profile = (ROLE / "templates/course.ovpn.j2").read_text()
    for block in ("<ca>", "<cert>", "<key>", "<tls-crypt>"):
        assert block in profile
    assert "remote-cert-tls server" in profile
    assert "auth-user-pass" not in profile


def test_role_preserves_active_cn_and_installs_profile_renderer() -> None:
    tasks = (ROLE / "tasks/main.yml").read_text()
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert "slurp:" in tasks
    assert defaults["openvpn_active_client_cn_file"].endswith("/active-client-cn")
    assert "openvpn_active_client_cn_stat.stat.exists" in tasks
    assert "scripts/openvpn-course-render-profile" in tasks
    assert 'dest: "{{ openvpn_pki_dir }}/render-profile"' in tasks
    assert 'SERVER_CRL_PATH=/etc/openvpn/server/crl.pem' in (
        ROLE / "templates/openvpn-course.env.j2"
    ).read_text()
    assert 'LAST_SHARED_KEY_FILE=/var/lib/openvpn-course/last-shared-key' in (
        ROLE / "templates/openvpn-course.env.j2"
    ).read_text()


def write_renderer_fixture(tmp_path: Path) -> dict[str, str]:
    pki = tmp_path / "pki"
    (pki / "pki/issued").mkdir(parents=True)
    (pki / "pki/private").mkdir()
    (pki / "pki/ca.crt").write_text("CA CERT\n")
    (pki / "pki/issued/course-shared.crt").write_text("CLIENT CERT\n")
    (pki / "pki/private/course-shared.key").write_text("CLIENT KEY\n")
    (pki / "tls-crypt.key").write_text("TLS CRYPT KEY\n")
    return {
        **os.environ,
        "PKI_DIR": str(pki),
        "ENDPOINT": "vpn.example.org",
        "OPENVPN_PORT": "443",
        "OPENVPN_PROTOCOL": "tcp",
    }


def test_profile_renderer_embeds_requested_client_material(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(RENDERER), "course-shared"],
        env=write_renderer_fixture(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "remote vpn.example.org 443" in result.stdout
    assert "proto tcp" in result.stdout
    for expected in ("CA CERT", "CLIENT CERT", "CLIENT KEY", "TLS CRYPT KEY"):
        assert expected in result.stdout
    assert "CLIENT KEY" not in result.stderr


def test_profile_renderer_rejects_unsafe_client_cn(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(RENDERER), "../course-shared"],
        env=write_renderer_fixture(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid client CN" in result.stderr
    assert result.stdout == ""
