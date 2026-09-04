from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/openvpn-course"


@pytest.fixture
def course_env(tmp_path: Path) -> dict[str, str]:
    profile = tmp_path / "current.ovpn"
    profile.write_text("client\n<key>secret</key>\n")
    status = tmp_path / "course.status"
    status.write_text("CLIENT_LIST,course-shared,198.51.100.8:5000\n")
    env_file = tmp_path / "course.env"
    env_file.write_text(
        f"PROFILE_PATH={profile}\nPKI_DIR={tmp_path}/pki\n"
        f"ACTIVE_CLIENT_CN_FILE={tmp_path}/active-client-cn\n"
        f"STATUS_PATH={status}\nENDPOINT=vpn.coseeing.org\n"
        "VPN_CIDR=10.250.0.0/24\nWINDOWS_CIDR=10.0.8.0/24\n"
        "PROFILE_BUCKET=test-bucket\nAWS_REGION=ap-northeast-1\n"
    )
    (tmp_path / "active-client-cn").write_text("course-shared\n")
    return {"OPENVPN_COURSE_ENV_FILE": str(env_file), "PATH": os.environ["PATH"]}


def run_course(env: dict[str, str], *args: str, input_text: str = ""):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def test_status_reports_metadata_without_profile_secret(course_env) -> None:
    result = run_course(course_env, "status")
    assert result.returncode == 0
    assert "vpn.coseeing.org" in result.stdout
    assert "10.0.8.0/24" in result.stdout
    assert "Connected clients: 1" in result.stdout
    assert "secret" not in result.stdout


def test_export_writes_mode_0600_and_refuses_overwrite(course_env, tmp_path) -> None:
    destination = tmp_path / "course.ovpn"
    first = run_course(course_env, "export", str(destination))
    assert first.returncode == 0
    assert destination.stat().st_mode & 0o777 == 0o600
    second = run_course(course_env, "export", str(destination))
    assert second.returncode != 0
    assert "already exists" in second.stderr


def test_logs_delegates_to_course_systemd_unit(course_env, tmp_path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    journalctl = mock_bin / "journalctl"
    journalctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n")
    journalctl.chmod(0o755)
    course_env["PATH"] = f"{mock_bin}:{course_env['PATH']}"
    result = run_course(course_env, "logs")
    assert "-u openvpn-server@course --no-pager -n 100" in result.stdout
