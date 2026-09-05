from __future__ import annotations

import fcntl
import os
import re
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
    server_crl = tmp_path / "server-crl.pem"
    server_crl.write_text("old server crl\n")
    last_shared_key = tmp_path / "last-shared-key"
    env_file = tmp_path / "course.env"
    env_file.write_text(
        f"PROFILE_PATH={profile}\nPKI_DIR={tmp_path}/pki\n"
        f"ACTIVE_CLIENT_CN_FILE={tmp_path}/active-client-cn\n"
        f"SERVER_CRL_PATH={server_crl}\n"
        f"LAST_SHARED_KEY_FILE={last_shared_key}\n"
        f"STATUS_PATH={status}\nENDPOINT=vpn.coseeing.org\n"
        "VPN_CIDR=10.250.0.0/24\nWINDOWS_CIDR=10.0.8.0/24\n"
        "PROFILE_BUCKET=test-bucket\nAWS_REGION=ap-northeast-1\n"
        f"MUTATION_LOCK_PATH={tmp_path}/openvpn-course.lock\n"
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


def configured_path(env: dict[str, str], name: str) -> Path:
    for line in Path(env["OPENVPN_COURSE_ENV_FILE"]).read_text().splitlines():
        key, _, value = line.partition("=")
        if key == name:
            return Path(value)
    raise AssertionError(f"{name} is not configured")


@pytest.fixture
def mock_commands(tmp_path: Path, course_env: dict[str, str]) -> Path:
    call_log = tmp_path / "calls.log"
    call_log.touch()
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    for name in ("aws", "openssl", "systemctl", "date", "flock"):
        command = mock_bin / name
        command.write_text(
            "#!/bin/bash\n"
            f"printf '{name} %s\\n' \"$*\" >>\"{call_log}\"\n"
            "if [[ $(basename \"$0\") == aws && ${MOCK_AWS_FAIL_PRESIGN:-} == 1 && $* == *'s3 presign'* ]]; then\n"
            "  exit 43\n"
            "fi\n"
            "if [[ $(basename \"$0\") == systemctl && ${MOCK_SYSTEMCTL_FAIL_RELOAD:-} == 1 && $* == *'reload openvpn-server@course'* ]]; then\n"
            "  exit 42\n"
            "fi\n"
            "if [[ $(basename \"$0\") == openssl && ${MOCK_OPENSSL_FAIL_VERIFY:-} == 1 && ${1:-} == verify ]]; then\n"
            "  exit 44\n"
            "fi\n"
            "if [[ $(basename \"$0\") == date ]]; then\n"
            "  if [[ $* == *'%Y%m%d%H%M%S'* ]]; then\n"
            "    printf '20300101000000\\n'\n"
            "  else\n"
            "    printf '2030-01-01T00:10:00Z\\n'\n"
            "  fi\n"
            "  exit 0\n"
            "fi\n"
            "if [[ $(basename \"$0\") == flock && ${MOCK_FLOCK_BUSY:-} == 1 ]]; then\n"
            "  exit 1\n"
            "fi\n"
            "if [[ $(basename \"$0\") == aws && $* == *'s3 presign'* ]]; then\n"
            "  printf 'https://example.invalid/profile?signature=test\\n'\n"
            "elif [[ $(basename \"$0\") == openssl && ${1:-} == rand ]]; then\n"
            "  printf '0123456789abcdef0123456789abcdef\\n'\n"
            "fi\n"
        )
        command.chmod(0o755)
    pki = tmp_path / "pki"
    (pki / "pki/issued").mkdir(parents=True)
    (pki / "pki/private").mkdir()
    (pki / "pki/ca.crt").write_text("ca\n")
    (pki / "pki/index.txt").write_text("valid course-shared\n")
    (pki / "pki/crlnumber").write_text("01\n")
    (pki / "pki/crl.pem").write_text("old crl\n")
    (pki / "pki/issued/course-shared.crt").write_text("old cert\n")
    (pki / "pki/private/course-shared.key").write_text("old key\n")
    easyrsa = pki / "easyrsa"
    easyrsa.write_text(
        "#!/bin/bash\n"
        f"printf 'easyrsa %s\\n' \"$*\" >>\"{call_log}\"\n"
        "if [[ ${1:-} == build-client-full ]]; then\n"
        "  printf 'new cert\\n' >\"pki/issued/$2.crt\"\n"
        "  printf 'new key\\n' >\"pki/private/$2.key\"\n"
        "  printf 'valid %s\\n' \"$2\" >pki/index.txt\n"
        "elif [[ ${1:-} == revoke ]]; then\n"
        "  printf 'revoked %s\\n' \"$2\" >pki/index.txt\n"
        "elif [[ ${1:-} == gen-crl ]]; then\n"
        "  printf 'new crl\\n' >pki/crl.pem\n"
        "  printf '02\\n' >pki/crlnumber\n"
        "fi\n"
    )
    easyrsa.chmod(0o755)
    renderer = pki / "render-profile"
    renderer.write_text(
        "#!/bin/sh\n"
        "if [ \"${MOCK_RENDER_PROFILE_WITHOUT_TLS_CRYPT:-}\" = 1 ]; then\n"
        "  printf 'client\\n<ca>ca</ca>\\n<cert>cert</cert>\\n<key>key</key>\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'client\\n<ca>ca</ca>\\n<cert>cert</cert>\\n<key>key</key>\\n<tls-crypt>tls</tls-crypt>\\n'\n"
    )
    renderer.chmod(0o755)
    course_env["PATH"] = f"{mock_bin}:{course_env['PATH']}"
    return call_log


def rotation_state(env: dict[str, str]) -> dict[str, object]:
    pki = configured_path(env, "PKI_DIR")
    return {
        "index": (pki / "pki/index.txt").read_text(),
        "crlnumber": (pki / "pki/crlnumber").read_text(),
        "pki_crl": (pki / "pki/crl.pem").read_text(),
        "profile": configured_path(env, "PROFILE_PATH").read_text(),
        "active_cn": configured_path(env, "ACTIVE_CLIENT_CN_FILE").read_text(),
        "server_crl": configured_path(env, "SERVER_CRL_PATH").read_text(),
        "issued": {
            path.name: path.read_text()
            for path in sorted((pki / "pki/issued").iterdir())
        },
        "private": {
            path.name: path.read_text()
            for path in sorted((pki / "pki/private").iterdir())
        },
    }


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


def test_export_uses_no_external_install_or_chown_commands(course_env, tmp_path) -> None:
    destination = tmp_path / "course.ovpn"
    destination.write_text("old profile")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    for command in ("install", "chown"):
        executable = mock_bin / command
        executable.write_text("#!/bin/sh\nexit 99\n")
        executable.chmod(0o755)
    env = {
        **course_env,
        "PATH": f"{mock_bin}:{course_env['PATH']}",
        "SUDO_UID": str(os.getuid()),
        "SUDO_GID": str(os.getgid()),
    }

    result = run_course(env, "export", str(destination), "--force")

    assert result.returncode == 0
    assert destination.read_text() == "client\n<key>secret</key>\n"
    assert destination.stat().st_uid == os.getuid()
    assert destination.stat().st_gid == os.getgid()


def test_export_rejects_a_symlinked_profile(course_env, tmp_path) -> None:
    profile = tmp_path / "current.ovpn"
    profile_link = tmp_path / "current-link.ovpn"
    profile_link.symlink_to(profile)
    environment_file = Path(course_env["OPENVPN_COURSE_ENV_FILE"])
    environment_file.write_text(
        environment_file.read_text().replace(
            f"PROFILE_PATH={profile}", f"PROFILE_PATH={profile_link}"
        )
    )
    destination = tmp_path / "course.ovpn"

    result = run_course(course_env, "export", str(destination))

    assert result.returncode != 0
    assert not destination.exists()
    assert not list(tmp_path.glob(".openvpn-course.*"))


@pytest.mark.parametrize("path_kind", ["absolute", "relative"])
def test_export_rejects_an_intermediate_symlinked_destination_directory(
    course_env, tmp_path, path_kind
) -> None:
    redirected_root = tmp_path / "redirected"
    redirected_nested = redirected_root / "nested"
    redirected_nested.mkdir(parents=True)
    link_root = tmp_path / "link-root"
    link_root.symlink_to(redirected_root)

    if path_kind == "absolute":
        destination = link_root / "nested" / "course.ovpn"
        cwd = None
    else:
        destination = Path("link-root") / "nested" / "course.ovpn"
        cwd = tmp_path

    result = run_course(course_env, "export", str(destination), cwd=cwd)

    assert result.returncode != 0
    assert "destination directory must not contain symlinks" in result.stderr
    assert not (redirected_nested / "course.ovpn").exists()
    assert not list(redirected_nested.glob(".openvpn-course.*"))


def test_export_rejects_parent_directory_destination_components(
    course_env, tmp_path
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()

    result = run_course(
        course_env,
        "export",
        str(Path("nested") / ".." / "course.ovpn"),
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "destination directory must not contain '..'" in result.stderr
    assert not (tmp_path / "course.ovpn").exists()
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


def test_rotate_requires_exact_confirmation(course_env) -> None:
    result = run_course(
        course_env, "rotate", "--days", "30", input_text="ROTATE\n"
    )
    assert result.returncode != 0
    assert "rotation cancelled" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--days",),
        ("30",),
        ("--days", "0"),
        ("--days", "366"),
        ("--days", "thirty"),
        ("--days", "30", "extra"),
    ],
)
def test_rotate_rejects_invalid_argument_shapes(course_env, args) -> None:
    result = run_course(
        course_env, "rotate", *args, input_text="ROTATE course-shared\n"
    )
    assert result.returncode != 0
    assert "usage: openvpn-course rotate --days N" in result.stderr


def test_rotate_builds_and_validates_before_revoking(
    course_env, mock_commands
) -> None:
    result = run_course(
        course_env,
        "rotate",
        "--days",
        "30",
        input_text="ROTATE course-shared\n",
    )
    assert result.returncode == 0, result.stderr
    calls = mock_commands.read_text().splitlines()
    build_index = next(i for i, line in enumerate(calls) if "build-client-full" in line)
    verify_index = next(i for i, line in enumerate(calls) if "verify" in line)
    revoke_index = next(i for i, line in enumerate(calls) if "revoke course-shared" in line)
    reload_index = next(
        i for i, line in enumerate(calls) if "reload openvpn-server@course" in line
    )
    assert build_index < verify_index < revoke_index < reload_index
    assert configured_path(course_env, "PROFILE_PATH").read_text().startswith(
        "client\n<ca>ca</ca>\n"
    )
    assert configured_path(course_env, "ACTIVE_CLIENT_CN_FILE").read_text().startswith(
        "course-shared-"
    )
    assert configured_path(course_env, "SERVER_CRL_PATH").read_text() == "new crl\n"


def test_rotate_rolls_back_after_post_revoke_failure(
    course_env, mock_commands
) -> None:
    result = run_course(
        {**course_env, "MOCK_SYSTEMCTL_FAIL_RELOAD": "1"},
        "rotate",
        "--days",
        "30",
        input_text="ROTATE course-shared\n",
    )

    assert result.returncode != 0
    assert configured_path(course_env, "PROFILE_PATH").read_text() == (
        "client\n<key>secret</key>\n"
    )
    assert configured_path(course_env, "ACTIVE_CLIENT_CN_FILE").read_text() == (
        "course-shared\n"
    )
    assert configured_path(course_env, "SERVER_CRL_PATH").read_text() == (
        "old server crl\n"
    )
    pki = configured_path(course_env, "PKI_DIR")
    assert (pki / "pki/crl.pem").read_text() == "old crl\n"
    assert "revoked course-shared" not in (pki / "pki/index.txt").read_text()
    calls = mock_commands.read_text().splitlines()
    assert sum("reload openvpn-server@course" in line for line in calls) == 2


def test_rotate_rolls_back_build_artifacts_when_verify_fails(
    course_env, mock_commands
) -> None:
    before = rotation_state(course_env)

    result = run_course(
        {**course_env, "MOCK_OPENSSL_FAIL_VERIFY": "1"},
        "rotate",
        "--days",
        "30",
        input_text="ROTATE course-shared\n",
    )

    assert result.returncode != 0
    assert rotation_state(course_env) == before
    calls = mock_commands.read_text().splitlines()
    assert any("build-client-full" in line for line in calls)
    assert any("verify" in line for line in calls)
    assert not any("revoke course-shared" in line for line in calls)
    assert not any("reload openvpn-server@course" in line for line in calls)


def test_rotate_rolls_back_build_artifacts_when_profile_validation_fails(
    course_env, mock_commands
) -> None:
    before = rotation_state(course_env)

    result = run_course(
        {**course_env, "MOCK_RENDER_PROFILE_WITHOUT_TLS_CRYPT": "1"},
        "rotate",
        "--days",
        "30",
        input_text="ROTATE course-shared\n",
    )

    assert result.returncode != 0
    assert "rendered profile is missing <tls-crypt>" in result.stderr
    assert rotation_state(course_env) == before
    calls = mock_commands.read_text().splitlines()
    assert any("build-client-full" in line for line in calls)
    assert any("verify" in line for line in calls)
    assert not any("revoke course-shared" in line for line in calls)
    assert not any("reload openvpn-server@course" in line for line in calls)


@pytest.mark.parametrize("args", [("extra",), ("--expires-in", "600")])
def test_share_rejects_invalid_argument_shapes(course_env, args) -> None:
    result = run_course(course_env, "share", *args)
    assert result.returncode != 0
    assert "usage: openvpn-course share" in result.stderr


def test_share_uses_random_key_and_exact_ten_minute_expiry(
    course_env, mock_commands
) -> None:
    result = run_course(course_env, "share")
    assert result.returncode == 0, result.stderr
    call_lines = mock_commands.read_text().splitlines()
    calls = "\n".join(call_lines)
    assert "s3 cp" in calls
    assert "s3 presign" in calls
    assert "--expires-in 600" in calls
    assert "s3://test-bucket/profiles/" in calls
    assert "secret" not in result.stdout
    assert result.stdout.count("https://") == 1
    assert re.search(
        r"Expires no later than: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        result.stdout,
    )
    deadline_index = next(
        i for i, line in enumerate(call_lines) if line.startswith("date ")
    )
    presign_index = next(
        i for i, line in enumerate(call_lines) if "s3 presign" in line
    )
    assert deadline_index < presign_index
    assert configured_path(course_env, "LAST_SHARED_KEY_FILE").read_text() == (
        "profiles/0123456789abcdef0123456789abcdef/course-vpn.ovpn\n"
    )


def test_share_removes_previous_profile_before_uploading_new_one(
    course_env, mock_commands
) -> None:
    previous_key = "profiles/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/course-vpn.ovpn"
    configured_path(course_env, "LAST_SHARED_KEY_FILE").write_text(f"{previous_key}\n")

    result = run_course(course_env, "share")

    assert result.returncode == 0, result.stderr
    calls = mock_commands.read_text().splitlines()
    rm_index = next(
        i
        for i, line in enumerate(calls)
        if f"s3 rm s3://test-bucket/{previous_key}" in line
    )
    cp_index = next(i for i, line in enumerate(calls) if "s3 cp" in line)
    assert rm_index < cp_index
    assert configured_path(course_env, "LAST_SHARED_KEY_FILE").read_text() == (
        "profiles/0123456789abcdef0123456789abcdef/course-vpn.ovpn\n"
    )


def test_share_keeps_previous_key_when_presign_fails(
    course_env, mock_commands
) -> None:
    previous_key = "profiles/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/course-vpn.ovpn"
    configured_path(course_env, "LAST_SHARED_KEY_FILE").write_text(f"{previous_key}\n")

    result = run_course({**course_env, "MOCK_AWS_FAIL_PRESIGN": "1"}, "share")

    assert result.returncode != 0
    calls = mock_commands.read_text()
    assert "s3 cp" in calls
    assert "s3 presign" in calls
    assert configured_path(course_env, "LAST_SHARED_KEY_FILE").read_text() == (
        f"{previous_key}\n"
    )
    assert "https://" not in result.stdout


def test_share_fails_fast_when_another_mutating_operation_holds_the_lock(
    course_env, mock_commands
) -> None:
    lock_path = configured_path(course_env, "MUTATION_LOCK_PATH")
    with lock_path.open("w") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = run_course({**course_env, "MOCK_FLOCK_BUSY": "1"}, "share")

    assert result.returncode != 0
    assert "another rotate or share operation is already in progress" in result.stderr
    assert "aws " not in mock_commands.read_text()


def test_rotate_fails_fast_after_confirmation_when_the_mutation_lock_is_busy(
    course_env, mock_commands
) -> None:
    result = run_course(
        {**course_env, "MOCK_FLOCK_BUSY": "1"},
        "rotate",
        "--days",
        "30",
        input_text="ROTATE course-shared\n",
    )

    assert result.returncode != 0
    assert "another rotate or share operation is already in progress" in result.stderr
    assert "easyrsa " not in mock_commands.read_text()


def test_read_only_commands_do_not_acquire_the_mutation_lock(course_env, tmp_path) -> None:
    lock_log = tmp_path / "flock.log"
    mock_bin = tmp_path / "read-only-bin"
    mock_bin.mkdir()
    (mock_bin / "flock").write_text(
        "#!/bin/sh\nprintf 'unexpected flock %s\\n' \"$*\" >>\"$LOCK_LOG\"\nexit 99\n"
    )
    (mock_bin / "journalctl").write_text("#!/bin/sh\nexit 0\n")
    for command in mock_bin.iterdir():
        command.chmod(0o755)
    env = {
        **course_env,
        "PATH": f"{mock_bin}:{course_env['PATH']}",
        "LOCK_LOG": str(lock_log),
    }

    status = run_course(env, "status")
    exported = run_course(env, "export", str(tmp_path / "course.ovpn"))
    logs = run_course(env, "logs")

    assert status.returncode == 0
    assert exported.returncode == 0
    assert logs.returncode == 0
    assert not lock_log.exists()
