from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_workflow():
    return yaml.safe_load(
        (ROOT / ".github/workflows/deploy-openvpn.yml").read_text()
    )


def test_workflow_has_validate_and_exactly_confirmed_deploy_jobs() -> None:
    workflow = load_workflow()
    assert set(workflow["jobs"]) == {"validate", "deploy"}
    assert workflow["jobs"]["deploy"]["if"] == (
        "inputs.action == 'deploy' && inputs.confirm_endpoint == inputs.vpn_endpoint"
    )
    assert workflow["jobs"]["deploy"]["environment"] == "a11y-village-production"


def test_workflow_discovers_networks_without_modifying_security_groups() -> None:
    workflow_text = (
        ROOT / ".github/workflows/deploy-openvpn.yml"
    ).read_text()
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
    assert "--no-fail-on-empty-changeset" in deploy_steps[
        "Deploy profile distribution"
    ]["run"]
    assert deploy_steps["Deploy OpenVPN with Ansible"]["env"][
        "ANSIBLE_HOST_KEY_CHECKING"
    ] == "False"
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
    assert "-S \"$active_course_chain\"" in remote_run
    assert "nft list table inet openvpn_course_input" in remote_run
    assert "nft list table ip openvpn_course_nat" in remote_run


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
