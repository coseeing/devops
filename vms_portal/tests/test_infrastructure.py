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


def test_windows_template_defines_twenty_conditional_vm_slots_with_dynamic_ips() -> None:
    template = load_cfn(ROOT / "cloudformation/windows-a11y-instance-template.yml")
    assert template["Parameters"]["InstanceCount"]["AllowedValues"] == [
        str(value) for value in range(1, 21)
    ]

    for index in range(1, 21):
        suffix = f"{index:03d}"
        condition = f"CreateSlot{suffix}"
        instance = template["Resources"][f"WindowsInstance{suffix}"]
        assert condition in template["Conditions"]
        assert instance["Condition"] == condition
        assert (
            instance["Properties"]["NetworkInterfaces"][0][
                "AssociatePublicIpAddress"
            ]
            is True
        )
        assert template["Outputs"][f"InstanceId{suffix}"]["Condition"] == condition
        assert template["Outputs"][f"PrivateIp{suffix}"]["Condition"] == condition
        assert template["Outputs"][f"PublicIp{suffix}"]["Condition"] == condition

    assert all(
        resource["Type"] != "AWS::EC2::EIP"
        for resource in template["Resources"].values()
    )
    assert not any(name.startswith("ElasticIp") for name in template["Outputs"])


def test_windows_batch_resources_have_portal_tags() -> None:
    template = load_cfn(ROOT / "cloudformation/windows-a11y-instance-template.yml")

    for index in range(1, 21):
        suffix = f"{index:03d}"
        instance_tags = {
            tag["Key"]: tag["Value"]
            for tag in template["Resources"][f"WindowsInstance{suffix}"][
                "Properties"
            ]["Tags"]
        }
        expected_common = {
            "Name": f"${{AWS::StackName}}-{suffix}",
            "VmPortalManaged": "true",
            "VmPortalStack": "AWS::StackName",
            "VmPortalInstanceIndex": suffix,
        }
        assert instance_tags == expected_common


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
    assert "BatchCreatedAt" not in create["run"]
    assert "aws cloudformation deploy" not in create["run"]

    publish = next(
        step for step in steps if step.get("name") == "Publish batch connection details"
    )
    assert 'seq -w 1 "${INSTANCE_COUNT}"' in publish["run"]
    assert "InstanceId${SUFFIX}" in publish["run"]
    assert "PrivateIp${SUFFIX}" in publish["run"]
    assert "PublicIp${SUFFIX}" in publish["run"]
    assert "| Name | Instance ID | Private IP | Current public IP |" in publish["run"]


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


def test_scheduler_stops_managed_windows_vms_at_one_am_taipei() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-access-template.yml")
    schedule = template["Resources"]["NightlyShutdownSchedule"]
    assert schedule["Type"] == "AWS::Scheduler::Schedule"
    assert schedule["Properties"]["ScheduleExpression"] == "cron(0 1 * * ? *)"
    assert schedule["Properties"]["ScheduleExpressionTimezone"] == "Asia/Taipei"
    assert schedule["Properties"]["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert schedule["Properties"]["Target"]["RetryPolicy"]["MaximumRetryAttempts"] > 0
    assert "DeadLetterConfig" in schedule["Properties"]["Target"]

    policy = template["Resources"]["ShutdownLambdaPolicy"]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    stop = next(item for item in policy if "ec2:StopInstances" in item["Action"])
    assert stop["Condition"]["StringEquals"][
        "aws:ResourceTag/VmPortalManaged"
    ] == "true"
    describe = next(item for item in policy if item["Action"] == ["ec2:DescribeInstances"])
    assert describe["Resource"] == "*"


def test_deploy_workflow_uploads_versioned_lambda_before_access_stack() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-vms-portal.yml").read_text()
    )
    steps = workflow["jobs"]["deploy"]["steps"]
    prepare = next(step for step in steps if step.get("name") == "Prepare AWS infrastructure")
    run = prepare["run"]
    assert "vms-portal-foundation-template.yml" in run
    assert "lambda/windows_vm_shutdown/lambda_function.py" in run
    assert "aws s3api put-object" in run
    assert "ShutdownCodeS3Version=" in run
    assert run.index("vms-portal-foundation-template.yml") < run.index(
        "vms-portal-access-template.yml"
    )


def test_portal_policy_reads_cur_through_athena_but_cannot_provision() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-access-template.yml")
    statements = template["Resources"]["PortalPolicy"]["Properties"]["PolicyDocument"][
        "Statement"
    ]
    actions = {
        action for statement in statements for action in statement.get("Action", [])
    }
    assert {
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetQueryResults",
        "glue:GetDatabase",
        "glue:GetTable",
        "s3:GetObject",
        "s3:PutObject",
    }.issubset(actions)
    assert actions.isdisjoint(
        {
            "ce:GetCostAndUsageWithResources",
            "ec2:DescribeAddresses",
            "ec2:RunInstances",
            "ec2:AllocateAddress",
            "ec2:AssociateAddress",
            "ec2:ReleaseAddress",
            "cloudformation:CreateStack",
            "cloudformation:DeleteStack",
            "iam:PassRole",
        }
    )


def test_foundation_defines_cur2_parquet_glue_projection_and_athena_limits() -> None:
    template = load_cfn(ROOT / "cloudformation/vms-portal-foundation-template.yml")
    resources = template["Resources"]

    bucket = resources["CostDataBucket"]["Properties"]
    assert bucket["BucketEncryption"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())

    export = resources["PortalCurExport"]["Properties"]["Export"]
    query = export["DataQuery"]
    assert "line_item_resource_id" in query["QueryStatement"]
    assert query["TableConfigurations"]["COST_AND_USAGE_REPORT"][
        "INCLUDE_RESOURCES"
    ] == "TRUE"
    destination = export["DestinationConfigurations"]["S3Destination"]
    assert destination["S3OutputConfigurations"] == {
        "Compression": "PARQUET",
        "Format": "PARQUET",
        "OutputType": "CUSTOM",
        "Overwrite": "OVERWRITE_REPORT",
    }
    assert export["RefreshCadence"] == {"Frequency": "SYNCHRONOUS"}

    table = resources["CostTable"]["Properties"]["TableInput"]
    assert table["Parameters"]["projection.enabled"] == "true"
    assert table["Parameters"]["projection.billing_period.type"] == "date"
    assert "${!billing_period}" in table["Parameters"]["storage.location.template"]
    assert resources["CostWorkGroup"]["Properties"]["WorkGroupConfiguration"][
        "BytesScannedCutoffPerQuery"
    ] == 1073741824
    assert "Crawler" not in " ".join(resources)


def test_deploy_workflow_passes_foundation_cost_outputs_to_access_stack() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-vms-portal.yml").read_text()
    )
    prepare = next(
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Prepare AWS infrastructure"
    )["run"]
    for value in (
        "CostDatabaseName",
        "CostTableName",
        "CostWorkGroupName",
        "CostDataBucketName",
        "CostQueryResultsPrefix",
    ):
        assert value in prepare
    assert "CostDatabaseName=" in prepare


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
    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile
    assert "read_only: true" in playbook
    assert "no-new-privileges:true" in playbook
    assert "AUTH_SECRET_ID" in playbook and "secret_data" not in playbook
    assert "ASSIGNMENTS_DB_PATH=/data/vms-portal/data/portal.db" in playbook
    assert "path: /data/vms-portal/data" in playbook
    assert "owner: 10001" in playbook
    assert "- /data/vms-portal/data:/data/vms-portal/data" in playbook
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
