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


def run_course(
    env: dict[str, str], *args: str, input_text: str = "", cwd: Path | None = None
):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        cwd=cwd,
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


def test_export_force_replaces_an_existing_regular_file(course_env, tmp_path) -> None:
    destination = tmp_path / "course.ovpn"
    destination.write_text("old profile")

    result = run_course(course_env, "export", str(destination), "--force")

    assert result.returncode == 0
    assert destination.read_text() == "client\n<key>secret</key>\n"
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["directory", "symlink", "dangling_symlink"])
def test_export_rejects_non_regular_destinations_even_with_force(
    course_env, tmp_path, kind
) -> None:
    destination = tmp_path / "course.ovpn"
    if kind == "directory":
        destination.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.ovpn"
        target.write_text("target profile")
        destination.symlink_to(target)
    else:
        destination.symlink_to(tmp_path / "missing.ovpn")

    result = run_course(course_env, "export", str(destination), "--force")

    assert result.returncode != 0
    assert "regular file" in result.stderr


@pytest.mark.parametrize(
    "args",
    [(), ("",), ("--force",), ("course.ovpn", "--invalid"), ("course.ovpn", "--force", "extra")],
)
def test_export_rejects_invalid_argument_shapes(course_env, tmp_path, args) -> None:
    result = run_course(course_env, "export", *args, cwd=tmp_path)

    assert result.returncode != 0
    assert "usage: openvpn-course export DEST [--force]" in result.stderr


def test_export_handles_a_leading_dash_destination_safely(course_env, tmp_path) -> None:
    result = run_course(course_env, "export", "-course.ovpn", cwd=tmp_path)

    destination = tmp_path / "-course.ovpn"
    assert result.returncode == 0
    assert destination.read_text() == "client\n<key>secret</key>\n"
    assert destination.stat().st_mode & 0o777 == 0o600


def test_export_chowns_a_temporary_file_before_publication(course_env, tmp_path) -> None:
    destination = tmp_path / "course.ovpn"
    destination.write_text("old profile")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    chown_log = tmp_path / "chown.log"
    chown = mock_bin / "chown"
    chown.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CHOWN_LOG\"\n")
    chown.chmod(0o755)
    env = {
        **course_env,
        "PATH": f"{mock_bin}:{course_env['PATH']}",
        "SUDO_UID": "123",
        "SUDO_GID": "456",
        "CHOWN_LOG": str(chown_log),
    }

    result = run_course(env, "export", str(destination), "--force")

    chown_arguments = chown_log.read_text().splitlines()
    assert result.returncode == 0
    assert chown_arguments[:2] == ["123:456", "--"]
    assert chown_arguments[-1] != str(destination)
    assert Path(chown_arguments[-1]).name.startswith(".openvpn-course.")
    assert destination.read_text() == "client\n<key>secret</key>\n"


def test_export_does_not_follow_a_destination_substituted_before_publication(
    course_env, tmp_path
) -> None:
    destination = tmp_path / "course.ovpn"
    destination.write_text("old profile")
    protected_target = tmp_path / "protected.ovpn"
    protected_target.write_text("protected profile")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    chown = mock_bin / "chown"
    chown.write_text(
        "#!/bin/sh\n"
        "rm -f -- \"$DESTINATION\"\n"
        "ln -s \"$PROTECTED_TARGET\" \"$DESTINATION\"\n"
    )
    chown.chmod(0o755)
    env = {
        **course_env,
        "PATH": f"{mock_bin}:{course_env['PATH']}",
        "SUDO_UID": "123",
        "SUDO_GID": "456",
        "DESTINATION": str(destination),
        "PROTECTED_TARGET": str(protected_target),
    }

    result = run_course(env, "export", str(destination), "--force")

    assert result.returncode != 0
    assert destination.is_symlink()
    assert protected_target.read_text() == "protected profile"
    assert not list(tmp_path.glob(".openvpn-course.*"))


def test_logs_delegates_to_course_systemd_unit(course_env, tmp_path) -> None:
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    journalctl = mock_bin / "journalctl"
    journalctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n")
    journalctl.chmod(0o755)
    course_env["PATH"] = f"{mock_bin}:{course_env['PATH']}"
    result = run_course(course_env, "logs")
    assert "-u openvpn-server@course --no-pager -n 100" in result.stdout
