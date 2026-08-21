# Windows VM Portal 部署 SOP

本 SOP 部署 `https://vms.coseeing.org`，AWS Region 固定為 `ap-northeast-1`。日常部署由 GitHub Actions 完成；只有帳密、GitHub/AWS 信任關係、DNS 與 Cost Explorer 需要第一次手動設定。

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
- 建立或更新 CloudFormation stack `vms-portal-access`。
- 對 role `coseeing-ec2-common` 管理 inline policy `vms-portal-runtime`。
- 建立 `/coseeing/vms-portal` log group。
- 查詢既有 Linux EC2 stack，並設定該 instance 的 IMDSv2 metadata options。

這是 bootstrap 的權限；Portal container 本身只使用 `cloudformation/vms-portal-access-template.yml` 內的受限 runtime policy。

### 3. DNS、Windows tag 與 Cost Explorer

- DNS：將 `vms.coseeing.org` 的 A/AAAA 記錄指向既有 Traefik Linux EC2。
- Windows VM：新版 `windows-a11y-instance-template.yml` 已自動加入 `VmPortalManaged=true`。既有 VM 必須補上相同 tag，否則 Portal 不會顯示或控制它。
- Cost Explorer：用 payer/management account 開啟 **Billing and Cost Management** → **Cost Management preferences** → **Granular data**，啟用 EC2 resource-level data。資料只涵蓋最近 14 天，可能需等待 48 小時，且 granular data/API request 可能產生費用。

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
- 部署 runtime IAM policy 與 CloudWatch log group。
- 將 Linux EC2 設為 `HttpTokens=required`、hop limit `2`。
- 以 Git commit SHA 作為 immutable image tag，build/push Docker image。
- 透過 Ansible 更新 Portal/Traefik。
- 檢查 `/health/live` 與 `/health/ready`。

同一 commit 重新執行時會重用既有 immutable image，不會覆寫 tag。

## C. 部署後人工驗收

1. 開啟 `https://vms.coseeing.org`。
2. 使用 admin 登入：只應列出有 `VmPortalManaged=true` 的 VM。
3. 使用 user 登入：不應直接出現清單；輸入已知 Public IPv4 後才能看到該 VM。
4. 第一次開關機前，人工確認完整 instance ID、Public IPv4、目前狀態與預定動作。
5. Cost Explorer 尚未準備完成時，畫面顯示「成本資料尚未提供」屬正常情況。

## D. Rollback 與故障排除

Rollback：checkout 上一個已知正常 commit，從該 commit 手動執行同一個 `deploy` workflow。每個 commit SHA 都對應 immutable image。停止 Portal container 不會改變 Windows VM 狀態。

若需立即停用控制能力，先從 `coseeing-ec2-common` 移除 `vms-portal-runtime` inline policy，再查看 `/coseeing/vms-portal` CloudWatch log。

- readiness 503：檢查 Secret JSON schema、instance role 與 IMDSv2 hop limit。
- VM 不出現：檢查 Region、Public IPv4 與 `VmPortalManaged=true`。
- AccessDenied：以 CloudTrail request ID 確認缺少的動作，不要擴大成 `ec2:*`。
- 成本空白：確認 granular data 已啟用並等待最多 48 小時。
- 帳密輪替未生效：確認 `auth_version` 已增加且 Secret stage 是 `AWSCURRENT`。
- Traefik 502：檢查 container health、`entry` network 與 `/data/entry/traefik.yml`。
