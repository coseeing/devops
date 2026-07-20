# Windows A11y Pipeline — One-Time AWS Console Setup

Do this once before the first build (or again only if the office/VPN CIDR changes,
or you want to rotate the base AMI). Everything here is manual, GUI-driven setup —
the recurring build itself is fully automated by `.github/workflows/build-windows-a11y-ami.yml`.

## 1. Look up the Windows Server 2025 base AMI ID

1. AWS Console → **EC2** → make sure the region selector (top right) is set to **Asia Pacific (Tokyo) ap-northeast-1**.
2. Left sidebar → **AMI Catalog**.
3. Search box → type `Windows Server 2025`.
4. Under **Quick Start AMIs**, find the entry published by **Amazon** named something like
   `Microsoft Windows Server 2025 Full Base` (NOT the "Core" edition — Core has no GUI shell,
   and Chrome/Firefox/NVDA need the full desktop experience).
5. Click it, copy the **AMI ID** shown (e.g. `ami-0123456789abcdef0`).
   - Alternative lookup: **Systems Manager** → **Parameter Store** → search
     `/aws/service/ami-windows-latest/Windows_Server-2025-English-Full-Base` → **Value** tab shows the
     current AMI ID AWS publishes for this edition.
6. Record this value — it becomes the `BASE_AMI_ID` GitHub variable in step 5.

## 2. Create the RDP security group

1. AWS Console → **EC2** → left sidebar → **Security Groups** → **Create security group**.
2. **Security group name**: `windows-a11y-rdp`
3. **Description**: `Allow RDP access to Windows A11y build/verify instances`
4. **VPC**: the default VPC in `ap-northeast-1`.
5. **Inbound rules** → **Add rule**:
   - Type: `RDP` (protocol TCP and port 3389 auto-fill)
   - Source: `Custom` → enter your office/VPN CIDR block (e.g. `203.0.113.0/24`) — do **not** use `0.0.0.0/0`.
   - Description: `Office VPN RDP access`
6. **Outbound rules**: leave the default (all traffic allowed) — the instance needs outbound HTTPS for
   Windows Update, Chocolatey, and the SSM agent.
7. **Tags**: `Name` = `windows-a11y-rdp`.
8. Click **Create security group**. Copy the resulting **Security group ID** (e.g. `sg-0123456789abcdef0`).
9. Record this value — it becomes the `SECURITY_GROUP_ID` GitHub variable in step 5.

If the office CIDR ever changes, edit this security group's inbound rule directly — nothing else
in the pipeline needs to change.

## 3. Create the SSM instance role

1. AWS Console → **IAM** → left sidebar → **Roles** → **Create role**.
2. **Trusted entity type**: `AWS service`.
3. **Use case**: `EC2`.
4. **Permissions policies**: search for and check `AmazonSSMManagedInstanceCore`.
5. **Role name**: `windows-a11y-ssm-instance-role`
6. **Description**: `EC2 instance role granting SSM Run Command access for Windows A11y build/verify instances`
7. Click **Create role**.

Creating an EC2 role through the console automatically creates a matching **instance profile**
with the same name — `windows-a11y-ssm-instance-role` is both the role name and the instance
profile name you'll use. Record it — it becomes the `INSTANCE_PROFILE_NAME` GitHub variable in step 5.

## 4. Extend the GitHub Actions deploy role's permissions

The pipeline assumes the same role every other workflow in this repo already uses
(`secrets.AWS_GITHUB_ACTION_ROLE`). It needs a few more permissions.

1. AWS Console → **IAM** → **Roles** → find the role that backs `AWS_GITHUB_ACTION_ROLE`
   (check the GitHub repo's Actions secret value, or ask whoever manages OIDC federation, for the exact role name).
2. Open it → **Add permissions** → **Create inline policy** → **JSON** tab → paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WindowsA11yEc2Lifecycle",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:StopInstances",
        "ec2:StartInstances",
        "ec2:RebootInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:CreateImage",
        "ec2:DeregisterImage",
        "ec2:DescribeImages",
        "ec2:DescribeSnapshots",
        "ec2:DeleteSnapshot",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WindowsA11yIamPassRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::*:role/windows-a11y-ssm-instance-role"
    },
    {
      "Sid": "WindowsA11ySsm",
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:DescribeInstanceInformation"
      ],
      "Resource": "*"
    },
    {
      "Sid": "WindowsA11ySecretsManager",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:ap-northeast-1:*:secret:windows-a11y/*"
    }
  ]
}
```

3. **Next** → **Policy name**: `windows-a11y-pipeline-policy` → **Create policy**.

## 5. Secrets Manager naming convention (reference only — nothing to create by hand)

The build workflow creates/updates these automatically every run; there is nothing to
provision here. For reference, the naming convention is:

- `windows-a11y/<ami_name>/coseeing`
- `windows-a11y/<ami_name>/user`

Each holds a JSON secret shaped `{"username": "...", "password": "..."}`. The IAM policy in
step 4 already scopes secret creation to the `windows-a11y/*` path. After a build, view them
in **Secrets Manager** console under that prefix.

## 6. Create the GitHub Environment

1. GitHub repo → **Settings** → **Environments** → **New environment** → name it `windows-a11y`.
2. Add these **Variables**:

   | Variable | Value | Where it comes from |
   |---|---|---|
   | `BASE_AMI_ID` | e.g. `ami-0123456789abcdef0` | Step 1 |
   | `SECURITY_GROUP_ID` | e.g. `sg-0123456789abcdef0` | Step 2 |
   | `INSTANCE_PROFILE_NAME` | `windows-a11y-ssm-instance-role` | Step 3 |
   | `INSTANCE_TYPE` | `m5.xlarge` | fixed default |
   | `VERIFY_INSTANCE_TYPE` | `m5.large` | fixed default |
   | `AVAILABILITY_ZONE` | `ap-northeast-1c` | fixed default |
   | `SUBNET_ID` | e.g. `subnet-0123456789abcdef0` | EC2 console → Subnets → filter by the default VPC + Availability Zone `ap-northeast-1c` → copy Subnet ID |
   | `KEY_NAME` | `deploy_key` | reusing the existing key pair already used by `launch-ec2-instance.yml` |

   Note: `AVAILABILITY_ZONE` is not read by the CloudFormation template or the workflow — it exists
   purely as a lookup aid for the operator to pick the correct `SUBNET_ID` above; the AZ actually used
   is whichever one the chosen subnet belongs to.

No environment secrets are needed — the office/VPN CIDR was only needed once, to type into the
security group's inbound rule in step 2.
