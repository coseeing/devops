from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible_yaml/roles/openvpn_server"
RENDERER = ROOT / "scripts/openvpn-course-render-profile"
FIREWALL = ROOT / "scripts/openvpn-course-firewall"


def load_role_tasks() -> list[dict[str, object]]:
    return yaml.safe_load((ROLE / "tasks/main.yml").read_text())


def named_task(tasks: list[dict[str, object]], name: str) -> dict[str, object]:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"missing task: {name}")


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


def test_role_verifies_profile_bucket_with_no_log_sentinel_data() -> None:
    tasks = load_role_tasks()
    sentinel = named_task(
        tasks, "Verify temporary profile bucket permissions with sentinel data"
    )

    assert sentinel["when"] == "openvpn_verify_s3 | bool"
    assert sentinel["no_log"] is True
    assert "current.ovpn" not in str(sentinel)

    block_names = [str(task.get("name")) for task in sentinel["block"]]
    assert block_names == [
        "Create temporary OpenVPN profile bucket sentinel",
        "Upload temporary OpenVPN profile bucket sentinel",
        "Presign temporary OpenVPN profile bucket sentinel",
        "Download temporary OpenVPN profile bucket sentinel",
        "Compare temporary OpenVPN profile bucket sentinel",
    ]
    upload = sentinel["block"][1]["ansible.builtin.command"]["argv"]
    presign = sentinel["block"][2]["ansible.builtin.command"]["argv"]
    download = sentinel["block"][3]["ansible.builtin.get_url"]
    compare = sentinel["block"][4]["ansible.builtin.command"]["argv"]
    sentinel_key = (
        "s3://{{ openvpn_profile_bucket }}/profiles/"
        "sentinel-{{ ansible_date_time.epoch }}"
    )
    assert upload == [
        "{{ openvpn_aws_cli_path }}",
        "s3",
        "cp",
        "/tmp/openvpn-course-s3-sentinel",
        sentinel_key,
        "--only-show-errors",
        "--region",
        "{{ openvpn_aws_region }}",
    ]
    assert presign == [
        "{{ openvpn_aws_cli_path }}",
        "s3",
        "presign",
        sentinel_key,
        "--expires-in",
        "600",
        "--region",
        "{{ openvpn_aws_region }}",
    ]
    assert sentinel["block"][2]["register"] == "openvpn_sentinel_url"
    assert download["url"] == "{{ openvpn_sentinel_url.stdout }}"
    assert compare == [
        "cmp",
        "--silent",
        "/tmp/openvpn-course-s3-sentinel",
        "/tmp/openvpn-course-s3-sentinel.downloaded",
    ]

    always_names = [str(task.get("name")) for task in sentinel["always"]]
    assert always_names == [
        "Delete temporary OpenVPN profile bucket sentinel",
        "Remove temporary OpenVPN profile bucket sentinel files",
    ]
    cleanup = sentinel["always"][0]
    assert cleanup["ansible.builtin.command"]["argv"] == [
        "{{ openvpn_aws_cli_path }}",
        "s3",
        "rm",
        sentinel_key,
        "--only-show-errors",
        "--region",
        "{{ openvpn_aws_region }}",
    ]
    assert cleanup["failed_when"] is False


def test_role_installs_scoped_firewall_before_openvpn_without_regressing_rollback() -> None:
    tasks = load_role_tasks()
    names = [str(task.get("name")) for task in tasks]
    activation_index = names.index("Activate OpenVPN artifacts transactionally")
    activation = named_task(tasks, "Activate OpenVPN artifacts transactionally")
    activation_names = [str(task.get("name")) for task in activation["block"]]
    firewall_validate_index = activation_names.index("Validate staged course firewall rules")
    firewall_install_index = activation_names.index("Install validated active course firewall rules")
    firewall_start_index = activation_names.index("Enable and start course firewall")
    firewall_reload_index = activation_names.index("Reload course firewall before OpenVPN")
    openvpn_start_index = activation_names.index("Enable and start course OpenVPN")

    assert names.index("Render staged course firewall rules") < activation_index
    assert firewall_install_index < firewall_start_index < firewall_reload_index
    assert firewall_reload_index < openvpn_start_index

    tasks = (ROLE / "tasks/main.yml").read_text()
    handlers = (ROLE / "handlers/main.yml").read_text()
    combined = f"{tasks}\n{handlers}"
    assert "nftables" in tasks
    assert "scripts/openvpn-course-firewall" in tasks
    assert "course-firewall.nft.j2" in tasks
    assert "dest: /etc/openvpn/course-firewall.nft.staged" in tasks
    assert "argv: [nft, --check, --file, /etc/openvpn/course-firewall.nft.staged]" in tasks
    assert "Reload course firewall" in handlers
    assert "flush ruleset" not in combined
    assert "iptables" not in combined
    assert "docker" not in combined.lower()


def test_activation_rollback_restores_captured_service_state() -> None:
    tasks = load_role_tasks()
    names = [str(task.get("name")) for task in tasks]
    activation_index = names.index("Activate OpenVPN artifacts transactionally")
    assert names.index("Capture prior OpenVPN active state") < activation_index
    assert names.index("Capture prior OpenVPN enabled state") < activation_index
    assert names.index("Resolve previous active OpenVPN file presence") < activation_index

    tasks_text = (ROLE / "tasks/main.yml").read_text()
    assert "openvpn_previous_server_config" not in tasks_text
    assert "openvpn_prior_active_state" in tasks_text
    assert "openvpn_prior_enabled_state" in tasks_text
    assert "openvpn_had_previous_active_files" in tasks_text

    activation = named_task(tasks, "Activate OpenVPN artifacts transactionally")
    rescue_names = [str(task.get("name")) for task in activation["rescue"]]
    assert "Restore prior OpenVPN enabled state" in rescue_names
    assert "Restore active OpenVPN service after rollback" in rescue_names
    assert "Stop OpenVPN service after rollback" in rescue_names
    assert "Restore prior course firewall enabled state" in rescue_names
    assert "Reload active course firewall after rollback" in rescue_names
    assert "Stop course firewall after rollback" in rescue_names


def test_ip_forward_changes_are_rolled_back_with_activation() -> None:
    tasks = load_role_tasks()
    names = [str(task.get("name")) for task in tasks]
    activation_index = names.index("Activate OpenVPN artifacts transactionally")
    assert names.index("Capture prior IP forwarding runtime value") < activation_index
    assert names.index("Check prior IP forwarding sysctl file") < activation_index
    assert names.index("Read prior IP forwarding sysctl file") < activation_index
    assert "Persist OpenVPN course IP forwarding" not in names
    assert "Apply OpenVPN course IP forwarding" not in names

    activation = named_task(tasks, "Activate OpenVPN artifacts transactionally")
    activation_names = [str(task.get("name")) for task in activation["block"]]
    persist_index = activation_names.index("Persist OpenVPN course IP forwarding")
    apply_index = activation_names.index("Apply OpenVPN course IP forwarding")
    firewall_start_index = activation_names.index("Enable and start course firewall")
    assert persist_index < apply_index < firewall_start_index

    rescue_names = [str(task.get("name")) for task in activation["rescue"]]
    assert "Restore previous IP forwarding sysctl file" in rescue_names
    assert "Remove IP forwarding sysctl file created by failed deploy" in rescue_names
    assert "Restore prior IP forwarding runtime value" in rescue_names

    tasks_text = (ROLE / "tasks/main.yml").read_text()
    assert "openvpn_prior_ip_forward_value" in tasks_text
    assert "openvpn_prior_ip_forward_file_stat" in tasks_text
    assert "openvpn_prior_ip_forward_file_content.content | b64decode" in tasks_text
    assert "openvpn_prior_ip_forward_file_stat.stat.uid" in tasks_text
    assert "openvpn_prior_ip_forward_file_stat.stat.gid" in tasks_text
    assert "openvpn_prior_ip_forward_file_stat.stat.mode" in tasks_text
    assert "net.ipv4.ip_forward={{ openvpn_prior_ip_forward_value.stdout | trim }}" in tasks_text
    assert "sysctl, --system" not in tasks_text
    assert "net.ipv6" not in tasks_text


def test_firewall_allows_only_rdp_to_windows_and_drops_other_vpn_traffic() -> None:
    rules = (ROLE / "templates/course-firewall.nft.j2").read_text()
    assert "table inet openvpn_course" in rules
    assert "table ip openvpn_course_nat" in rules
    assert "type filter hook input priority filter; policy accept;" in rules
    assert "type filter hook forward priority filter; policy accept;" in rules
    assert "type nat hook postrouting priority srcnat; policy accept;" in rules
    assert 'iifname "tun-course" ip saddr {{ openvpn_vpn_cidr }} ip daddr {{ openvpn_windows_cidr }} tcp dport 3389 accept' in rules
    assert 'iifname "tun-course" ip daddr {{ openvpn_windows_cidr }} tcp dport 3389 accept' not in rules
    assert 'iifname "tun-course" drop' in rules
    assert 'oifname "tun-course" ct state established,related accept' in rules
    assert 'oifname "tun-course" drop' in rules
    assert 'ip saddr {{ openvpn_vpn_cidr }} ip daddr {{ openvpn_windows_cidr }} tcp dport 3389 masquerade' in rules
    assert "flush ruleset" not in rules
    assert "0.0.0.0/0" not in rules
    assert "openvpn_vpc_cidr" not in rules


def test_firewall_drops_new_return_path_traffic_before_vpn_ingress_rules() -> None:
    rules = (ROLE / "templates/course-firewall.nft.j2").read_text()
    established = rules.index('oifname "tun-course" ct state established,related accept')
    return_drop = rules.index('oifname "tun-course" drop')
    exact_allow = rules.index(
        'iifname "tun-course" ip saddr {{ openvpn_vpn_cidr }} '
        'ip daddr {{ openvpn_windows_cidr }} tcp dport 3389 accept'
    )
    vpn_ingress_drop = rules.rindex('iifname "tun-course" drop')

    assert established < return_drop < exact_allow < vpn_ingress_drop


def test_firewall_unit_is_required_scoped_and_starts_before_openvpn() -> None:
    unit = (ROLE / "templates/openvpn-course-firewall.service.j2").read_text()
    assert "Before=openvpn-server@course.service" in unit
    assert "RequiredBy=openvpn-server@course.service" in unit
    assert "ExecStart=/usr/local/libexec/openvpn-course-firewall apply" in unit
    assert "ExecReload=/usr/local/libexec/openvpn-course-firewall reload" in unit
    assert "ExecStop=/usr/local/libexec/openvpn-course-firewall remove" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "flush ruleset" not in unit


def write_fake_nft(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    log = tmp_path / "nft-calls.log"
    log.touch()
    batch_dir = tmp_path / "nft-batches"
    batch_dir.mkdir()
    nft = mock_bin / "nft"
    nft.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >>\"${FAKE_NFT_LOG:?}\"\n"
        "case \"$*\" in\n"
        "  'list table inet openvpn_course')\n"
        "    [[ \"${FAKE_NFT_HAS_INET:-1}\" == 1 ]] || exit 1\n"
        "    ;;\n"
        "  'list table ip openvpn_course_nat')\n"
        "    [[ \"${FAKE_NFT_HAS_NAT:-1}\" == 1 ]] || exit 1\n"
        "    ;;\n"
        "  'delete table inet openvpn_course'|'delete table ip openvpn_course_nat')\n"
        "    ;;\n"
        "  --check\\ --file*)\n"
        "    if [[ \"${FAKE_NFT_FAIL_CHECK:-0}\" == 1 ]]; then exit 33; fi\n"
        "    if [[ \"${3:-}\" != \"${OPENVPN_COURSE_FIREWALL_RULES:-}\" && -f \"${3:-}\" ]]; then\n"
        "      cp \"$3\" \"${FAKE_NFT_BATCH_DIR:?}/checked-batch.nft\"\n"
        "    fi\n"
        "    ;;\n"
        "  --file*)\n"
        "    if [[ -f \"${2:-}\" ]]; then cp \"$2\" \"${FAKE_NFT_BATCH_DIR:?}/loaded-batch.nft\"; fi\n"
        "    if [[ \"${FAKE_NFT_FAIL_LOAD:-0}\" == 1 ]]; then exit 34; fi\n"
        "    ;;\n"
        "  *)\n"
        "    exit 97\n"
        "    ;;\n"
        "esac\n"
    )
    nft.chmod(0o755)
    return mock_bin, log, batch_dir


def firewall_env(tmp_path: Path, **overrides: str) -> tuple[dict[str, str], Path, Path, Path]:
    mock_bin, log, batch_dir = write_fake_nft(tmp_path)
    rules = tmp_path / "course-firewall.nft"
    rules.write_text(
        "table inet openvpn_course {}\n"
        "table ip openvpn_course_nat {}\n"
    )
    env = {
        **os.environ,
        "PATH": f"{mock_bin}:{os.environ['PATH']}",
        "OPENVPN_COURSE_FIREWALL_RULES": str(rules),
        "FAKE_NFT_LOG": str(log),
        "FAKE_NFT_BATCH_DIR": str(batch_dir),
        **overrides,
    }
    return env, rules, log, batch_dir


def run_firewall(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(FIREWALL), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_firewall_helper_apply_and_reload_replace_scoped_tables_atomically(
    tmp_path: Path,
) -> None:
    for command in ("apply", "reload"):
        env, rules, log, batch_dir = firewall_env(tmp_path / command)

        result = run_firewall(env, command)

        assert result.returncode == 0, result.stderr
        calls = log.read_text().splitlines()
        assert calls[:3] == [
            f"--check --file {rules}",
            "list table inet openvpn_course",
            "list table ip openvpn_course_nat",
        ]
        assert calls[3].startswith("--check --file ")
        assert calls[4].startswith("--file ")
        assert calls[3].removeprefix("--check --file ") == calls[4].removeprefix(
            "--file "
        )
        assert (batch_dir / "loaded-batch.nft").read_text() == (
            "delete table inet openvpn_course\n"
            "delete table ip openvpn_course_nat\n"
            f"include \"{rules}\"\n"
        )


def test_firewall_helper_remove_deletes_only_scoped_tables(tmp_path: Path) -> None:
    env, _rules, log, _batch_dir = firewall_env(tmp_path)

    result = run_firewall(env, "remove")

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "list table inet openvpn_course",
        "delete table inet openvpn_course",
        "list table ip openvpn_course_nat",
        "delete table ip openvpn_course_nat",
    ]


def test_firewall_helper_remove_succeeds_when_scoped_tables_are_absent(
    tmp_path: Path,
) -> None:
    env, _rules, log, _batch_dir = firewall_env(
        tmp_path, FAKE_NFT_HAS_INET="0", FAKE_NFT_HAS_NAT="0"
    )

    result = run_firewall(env, "remove")

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "list table inet openvpn_course",
        "list table ip openvpn_course_nat",
    ]


def test_firewall_helper_validation_failure_does_not_remove_or_load_tables(
    tmp_path: Path,
) -> None:
    env, rules, log, batch_dir = firewall_env(tmp_path, FAKE_NFT_FAIL_CHECK="1")

    result = run_firewall(env, "apply")

    assert result.returncode != 0
    assert log.read_text().splitlines() == [f"--check --file {rules}"]
    assert not list(batch_dir.iterdir())


def test_firewall_helper_load_failure_has_no_standalone_delete_before_failure(
    tmp_path: Path,
) -> None:
    env, rules, log, batch_dir = firewall_env(tmp_path, FAKE_NFT_FAIL_LOAD="1")

    result = run_firewall(env, "apply")

    assert result.returncode != 0
    assert "delete table inet openvpn_course" not in log.read_text().splitlines()
    assert "delete table ip openvpn_course_nat" not in log.read_text().splitlines()
    assert (batch_dir / "loaded-batch.nft").read_text() == (
        "delete table inet openvpn_course\n"
        "delete table ip openvpn_course_nat\n"
        f"include \"{rules}\"\n"
    )


def test_firewall_helper_defaults_to_fixed_production_rules_path(
    tmp_path: Path,
) -> None:
    mock_bin, log, batch_dir = write_fake_nft(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{mock_bin}:{os.environ['PATH']}",
        "FAKE_NFT_LOG": str(log),
        "FAKE_NFT_BATCH_DIR": str(batch_dir),
    }

    result = run_firewall(env, "apply")

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[0] == (
        "--check --file /etc/openvpn/course-firewall.nft"
    )
    assert (batch_dir / "loaded-batch.nft").read_text().endswith(
        'include "/etc/openvpn/course-firewall.nft"\n'
    )


def test_firewall_helper_rejects_invalid_cli_without_touching_nft(
    tmp_path: Path,
) -> None:
    env, _rules, log, _batch_dir = firewall_env(tmp_path)

    result = run_firewall(env, "status")

    assert result.returncode == 2
    assert "usage: openvpn-course-firewall {apply|reload|remove}" in result.stderr
    assert log.read_text() == ""


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
