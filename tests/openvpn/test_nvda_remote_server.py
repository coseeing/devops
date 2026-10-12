from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible_yaml/roles/nvda_remote_server"


def load_defaults() -> dict[str, object]:
    return yaml.safe_load((ROLE / "defaults/main.yml").read_text())


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


def test_config_uses_generated_certificate_instead_of_bundled_key() -> None:
    config = (ROLE / "templates/NVDARemoteServer.conf.j2").read_text()
    assert "port={{ nvda_remote_port }}" in config
    assert "keyfile=/data/server.key" in config
    assert "certfile=/data/server.crt" in config

    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    cert = next(t for t in tasks if t["name"] == "Generate NVDA Remote TLS certificate once")
    assert cert["ansible.builtin.command"]["creates"].endswith("/server.crt")


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


def test_role_waits_until_the_relay_listens() -> None:
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    wait = next(t for t in tasks if t["name"] == "Wait for NVDA Remote server to listen")
    assert wait["ansible.builtin.wait_for"]["port"] == "{{ nvda_remote_port | int }}"
