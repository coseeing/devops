# Windows VM Portal 部署 SOP

本 SOP 部署 `https://vms.coseeing.org`。Region 固定為 `ap-northeast-1`。先完成唯讀驗證；第一次真實開關機前需再次確認 instance ID、Public IPv4 與狀態。

## 1. 建立共用帳密 secret

在可信任電腦執行；提示輸入不會回顯，輸出只有 Argon2id 雜湊：

```bash
cd vms_portal
uv sync --frozen
uv run vms-portal-secret > /tmp/vms-portal-auth.json
aws secretsmanager create-secret --region ap-northeast-1 --name prod/vms-portal/auth --secret-string file:///tmp/vms-portal-auth.json
```

完成後安全刪除暫存檔。輪替時重新產生 JSON，把現有 `auth_version` 加一，再執行 `aws secretsmanager put-secret-value`。後端最多五分鐘取得新版，所有舊 session 隨即失效。

## 2. 建立 runtime IAM policy 與 log group

取得 secret ARN，然後部署：

```bash
aws cloudformation deploy --region ap-northeast-1 --stack-name vms-portal-access --template-file cloudformation/vms-portal-access-template.yml --capabilities CAPABILITY_NAMED_IAM --parameter-overrides ExistingRoleName=coseeing-ec2-common AuthSecretArn=SECRET_ARN LogRetentionDays=90
```

此 policy 只允許 Describe、讀取指定 secret、查詢 resource cost、寫入指定 log group，以及對 `VmPortalManaged=true` 的 instance 執行 Start/Stop。

## 3. 確認 Docker 可以取得 instance profile

先查 Linux EC2 metadata options：

```bash
aws ec2 describe-instances --region ap-northeast-1 --instance-ids LINUX_INSTANCE_ID --query 'Reservations[0].Instances[0].MetadataOptions'
```

必須為 `HttpTokens=required` 且 `HttpPutResponseHopLimit=2`。若 hop limit 不是 2，在變更前確認 instance ID，再執行：

```bash
aws ec2 modify-instance-metadata-options --region ap-northeast-1 --instance-id LINUX_INSTANCE_ID --http-tokens required --http-put-response-hop-limit 2
```

## 4. 為 Windows VM 補 management tag

新版 `windows-a11y-instance-template.yml` 會自動加入 tag。對每個既有 stack 先建立 change set 或執行 deploy，確認變更只有 Tags；CloudFormation 文件將此更新列為 No interruption。部署後驗證：

```bash
aws ec2 describe-instances --region ap-northeast-1 --filters Name=tag:VmPortalManaged,Values=true --query 'Reservations[].Instances[].[InstanceId,PublicIpAddress,State.Name]'
```

## 5. 啟用 Cost Explorer 單機資料

使用 payer/management account 開啟 Billing and Cost Management → Cost Management preferences → Granular data，啟用 EC2 resource-level data。資料只涵蓋最近 14 天，準備可能需要 48 小時，且 granular data 與 API request 可能產生費用。

## 6. DNS 與 GitHub 設定

將 `vms.coseeing.org` 的 A/AAAA 記錄指向既有 Traefik 主機。確認 GitHub environment `a11y-village-production` 已有 `AWS_GITHUB_ACTION_ROLE` 與 `EC2_SSH_KEY`，OIDC role 另需 ECR push、DescribeStacks 與部署所需權限。ECR repository `vms-portal` 必須已建立。

## 7. 驗證與部署

先在 Actions 執行 `Validate or deploy Windows VM portal`，選 `validate`。通過後重跑並選 `deploy`，填入 immutable `image_tag`、secret ID，且 `confirm_domain` 必須完整輸入 `vms.coseeing.org`。

部署後只做唯讀檢查：

```bash
curl --fail https://vms.coseeing.org/health/live
curl --fail https://vms.coseeing.org/health/ready
aws logs tail /coseeing/vms-portal --region ap-northeast-1 --since 10m
```

登入 admin 確認只顯示有 tag 的 VM；登入 user 確認不顯示清單，輸入已知 Public IPv4 才出現 VM。成本尚未準備時應顯示「成本資料尚未提供」，不影響查詢。

## 8. 第一次開關機與 rollback

第一次操作前記錄並再次確認完整 instance ID、Public IPv4、目前狀態與預定動作。未取得確認不要操作。

rollback 時把 workflow 的 `image_tag` 改為上一個已知正常 tag 並重新部署。停止 portal container 不會改變任何 Windows VM 狀態。若需立即停用控制能力，先移除 `vms-portal-runtime` policy，再調查 CloudWatch audit log。

## 9. 故障排除

- readiness 503：檢查 secret ARN、instance role、IMDSv2 hop limit 與 secret JSON schema。
- VM 不出現：檢查 Region、Public IPv4 與 `VmPortalManaged=true`。
- AccessDenied：用 CloudTrail request ID 對照 IAM；不要擴大成 `ec2:*`。
- 成本空白：確認已啟用 granular data並等待最多 48 小時。
- 登入輪替未生效：確認 `auth_version` 已增加且 secret stage 是 `AWSCURRENT`。
- Traefik 502：檢查 container health、`entry` network 與 `/data/entry/traefik.yml`。
