# Windows VM Portal 部署 SOP

本 SOP 部署 `https://vms.coseeing.org`，AWS Region 固定為 `ap-northeast-1`。日常部署由 GitHub Actions 完成；只有帳密、GitHub/AWS 信任關係與 DNS 需要第一次手動設定。

## A. 第一次部署前：一次性設定

### 1. 建立登入帳密 Secret

在可信任電腦產生 JSON。密碼輸入不會回顯，JSON 只包含 Argon2id 雜湊及隨機 session key：

```bash
cd vms_portal
uv sync --frozen
uv run vms-portal-secret
```

不要把密碼或輸出的 JSON 貼到 GitHub issue、PR、workflow input 或 repository secret。到 AWS Console：

1. 開啟 **Secrets Manager** → **Store a new secret**。
2. 選 **Other type of secret**，切換到 **Plaintext**。
3. 貼上完整 JSON。
4. Secret name 填 `prod/vms-portal/auth`，完成建立。

日後輪替帳密時重新產生 JSON，將 `auth_version` 加一，再到同一個 Secret 建立新版本。Portal 最多五分鐘載入新版，舊 session 會失效，不必重新部署。

### 2. 確認 GitHub production environment

Repository 的 environment `a11y-village-production` 必須已有：

- `AWS_GITHUB_ACTION_ROLE`：GitHub OIDC 使用的 AWS role ARN。
- `EC2_SSH_KEY`：登入既有 Linux EC2 的 SSH private key。

OIDC role 必須允許 workflow 執行下列範圍：

- 查詢 `prod/vms-portal/auth` 的 ARN。
- 建立/查詢 ECR repository `vms-portal` 並 push image。
- 在 `ap-northeast-1` 建立或更新 CloudFormation stack `vms-portal-foundation` 與 `vms-portal-access`，並在 BCM Data Exports 唯一支援的 `us-east-1` 建立 `vms-portal-cur-export`。
- 管理受限範圍的 BCM Data Exports、S3、Glue、Athena、Lambda、SQS 與 EventBridge Scheduler 資源，並將 Lambda code artifact 上傳至 foundation stack 建立的 versioned bucket。
- BCM export bootstrap 需包含 `bcm-data-exports:*` 對 CUR table/export ARN、`cur:PutReportDefinition`，以及只對本 workflow 建立之 Lambda/Scheduler roles 的 `iam:PassRole`；不要給 Portal runtime 這些管理權限。
- 對 role `coseeing-ec2-common` 管理 inline policy `vms-portal-runtime`。
- 建立 `/coseeing/vms-portal` log group。
- 查詢既有 Linux EC2 stack，並設定該 instance 的 IMDSv2 metadata options。

這是 bootstrap 的權限；Portal container 本身只使用 `cloudformation/vms-portal-access-template.yml` 內的受限 runtime policy。

### 3. 確認 Linux EC2 instance profile 權限

承載 Portal/Traefik 的 Linux EC2 必須綁定一個包含 IAM role `coseeing-ec2-common` 的 instance profile。`cloudformation/common-ec2-instance-template.yml` 建立的 EC2 已經使用此 role；若是其他既有 EC2，請到 **EC2** → **Instances** → 選擇該 instance → **Actions** → **Security** → **Modify IAM role** 確認。

第一次執行 `deploy` workflow 時，Action 會建立或更新 CloudFormation stack `vms-portal-access`，並自動將 inline policy `vms-portal-runtime` 掛到 `coseeing-ec2-common`。不需要手動貼 IAM JSON。該 policy 只包含 Portal 特有權限：

| AWS 權限 | Resource 限制 | 用途 |
| --- | --- | --- |
| `ec2:DescribeInstances` | `*` | admin 列出 VM、user 依 Instance ID 查詢，以及每次開關機前重新驗證 tag/狀態。此 API 不支援限制到單一 instance ARN。 |
| `ec2:StartInstances`、`ec2:StopInstances` | 本帳號、本 Region 的 EC2 instance ARN，另要求 `VmPortalManaged=true` | 只允許控制明確交由 Portal 管理的 Windows VM。 |
| `secretsmanager:DescribeSecret`、`secretsmanager:GetSecretValue` | 建立部署時指定的單一 Secret ARN | 啟動與定期更新共用登入帳密；Secret 只保留於記憶體 cache。 |
| Athena query/read | 單一 `vms-portal-costs` workgroup | 執行 Portal 的批次 60 天成本查詢並讀取結果。 |
| Glue `GetDatabase`、`GetTable` | 單一 cost database/table 及 catalog | 解析固定 CUR 2.0 Parquet schema；不使用 crawler。 |
| S3 list/read/write | CUR data prefix 只讀、Athena result prefix 讀寫 | 讀取 Data Export 並保存短期查詢結果；不能管理 bucket 或 export。 |
| `logs:CreateLogStream`、`logs:PutLogEvents` | `/coseeing/vms-portal` log group 內的 stream | Docker `awslogs` driver 寫入登入及開關機 audit log。 |

同一台 EC2 上的既有服務已經從 private ECR 拉取 image，因此 `coseeing-ec2-common` 的共用基礎 policy 應已具備 `ecr:GetAuthorizationToken`、`ecr:BatchCheckLayerAvailability`、`ecr:GetDownloadUrlForLayer` 與 `ecr:BatchGetImage`。這些權限不是 Portal 特有權限，不由 `vms-portal-access` stack 重複管理。可登入該 EC2 驗證目前 instance profile 是否仍能取得 ECR token：

```bash
aws ecr get-login-password --region ap-northeast-1 >/dev/null \
  && echo "ECR authentication OK"
```

若上式成功，Portal 可沿用既有 ECR pull 權限；若失敗，應修正 EC2 的共用 ECR read policy，而不是把 ECR 權限加入 `vms-portal-runtime`。共用 policy 應限制 image read 到實際使用的 repositories；只有 `ecr:GetAuthorizationToken` 因 AWS API 限制使用 `Resource: "*"`。

Linux EC2 不需要 `ec2:*`、`iam:*`、`secretsmanager:*` 或 ECR push 權限。建立 ECR、push image、部署 CloudFormation 與修改 metadata options 是 GitHub OIDC role 的 bootstrap 工作，不是 EC2 runtime role 的權限。

部署後可在 AWS Console 的 **IAM** → **Roles** → `coseeing-ec2-common` → **Permissions** 確認存在 `vms-portal-runtime`。若 role 名稱不同，需先調整 workflow 的 `RUNTIME_ROLE_NAME`，不要額外建立一份過度寬鬆的 policy。

### 4. DNS、Windows tag 與 CUR 2.0

- DNS：將 `vms.coseeing.org` 的 A/AAAA 記錄指向既有 Traefik Linux EC2。
- Windows VM：新版 `windows-a11y-instance-template.yml` 已自動加入 `VmPortalManaged=true`。既有 VM 必須補上相同 tag，否則 Portal 不會顯示或控制它。
- CUR 2.0：deploy workflow 先在 Tokyo 建立加密、封鎖公開存取的 cost bucket、固定 Glue schema 與受 scan limit 保護的 Athena workgroup，再從 `us-east-1` stack 建立 resource-ID Data Export，最後更新 Portal runtime IAM。首次報表通常需等待最多 24 小時；若帳號沒有可用的舊月份檔案，近 60 天畫面會明確顯示實際可用期間。需要補舊資料時由 payer/management account 向 AWS Support 申請 backfill。

Portal 不再使用 Cost Explorer 的 14 天 resource API，也不估算 EIP 成本。CUR query 對每個 instance 使用 Savings Plan effective cost、RI effective cost 或 unblended cost 的適用值，並區分數字 `0`、報表尚未準備及查詢失敗。

以上三項無法由本 repository 的 deploy workflow 安全代辦。

## B. 日常部署：全部在 GitHub Actions

1. GitHub → **Actions** → **Validate or deploy Windows VM portal** → **Run workflow**。
2. 先選 `validate` 執行測試及 Docker build。
3. 通過後再次 Run workflow，選 `deploy`：
   - `stack_name`：通常保留 `coseeing-stack-v2`。
   - `auth_secret_id`：通常保留 `prod/vms-portal/auth`。
   - `confirm_domain`：完整輸入 `vms.coseeing.org`。
4. 等待 workflow 完成，從 GitHub Actions summary 查看 URL、image tag、Linux instance ID 與 Secret 名稱。

Deploy job 會自動完成：

- 建立 ECR repository（若不存在）。
- 依序部署 foundation、上傳 versioned shutdown Lambda、再部署 runtime IAM/Scheduler；任一步失敗都不會更新 Portal container。
- 在 `us-east-1` 部署 CUR 2.0 export；S3、Glue、Athena、Lambda、Scheduler 與 Portal 仍位於 `ap-northeast-1`。
- 將 Linux EC2 設為 `HttpTokens=required`、hop limit `2`。
- 以 Git commit SHA 作為 immutable image tag，build/push Docker image。
- 透過 Ansible 更新 Portal/Traefik。
- 檢查 `/health/live` 與 `/health/ready`。

同一 commit 重新執行時會重用既有 immutable image，不會覆寫 tag。

## C. 部署後人工驗收

1. 開啟 `https://vms.coseeing.org`。
2. 使用 admin 登入：只應列出有 `VmPortalManaged=true` 的 VM，並看到 Name、Instance ID、assignment、private IP、目前 public IP、狀態、60 天成本與開關機控制。
3. 更新一筆 assignment、重新部署 Portal，再確認資料仍存在。assignment 只是紀錄「這台給誰」，不會改變登入或操作權限。
4. 使用 user 登入：不應直接出現清單；輸入完整 Instance ID 後，只能看到該 VM 的 Name、Instance ID、private IP、狀態、60 天成本與開關機控制，不應看到 assignment 或 public IP。
5. 第一次開關機前，人工確認完整 Instance ID、private IP、目前狀態與預定動作。public IP 是動態值，stop/start 後可能改變。
6. 成本有三種明確狀態：數值（包括 `0`）、`成本報表尚未準備完成`、`成本查詢失敗`。有資料時同時顯示實際可用期間；首次 CUR delivery 最多可能等待 24 小時。
7. 在 EventBridge Scheduler 確認 `vms-portal-nightly-shutdown` 類型的 schedule 為 enabled、timezone 是 `Asia/Taipei`，並在 01:00 後確認所有 running 且 `VmPortalManaged=true` 的 VM 進入 stopping/stopped。此機制只停機，不會自動開機。

Assignment SQLite 位於 host 的 `/data/vms-portal/data/portal.db`，只有這個目錄以 writable bind mount 掛入 read-only container。備份前先在 `/data/vms-portal` 執行 `docker compose stop vms-portal`，複製 `data/portal.db` 到受控備份位置，再執行 `docker compose start vms-portal`，避免複製進行中的 SQLite transaction。刪除 VM 時不會自動刪除 assignment record；Admin 清單只 join 目前仍存在的 VM。

成本機制本身（小量 S3、Athena query、Glue catalog、Scheduler/Lambda）預估約 `0.02–0.10 USD/月`，不含 Windows EC2、EBS、資料傳輸與 public IPv4。每台 VM 有 public IPv4 時另依 AWS public IPv4 單價計費；以 `0.005 USD/小時` 且整月持有估算約 `3.60 USD/台/月`，實際金額以當期 AWS 帳單與價格為準。每天 01:00 自動停機可停止 EC2 compute 累計；非 EIP 的動態 public IPv4 會在 stop 時釋放，但 EBS 仍會繼續計費。

## D. Rollback 與故障排除

Rollback：checkout 上一個已知正常 commit，從該 commit 手動執行同一個 `deploy` workflow。每個 commit SHA 都對應 immutable image。停止 Portal container 不會改變 Windows VM 狀態。

若需立即停用控制能力，先從 `coseeing-ec2-common` 移除 `vms-portal-runtime` inline policy，再查看 `/coseeing/vms-portal` CloudWatch log。

- readiness 503：檢查 Secret JSON schema、instance role 與 IMDSv2 hop limit。
- VM 不出現：檢查 Region、Instance ID 與 `VmPortalManaged=true`。
- AccessDenied：以 CloudTrail request ID 確認缺少的動作，不要擴大成 `ec2:*`。
- 成本顯示尚未準備：確認 `vms-portal-cur-export` 位於 `us-east-1`、Data Export execution 成功，並等待首次 S3 delivery（通常最多 24 小時）。需要近兩個月舊資料時向 AWS Support 詢問 backfill。
- 成本查詢失敗：從 Portal log 取得 error class、AWS request ID 與 Athena query execution ID，再檢查 Tokyo Glue table、Athena workgroup、S3 prefixes 與 runtime IAM；不要把錯誤改顯示成 `0`。
- 每日關機未執行：檢查 Scheduler execution role、Lambda log、SQS DLQ 與 VM 的 `VmPortalManaged=true` tag。
- 既有 `i-021a0b068258c64d5` 沒有 public IP，且不會被 template 更新自動重建；需要直接 IPv4 外網時，請用 Windows batch workflow 重建。
- 帳密輪替未生效：確認 `auth_version` 已增加且 Secret stage 是 `AWSCURRENT`。
- Traefik 502：檢查 container health、`entry` network 與 `/data/entry/traefik.yml`。
