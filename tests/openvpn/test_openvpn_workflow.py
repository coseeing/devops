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
