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


def test_windows_template_defines_twenty_conditional_vm_eip_slots() -> None:
    template = load_cfn(ROOT / "cloudformation/windows-a11y-instance-template.yml")
    assert template["Parameters"]["InstanceCount"]["AllowedValues"] == [
        str(value) for value in range(1, 21)
    ]

    for index in range(1, 21):
        suffix = f"{index:03d}"
        condition = f"CreateSlot{suffix}"
        instance = template["Resources"][f"WindowsInstance{suffix}"]
        eip = template["Resources"][f"ElasticIp{suffix}"]

        assert condition in template["Conditions"]
        assert instance["Condition"] == condition
        assert eip["Condition"] == condition
        assert (
            instance["Properties"]["NetworkInterfaces"][0][
                "AssociatePublicIpAddress"
            ]
            is False
        )
        assert eip["Properties"]["InstanceId"] == f"WindowsInstance{suffix}"
        assert template["Outputs"][f"InstanceId{suffix}"]["Condition"] == condition
        assert template["Outputs"][f"ElasticIp{suffix}"]["Condition"] == condition


def test_windows_batch_resources_have_portal_and_cost_tags() -> None:
    template = load_cfn(ROOT / "cloudformation/windows-a11y-instance-template.yml")

    for index in range(1, 21):
        suffix = f"{index:03d}"
        instance_tags = {
            tag["Key"]: tag["Value"]
            for tag in template["Resources"][f"WindowsInstance{suffix}"][
                "Properties"
            ]["Tags"]
        }
        eip_tags = {
            tag["Key"]: tag["Value"]
            for tag in template["Resources"][f"ElasticIp{suffix}"]["Properties"][
                "Tags"
            ]
        }

        expected_common = {
            "Name": f"${{AWS::StackName}}-{suffix}",
            "VmPortalManaged": "true",
            "VmPortalStack": "AWS::StackName",
            "VmPortalInstanceIndex": suffix,
        }
        assert instance_tags == expected_common
        assert eip_tags == {**expected_common, "VmPortalCreatedAt": "BatchCreatedAt"}


def test_windows_launch_workflow_uses_batch_count_and_latest_managed_ami() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/launch-windows-a11y-ec2.yml").read_text()
    )
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["instance_count"]["default"] == "1"
    assert "ami_name" not in inputs

    steps = workflow["jobs"]["launch"]["steps"]
    find_ami = next(step for step in steps if step.get("id") == "ami")
    assert find_ami["env"]["AMI_NAME"] == "windows-a11y-*"
    assert "reverse(sort_by(Images, &CreationDate))[0].ImageId" in find_ami["run"]


def test_windows_launch_workflow_creates_atomic_batch_and_lists_all_ips() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/launch-windows-a11y-ec2.yml").read_text()
    )
    steps = workflow["jobs"]["launch"]["steps"]
    create = next(step for step in steps if step.get("name") == "Create VM batch stack")
    assert "aws cloudformation create-stack" in create["run"]
    assert "--on-failure DELETE" in create["run"]
    assert 'ParameterKey=InstanceCount,ParameterValue="${INSTANCE_COUNT}"' in create["run"]
    assert 'ParameterKey=BatchCreatedAt,ParameterValue="${BATCH_CREATED_AT}"' in create["run"]
    assert "aws cloudformation deploy" not in create["run"]

    publish = next(
        step for step in steps if step.get("name") == "Publish batch connection details"
    )
    assert 'seq -w 1 "${INSTANCE_COUNT}"' in publish["run"]
    assert "InstanceId${SUFFIX}" in publish["run"]
    assert "ElasticIp${SUFFIX}" in publish["run"]
    assert "| Name | Instance ID | Elastic IP |" in publish["run"]


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


def test_portal_policy_can_describe_eips_but_cannot_provision_resources() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-access-template.yml")
    statements = template["Resources"]["PortalPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    describe_addresses = next(
        item for item in statements if item["Action"] == ["ec2:DescribeAddresses"]
    )
    assert describe_addresses["Resource"] == "*"

    actions = {
        action for statement in statements for action in statement.get("Action", [])
    }
    assert actions.isdisjoint(
        {
            "ec2:RunInstances",
            "ec2:AllocateAddress",
            "ec2:AssociateAddress",
            "ec2:ReleaseAddress",
            "cloudformation:CreateStack",
            "cloudformation:DeleteStack",
            "iam:PassRole",
        }
    )


def test_portal_runtime_policy_does_not_duplicate_shared_ecr_access() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-access-template.yml")
    statements = template["Resources"]["PortalPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    actions = {
        action
        for statement in statements
        for action in statement["Action"]
    }

    assert not any(action.startswith("ecr:") for action in actions)


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


def test_deploy_workflow_derives_immutable_image_tag_and_defaults_secret() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-vms-portal.yml").read_text()
    )
    inputs = workflow[True]["workflow_dispatch"]["inputs"]

    assert "image_tag" not in inputs
    assert inputs["auth_secret_id"]["default"] == "prod/vms-portal/auth"
    assert workflow["jobs"]["deploy"]["env"]["IMAGE_TAG"] == "${{ github.sha }}"


def test_deploy_workflow_bootstraps_aws_and_checks_health() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-vms-portal.yml").read_text()
    )
    deploy = workflow["jobs"]["deploy"]
    steps = {step.get("name"): step for step in deploy["steps"]}

    prepare = steps["Prepare AWS infrastructure"]["run"]
    assert "aws secretsmanager describe-secret" in prepare
    assert "aws ecr create-repository" in prepare
    assert "aws cloudformation deploy" in prepare
    assert "aws ec2 modify-instance-metadata-options" in prepare

    verify = steps["Verify portal health"]["run"]
    assert "https://vms.coseeing.org/health/live" in verify
    assert "https://vms.coseeing.org/health/ready" in verify
