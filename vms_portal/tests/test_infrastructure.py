from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


class CfnLoader(yaml.SafeLoader):
    pass


CfnLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_scalar(node)
)


def load_cfn(path: Path):
    return yaml.load(path.read_text(), Loader=CfnLoader)


def test_windows_template_applies_management_tag() -> None:
    template = load_cfn(ROOT / "cloudformation/windows-a11y-instance-template.yml")
    tags = template["Resources"]["WindowsInstance"]["Properties"]["Tags"]
    assert {tag["Key"]: tag["Value"] for tag in tags}["VmPortalManaged"] == "true"


def test_access_policy_restricts_mutation_by_tag() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-access-template.yml")
    statements = template["Resources"]["PortalPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    mutation = next(
        item for item in statements if "ec2:StartInstances" in item["Action"]
    )
    assert (
        mutation["Condition"]["StringEquals"]["aws:ResourceTag/VmPortalManaged"]
        == "true"
    )
    assert mutation["Resource"] != "*"
    describe = next(
        item for item in statements if item["Action"] == ["ec2:DescribeInstances"]
    )
    assert describe["Resource"] == "*"


def test_deployment_is_non_root_read_only_and_uses_exact_domain() -> None:
    dockerfile = (ROOT / "vms_portal/Dockerfile").read_text()
    playbook = (ROOT / "ansible_yaml/vms-portal-playbook.yml").read_text()
    traefik = (ROOT / "ansible_yaml/extra/vms-portal.yml").read_text()

    assert "USER app" in dockerfile
    assert "read_only: true" in playbook
    assert "no-new-privileges:true" in playbook
    assert "AUTH_SECRET_ID" in playbook and "secret_data" not in playbook
    assert "vms.coseeing.org" in traefik


def test_deploy_workflow_requires_exact_domain_confirmation() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-vms-portal.yml").read_text()
    )
    deploy = workflow["jobs"]["deploy"]
    assert (
        deploy["if"]
        == "inputs.action == 'deploy' && inputs.confirm_domain == 'vms.coseeing.org'"
    )
