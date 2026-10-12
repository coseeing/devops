from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_workflow():
    return yaml.safe_load((ROOT / ".github/workflows/deploy-openvpn.yml").read_text())


def load_profile_workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/manage-openvpn-profile.yml").read_text()
    )


def test_workflow_has_validate_and_exactly_confirmed_deploy_jobs() -> None:
    workflow = load_workflow()
    assert set(workflow["jobs"]) == {"validate", "deploy"}
    assert workflow["jobs"]["deploy"]["if"] == (
        "inputs.action == 'deploy' && inputs.confirm_endpoint == inputs.vpn_endpoint"
    )
    assert workflow["jobs"]["deploy"]["environment"] == "a11y-village-production"


def test_workflow_discovers_networks_without_modifying_security_groups() -> None:
    workflow_text = (ROOT / ".github/workflows/deploy-openvpn.yml").read_text()
    assert "aws ec2 describe-subnets" in workflow_text
    assert "aws ec2 describe-vpcs" in workflow_text
    assert "validate-openvpn-inputs.py" in workflow_text
    for forbidden in (
        "authorize-security-group-ingress",
        "revoke-security-group-ingress",
        "modify-network-interface-attribute",
        "replace-route",
    ):
        assert forbidden not in workflow_text


def test_workflow_validates_before_deploy_and_hides_s3_sentinel_url() -> None:
    workflow = load_workflow()
    assert workflow["jobs"]["deploy"]["needs"] == "validate"
    deploy_steps = {
        step.get("name"): step for step in workflow["jobs"]["deploy"]["steps"]
    }
    assert (
        "--no-fail-on-empty-changeset"
        in deploy_steps["Deploy profile distribution"]["run"]
    )
    assert (
        deploy_steps["Deploy OpenVPN with Ansible"]["env"]["ANSIBLE_HOST_KEY_CHECKING"]
        == "True"
    )
    assert (
        "UserKnownHostsFile"
        in deploy_steps["Deploy OpenVPN with Ansible"]["env"]["ANSIBLE_SSH_ARGS"]
    )
    deploy_run = deploy_steps["Deploy OpenVPN with Ansible"]["run"]
    assert "ansible-playbook" in deploy_run
    assert "current.ovpn" not in deploy_run


def test_deployment_summary_excludes_topology_outputs() -> None:
    workflow = load_workflow()
    summary_step = next(
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deployment summary"
    )
    summary_run = summary_step["run"]

    for forbidden in (
        "Endpoint:",
        "Linux EC2:",
        "Linux public IP:",
        "VPN CIDR:",
        "Windows CIDR:",
        "VPC CIDR:",
    ):
        assert forbidden not in summary_run

    assert "Distribution stack:" in summary_run
    assert "Profile bucket:" in summary_run
    assert "Deployment:" in summary_run
    assert "Remote checks:" in summary_run


def test_workflow_remote_check_verifies_active_docker_course_firewall_path() -> None:
    workflow = load_workflow()
    remote_run = next(
        step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Verify remote OpenVPN service state"
    )

    assert "nft list table inet openvpn_course >/dev/null" not in remote_run
    assert "iptables --version" in remote_run
    assert "nf_tables" in remote_run
    assert "iptables -w 5 -t filter -C FORWARD -j DOCKER-USER" in remote_run
    assert "iptables -w 5 -t filter -S FORWARD" in remote_run
    assert "first_forward_rule" in remote_run
    assert "-A FORWARD -j DOCKER-USER" in remote_run
    assert "iptables -w 5 -t filter -S DOCKER-USER" in remote_run
    assert "openvpn-course-forward" in remote_run
    assert "OPENVPN-COURSE-A" in remote_run
    assert "OPENVPN-COURSE-B" in remote_run
    assert '-S "$active_course_chain"' in remote_run
    assert "nft list table inet openvpn_course_input" in remote_run
    assert "nft list table ip openvpn_course_nat" in remote_run


def test_workflow_remote_check_verifies_nvda_remote_server() -> None:
    workflow = load_workflow()
    remote_run = next(
        step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Verify remote OpenVPN service state"
    )

    assert "systemctl is-active --quiet nvda-remote-server" in remote_run
    assert "systemctl is-enabled --quiet nvda-remote-server" in remote_run
    assert 'ss -ltn | grep -Eq "[:.]6837[[:space:]]"' in remote_run
    assert "tcp dport 6837 drop" in remote_run
    assert "systemctl is-enabled --quiet nvda-remote-cert-renew.timer" in remote_run
    assert "openssl verify -CAfile /etc/nvda-remote-server/ca/ca.crt" in remote_run


def test_workflow_distributes_only_the_public_nvda_ca_to_windows() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [step.get("name") for step in steps]
    read_ca = steps[names.index("Read NVDA Remote CA certificate")]["run"]
    trust = steps[names.index("Trust NVDA Remote CA on managed Windows VMs")]["run"]

    assert names.index("Deploy OpenVPN with Ansible") < names.index(
        "Read NVDA Remote CA certificate"
    ) < names.index("Trust NVDA Remote CA on managed Windows VMs")
    assert "sudo cat /etc/nvda-remote-server/ca/ca.crt" in read_ca
    assert "ca.key" not in read_ca
    assert 'subject=CN=Coseeing NVDA Remote CA' in read_ca
    assert "cloudformation/nvda-remote-ca-trust-template.yml" in trust
    assert 'CaCertificateDerBase64="$CA_DER_BASE64"' in trust

    validate_runs = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["validate"]["steps"]
    )
    assert "cloudformation/nvda-remote-ca-trust-template.yml" in validate_runs
    assert "scripts/nvda-remote-cert" in validate_runs


def test_deploy_preserves_current_profile_without_publishing_a_share() -> None:
    workflow = load_workflow()
    deploy = workflow["jobs"]["deploy"]

    assert deploy["concurrency"] == {
        "group": "openvpn-course-production",
        "cancel-in-progress": False,
        "queue": "max",
    }
    deploy_text = yaml.safe_dump(deploy)
    assert "openvpn-course share" not in deploy_text
    assert "openvpn-course rotate" not in deploy_text
    assert "PROFILE_SECRET_ID" not in deploy_text
    assert "secretsmanager" not in deploy_text


def test_profile_workflow_rotates_or_shares_only_when_explicitly_requested() -> None:
    workflow = load_profile_workflow()
    dispatch = workflow[True]["workflow_dispatch"]["inputs"]
    manage = workflow["jobs"]["manage"]
    steps = {step.get("name"): step for step in manage["steps"]}

    assert dispatch["action"]["options"] == ["share", "rotate"]
    assert dispatch["client_days"]["default"] == "30"
    assert dispatch["confirm_rotation"]["required"] is False
    assert manage["environment"] == "a11y-village-production"
    assert manage["concurrency"] == {
        "group": "openvpn-course-production",
        "cancel-in-progress": False,
        "queue": "max",
    }
    assert manage["env"]["PROFILE_SECRET_ID"] == ("prod/vms-portal/openvpn-profile")

    validate_run = steps["Validate requested profile operation"]["run"]
    assert "ROTATE course-shared" in validate_run
    assert "CLIENT_DAYS" in validate_run

    rotate = steps["Rotate shared OpenVPN certificate"]
    assert rotate["if"] == "inputs.action == 'rotate'"
    assert "sudo openvpn-course rotate --days" in rotate["run"]
    assert "ROTATE course-shared" in rotate["run"]

    publish = steps["Publish current OpenVPN profile download secret"]
    assert publish["env"] == {
        "HOST_IP": "${{ steps.discover.outputs.host_ip }}",
        "SSH_KNOWN_HOSTS_PATH": "${{ runner.temp }}/openvpn-known-hosts",
        "SSH_KEY_PATH": "${{ runner.temp }}/openvpn-ec2-key",
    }
    publish_run = publish["run"]
    assert "sudo openvpn-course share" in publish_run
    assert 'echo "::add-mask::${PROFILE_URL}"' in publish_run
    assert "jq -n" in publish_run
    assert "'{url:$url,expires_at:$expires_at}'" in publish_run
    assert "aws secretsmanager describe-secret" in publish_run
    assert "aws secretsmanager put-secret-value" in publish_run
    assert "aws secretsmanager create-secret" in publish_run
    assert "ResourceNotFoundException" in publish_run
    assert 'cat "$DESCRIBE_ERROR" >&2' in publish_run
    assert "$GITHUB_OUTPUT" not in publish_run
    assert "$GITHUB_STEP_SUMMARY" not in publish_run


def test_workflows_pin_the_openvpn_ssh_host_key() -> None:
    deploy = load_workflow()["jobs"]["deploy"]
    manage = load_profile_workflow()["jobs"]["manage"]

    for job in (deploy, manage):
        steps = {step.get("name"): step for step in job["steps"]}
        install = steps["Install EC2 SSH credentials"]
        assert install["env"]["EC2_SSH_KNOWN_HOSTS"] == (
            "${{ secrets.OPENVPN_SSH_KNOWN_HOSTS }}"
        )
        assert install["env"]["SSH_KNOWN_HOSTS_PATH"] == (
            "${{ runner.temp }}/openvpn-known-hosts"
        )

        job_text = yaml.safe_dump(job)
        assert "StrictHostKeyChecking=yes" in job_text
        assert "UserKnownHostsFile" in job_text
        assert "StrictHostKeyChecking=no" not in job_text
        assert "UserKnownHostsFile=/dev/null" not in job_text


def test_operations_doc_covers_manual_security_and_end_to_end_checks() -> None:
    text = (ROOT / "docs/openvpn-course-operations.md").read_text()
    for required in (
        "sudo openvpn-course status",
        "sudo openvpn-course share",
        "sudo openvpn-course export",
        "sudo openvpn-course rotate --days 30",
        "sudo openvpn-course logs",
        "UDP 1194",
        "TCP 3389",
        "Private IPv4",
        "10 minutes",
    ):
        assert required in text
    assert "does not modify Security Groups" in text


def test_operations_docs_cover_pinned_host_key_and_ambiguous_rotation() -> None:
    english = (ROOT / "docs/openvpn-course-operations.md").read_text()
    chinese = (ROOT / "docs/openvpn-course-operations.zh-TW.md").read_text()

    for text in (english, chinese):
        assert "OPENVPN_SSH_KNOWN_HOSTS" in text
        assert "/var/lib/openvpn-course/active-client-cn" in text
        assert "sudo openvpn-course status" in text


def doc_section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + len(heading) + 3)
    return text[start:] if end == -1 else text[start:end]


def test_operations_doc_explains_share_cleanup_failure_and_retry() -> None:
    text = (ROOT / "docs/openvpn-course-operations.md").read_text()
    section = doc_section(text, "Share a Profile for 10 Minutes")
    assert "before uploading or presigning the new one" in section
    assert "the previous URL/object may already be unavailable" in section
    assert "rerun `sudo openvpn-course share`" in section
    assert "S3 lifecycle expiration" in section
    assert "on the next successful share" not in section


def test_operations_doc_separates_user_instance_lookup_from_admin_listing() -> None:
    text = (ROOT / "docs/openvpn-course-operations.md").read_text()
    section = doc_section(text, "Find the Windows Private IPv4 in VMS Portal")
    assert "normal user" in section
    assert "exact EC2 Instance ID" in section
    assert "lookup form" in section
    assert "admin" in section
    assert "browse/list records" in section


def test_operations_doc_stops_and_disables_openvpn_before_firewall_removal() -> None:
    text = (ROOT / "docs/openvpn-course-operations.md").read_text()
    section = doc_section(text, "Recover or Remove Only OpenVPN Components")
    assert section.count("sudo openvpn-course-firewall remove") == 1
    commands = (
        "sudo systemctl stop openvpn-server@course",
        "sudo systemctl disable openvpn-server@course",
        "sudo systemctl stop openvpn-course-firewall",
        "sudo systemctl disable openvpn-course-firewall",
        "sudo openvpn-course-firewall remove",
    )
    positions = [section.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "never remove the firewall while the VPN service runs" in section


def test_operations_doc_explains_docker_user_preflight_and_mutation_locking() -> None:
    text = (ROOT / "docs/openvpn-course-operations.md").read_text()
    scope = doc_section(text, "Scope and Security Boundary")
    share = doc_section(text, "Share a Profile for 10 Minutes")
    rotation = doc_section(text, "Rotate Every Distributed Profile")
    recovery = doc_section(text, "Recover or Remove Only OpenVPN Components")

    assert "DOCKER-USER" in scope
    assert "iptables-nft" in scope
    assert "`FORWARD`-to-`DOCKER-USER`" in scope
    assert "inet openvpn_course_input" in scope
    assert "does not restart Docker" in scope
    assert "after `aws s3 presign`" in share
    assert "another rotate or share operation is already in progress" in share
    assert "another rotate or share operation is already in progress" in rotation
    assert "does not lock `status`, `export`, or `logs`" in rotation
    assert "DOCKER-USER jump" in recovery
    assert "ip openvpn_course_nat" in recovery
