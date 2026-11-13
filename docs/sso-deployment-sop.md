# SSO 部署與切換 SOP

本 SOP 適用於 `coseeing/SSO` 的 Kratos、Hydra、Kratos schemas 與 center，部署目標固定為 AWS 帳號 `622913514517`、Region `ap-northeast-1`。prod 使用 `sso.coseeing.org` 與 `/data/sso`；dev 使用 `sso.dev.coseeing.org` 與 `/data/sso-dev`。兩套環境可在 `coseeing-stack-v2` 的同一台主機並存。

部署分成兩段：SSO repo 的 `upload-to-ecr` workflow 建置五個映像檔，devops repo 的 `Run Ansible` workflow 再把指定環境更新到主機。任何 AWS 建置、資料搬遷、DNS 切換或正式部署都必須由有權限的維運者執行；本 repository 不會自動完成這些一次性動作。

## A. 一次性人工設定

### 1. 建立五個 ECR repositories

在 `ap-northeast-1` 建立下列 private repositories。名稱不可加環境後綴；環境由可覆蓋的 `prod`、`dev` tag 區分。

- `sso-kratos`
- `sso-hydra`
- `sso-kratos-schemas`
- `sso-oathkeeper`
- `sso-center`

SSO workflow 的 OIDC role 需要這五個 repository 的 push 權限。承載 SSO 的 EC2 instance role 只需要 ECR pull 權限，不應取得 push 或 repository 管理權限。

### 2. 建立 RDS databases 與專用帳號

在 `coseeing-main` 上建立 prod 與 dev 各兩個 database、四個互不共用的帳號。請在受控的 MySQL client 中把下列 `<...>` 換成實際高強度密碼；不要把密碼放進 shell history、issue 或 PR。

```sql
CREATE DATABASE kratos CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE hydra CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE kratos_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE hydra_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'sso_kratos_prod'@'%' IDENTIFIED BY '<KRATOS_PROD_DB_PASSWORD>';
CREATE USER 'sso_hydra_prod'@'%' IDENTIFIED BY '<HYDRA_PROD_DB_PASSWORD>';
CREATE USER 'sso_kratos_dev'@'%' IDENTIFIED BY '<KRATOS_DEV_DB_PASSWORD>';
CREATE USER 'sso_hydra_dev'@'%' IDENTIFIED BY '<HYDRA_DEV_DB_PASSWORD>';

GRANT ALL PRIVILEGES ON kratos.* TO 'sso_kratos_prod'@'%';
GRANT ALL PRIVILEGES ON hydra.* TO 'sso_hydra_prod'@'%';
GRANT ALL PRIVILEGES ON kratos_dev.* TO 'sso_kratos_dev'@'%';
GRANT ALL PRIVILEGES ON hydra_dev.* TO 'sso_hydra_dev'@'%';
```

每個帳號只能存取自己的 database。確認 `coseeing-stack-v2` EC2 的 security group 可連到 RDS security group 的 TCP 3306；不要把 RDS 3306 開放給公網。

### 3. 建立 Secrets Manager secrets

在 Secrets Manager 建立兩個 **Plaintext JSON** secret：`prod/sso` 與 `dev/sso`。兩者的 key 相同，但值必須獨立；只有從舊 prod 搬遷的 Hydra/Kratos 加密 secret 例外，切換時須先原值保留。

| Key | 必要 | 用途 |
| --- | --- | --- |
| `HYDRA_DSN` | 是 | Hydra RDS DSN |
| `KRATOS_DSN` | 是 | Kratos RDS DSN |
| `HYDRA_SYSTEM_SECRET` | 是 | Hydra 資料加解密；prod 切換時先沿用舊值 |
| `KRATOS_COOKIE_SECRET` | 是 | Kratos cookie 驗證；prod 切換時先沿用舊值 |
| `KRATOS_CIPHER_SECRET` | 是 | Kratos 資料加解密，長度必須為 32；prod 切換時先沿用舊值 |
| `KRATOS_GOOGLE_CLIENT_ID` | 是 | Google OIDC client ID |
| `KRATOS_GOOGLE_CLIENT_SECRET` | 是 | Google OIDC client secret |
| `KRATOS_GMAIL_ADDRESS` | 是 | Kratos courier 寄件帳號 |
| `KRATOS_GMAIL_APP_PASSWORD` | 是 | Gmail app password |
| `OATHKEEPER_SHARED_SECRET` | 是 | Oathkeeper header mutator 的共享 secret（`scripts/generate-secrets.sh` 可產生） |
| `TRUSTED_CONSENT_CLIENT_IDS` | 是 | 允許自動 consent 的 client ID 清單 |
| `HYDRA_SYSTEM_SECRET_OLD` | 否 | 輪換後仍須解密舊 Hydra 資料的前一個值 |
| `KRATOS_COOKIE_SECRET_OLD` | 否 | 輪換後仍須驗證舊 Kratos cookie 的前一個值 |
| `KRATOS_CIPHER_SECRET_OLD` | 否 | 輪換後仍須解密舊 Kratos 資料的前一個值 |

DSN 格式如下。密碼若含 `@`、`:`、`/` 等保留字，先做 URL percent-encoding。

```text
mysql://<user>:<encoded-password>@tcp(<rds-endpoint>:3306)/hydra?max_conns=20&max_idle_conns=4
mysql://<user>:<encoded-password>@tcp(<rds-endpoint>:3306)/kratos?max_conns=20&max_idle_conns=4
mysql://<user>:<encoded-password>@tcp(<rds-endpoint>:3306)/hydra_dev?max_conns=20&max_idle_conns=4
mysql://<user>:<encoded-password>@tcp(<rds-endpoint>:3306)/kratos_dev?max_conns=20&max_idle_conns=4
```

Secret 值只能進 Secrets Manager。不要貼到 GitHub variables、workflow inputs、Actions log、issue、PR 或本 repository。主機上的 `/data/sso{,-dev}/.env` 由 playbook 以 mode `0600` 寫入，相關 Ansible task 使用 `no_log`。

### 4. 設定 GitHub OIDC 與 variables

在 `coseeing/SSO` repository 設定 Actions variables：

- `AWS_ACCOUNT_ID=622913514517`
- `AWS_REGION=ap-northeast-1`
- `AWS_GITHUB_ACTION_ROLE=<OIDC role ARN>`

OIDC role 的 trust policy 必須信任 GitHub OIDC provider，並將 subject 限制到 `coseeing/SSO`（例如 `repo:coseeing/SSO:*`，可再縮小到實際允許的 branch/environment）。該 role 只需登入 ECR 並 push 上述五個 repositories；workflow 不會建立 repository。

devops repository 的 GitHub Environment `a11y-village-production` 另需既有的 `AWS_GITHUB_ACTION_ROLE` secret 與 `EC2_SSH_KEY` secret，供 `Run Ansible` 查詢 stack IP、SSH 登入及讀取 Secrets Manager。不要把 SSH private key 改成 repository variable。

### 5. 確認 Traefik、DNS 與 Cloudflare

- 主機必須已存在 `/data/entry/entry/traefik.yml` 與 Docker network `entry`。SSO playbook 會在任何安裝或寫檔前檢查 Traefik；不存在會停止。
- 建立 `sso.dev.coseeing.org` 的 DNS 記錄並指向 `coseeing-stack-v2` EC2。
- prod 切換前先準備 `sso.coseeing.org` 記錄，但不要提早改掉目前 origin。
- 若 Cloudflare proxy 影響首次 TLS-ALPN 憑證簽發，依 F 節暫時切成 DNS only，簽發完成後再恢復 proxy。

## B. 日常部署

每次部署必須先更新映像檔，再執行 Ansible；只跑第二段可能重啟舊映像檔。

1. 到 `coseeing/SSO` → **Actions** → **upload-to-ecr** → **Run workflow**。
2. `target_env` 選 `dev` 或 `prod`；`build_target` 通常選 `all`。只更新單一映像時才選 `kratos`、`kratos-schemas`、`hydra`、`oathkeeper` 或 `center`。
3. 確認五個預期的 `<repository>:<target_env>` 已成功 push。
4. 到 `coseeing/devops` → **Actions** → **Run Ansible** → **Run workflow**。
5. `deploy_type` 選 `sso`，`deploy_site` 必須與 `target_env` 相同，`stack_name` 通常填 `coseeing-stack-v2`。
6. workflow 完成後檢查對應目錄：prod 為 `/data/sso`、dev 為 `/data/sso-dev`。

playbook 會重拉 `sso-center`、`sso-kratos`、`sso-hydra`、`sso-kratos-schemas`、`sso-oathkeeper` 的可覆蓋 tag，再執行兩個 migration container，最後啟動服務。prod 的內部 network 為 `sso`，container 為 `sso-hydra`、`sso-hydra-admin`、`sso-kratos`、`sso-kratos-admin`、`sso-kratos-schemas`、`sso-oathkeeper`、`sso-center`；dev 為 `sso-dev` 與加上 `-dev` 的同名 container。兩套環境不得互連。

Hydra 與 Kratos 的 public、admin API 分開跑：`sso-hydra`／`sso-kratos` 只提供 public API 並接 `entry`；`sso-hydra-admin`／`sso-kratos-admin` 只提供 admin API，只接 `sso{,-dev}`，因為 admin API 本身沒有認證。外部要用 admin API 只能經 `sso-oathkeeper`：`https://<domain>/hydra/admin/...`、`https://<domain>/kratos/admin/...`，需要帶 `sso.admin` scope、audience 為 `https://sso.coseeing.org/admin` 的 Hydra access token（access rules 寫在 SSO repo 的 `oathkeeper/access-rules/sso.yml`，打包進映像）。dev 沿用同一個 audience 字串；dev token 由 dev Hydra 簽發，Oathkeeper 只信任該環境自己的 issuer 與 JWKS，所以 prod 與 dev 的 token 不能互用。admin client 存在各自的 Hydra database，dev 需要時要另外建立。

## C. dev 首次部署與 a11yvillage client

1. 完成 A 節的 `kratos_dev`、`hydra_dev`、`dev/sso`、DNS 與 ECR 設定。
2. 依 B 節先跑 `upload-to-ecr`：`target_env=dev`、`build_target=all`。
3. 跑 `Run Ansible`：`deploy_type=sso`、`deploy_site=dev`、`stack_name=coseeing-stack-v2`。migrate containers 會建立空 database schema。
4. 驗證 dev Kratos ready、Hydra discovery 與 center 登入頁。
5. 在主機上 clone 一份 SSO repo（只為了使用 `scripts/`，不需要 `.env`），從 prod 匯出 a11yvillage client。輸出 JSON 含 client secret，必須存到 mode `0600` 的暫存目錄，不得提交 git：

```bash
cd /path/to/SSO
umask 077
HYDRA_DOCKER_NETWORK=sso \
HYDRA_ADMIN_URL=http://sso-hydra-admin:4445 \
python3 scripts/export-hydra-client.py a11yvillage \
  --output /secure/temp/a11yvillage.json
```

6. 人工檢查 JSON 的 `client_id`、redirect URIs、grant types 與 CORS 設定，再匯入 dev：

```bash
HYDRA_DOCKER_NETWORK=sso-dev \
HYDRA_ADMIN_URL=http://sso-hydra-admin-dev:4445 \
python3 scripts/import-hydra-client.py /secure/temp/a11yvillage.json
```

7. 從 a11yvillage dev 完成一次登入與 callback 驗證後，安全刪除暫存 JSON。不要在命令列或 Actions log 印出 client secret。

## D. prod 切換

1. 完成 A 節五個 ECR repositories、`prod/sso`、RDS database/帳號、security group、OIDC、Traefik 與 DNS 準備。
2. 進入維護狀態，停止舊站的 Kratos、Hydra 與會寫入這兩個 database 的服務，再建立一致性 dump。dump **只包含 `kratos` 與 `hydra`**，不可包含 `mysql` 系統 database。使用互動式密碼或受控 login path，不要把密碼放在 command line：

   ```bash
   mysqldump --single-transaction --routines --triggers \
     --databases kratos hydra > sso-prod-YYYYMMDD.sql
   ```

3. 檢查 dump 大小與 checksum，將它加密保存；匯入 RDS 的 `kratos`、`hydra`，匯入完成前不要啟動新舊任何一套寫入服務。
4. 再次核對 `prod/sso` 的 `HYDRA_SYSTEM_SECRET`、`KRATOS_COOKIE_SECRET`、`KRATOS_CIPHER_SECRET` 與舊站完全相同。此時不能先產生新值，否則 Hydra JWKS、OAuth client secret、Kratos session 或加密資料可能無法解開。
5. 在 SSO repo 執行 `upload-to-ecr`：`target_env=prod`、`build_target=all`。
6. 在 devops repo 執行 `Run Ansible`：`deploy_type=sso`、`deploy_site=prod`、`stack_name=coseeing-stack-v2`。
7. DNS 切換前直接指定新 origin 驗證。至少檢查：

   ```bash
   curl --resolve sso.coseeing.org:443:57.181.102.37 \
     https://sso.coseeing.org/kratos/health/ready
   curl --resolve sso.coseeing.org:443:57.181.102.37 \
     https://sso.coseeing.org/.well-known/openid-configuration
   ```

   另外用 hosts 覆寫或同等受控方式驗證 center 密碼登入、2FA、Google 登入，以及 a11yvillage admin 登入。
8. 全部驗證通過後，才把 Cloudflare 的 `sso.coseeing.org` origin 改到 `57.181.102.37`；持續觀察 Kratos、Hydra、center 與 Traefik log。
9. 舊主機、加密 dump 與切換前備份保留數天。不要在確認期內刪除舊資料或舊的 Hydra/Kratos secret。
10. 穩定後依 E 節輪換 secret。

回退時先停止新寫入並評估切換後是否已有新使用者、2FA 或 OAuth 變更。若可接受回到切換前資料，將 Cloudflare origin 改回舊主機並啟動舊站；若必須保留新資料，先停機並規劃資料回搬，不能直接讓新舊 database 同時寫入。應用程式版本回退需在 SSO repo checkout 已知正常 commit，重新 push 同一個 `prod` tag，再重跑 `Run Ansible`；tag 可覆蓋，devops repo 本身沒有 immutable image rollback。database migration 若不向下相容，使用切換前 RDS snapshot/dump 恢復，並保持完整停機窗口。

## E. Hydra／Kratos secret 輪換

Hydra v26 沒有可把所有既有資料重新加密後安全移除舊 system secret 的指令。輪換採「新值負責加密、舊值持續解密」：舊值必須永久保留，不能在觀察一段時間後刪除。

1. 讀取目前 `HYDRA_SYSTEM_SECRET`、`KRATOS_COOKIE_SECRET`、`KRATOS_CIPHER_SECRET`，在受控環境產生對應的新值；`KRATOS_CIPHER_SECRET` 新值仍須為 32 長度。
2. 在同一個 Secrets Manager secret 中，把新值寫入原 key，把原值移到對應的 `_OLD` key：
   - `HYDRA_SYSTEM_SECRET_OLD`
   - `KRATOS_COOKIE_SECRET_OLD`
   - `KRATOS_CIPHER_SECRET_OLD`
3. 依 B 節重新部署相同環境。playbook 會把新值放到 `SECRETS_*_0`，舊值放到 `SECRETS_*_1`；沒有 `_OLD` key 時不會產生空的 `_1` 設定。
4. 驗證既有使用者登入、既有 session、Google、2FA、Hydra discovery、JWKS 與 a11yvillage client。
5. 保留三個 `_OLD` key。**不要刪除、覆寫或重複輪換到無法保留目前所有歷史解密值的狀態。** 需要再次輪換前，先確認 Ory 版本是否已提供可驗證的重新加密與多歷史值策略；未確認前停止操作。

`OATHKEEPER_SHARED_SECRET`、Google OAuth secret 與 Gmail app password 不使用上述 `_OLD` 機制；輪換會使相關 session 或憑證立即失效，應在維護窗口進行並完成登入/寄信驗證。

## F. 疑難排解

所有命令都在對應環境目錄執行：prod `/data/sso`，dev `/data/sso-dev`。

- **playbook 一開始就回報沒有 Traefik**：確認 `/data/entry/entry/traefik.yml` 與既有 `entry` network。不要建立空檔繞過檢查；先修復 Traefik stack。
- **Traefik 502**：執行 `docker compose ps`、`docker compose logs center kratos hydra`，確認服務不是 migration failure；再用 `docker network inspect entry` 與 `docker network inspect sso`（dev 用 `sso-dev`）確認 public services 同時加入正確網路。檢查 container 名稱是否為 prod 的 `sso-center`／`sso-kratos`／`sso-hydra`／`sso-oathkeeper`，或 dev 的 `sso-center-dev`／`sso-kratos-dev`／`sso-hydra-dev`／`sso-oathkeeper-dev`。`/hydra/admin`、`/kratos/admin` 回 401 是正常的（沒有帶 `sso.admin` token）；回 502 時看 `docker compose logs oathkeeper hydra-admin kratos-admin`。
- **certresolver 在 Cloudflare proxy 後申請失敗**：Traefik 使用 TLS-ALPN challenge。暫時把該 hostname 切成 Cloudflare DNS only，等待憑證成功簽發後再恢復 proxy；同時檢查 443 可達與 `/data/entry/entry/traefik.yml` 中的 `sso`／`sso-dev` resolver。
- **`hydra-migrate` 或 `kratos-migrate` 失敗**：執行 `docker compose ps -a` 與 `docker compose logs hydra-migrate kratos-migrate`。核對 DSN database 名稱、URL encoding、RDS 3306 security group、帳號只對應自己的 database，以及 RDS TLS/連線限制。修正後重跑 `Run Ansible`，不要手動略過 migration 啟動主服務。
- **Hydra JWKS、OAuth client secret 或登入資料解不開**：優先比對切換前的 `HYDRA_SYSTEM_SECRET`、`KRATOS_CIPHER_SECRET` 與 `KRATOS_COOKIE_SECRET`。立即恢復正確舊值或 `_OLD` 值並重新部署；不要以重新產生 secret、刪除 JWKS 或重建 client 當成修復。
- **dev 與 prod 串錯環境**：檢查 internal URLs 是否使用 `sso-kratos{-dev}`、`sso-kratos-admin{-dev}`、`sso-hydra-admin{-dev}`，network 是否為 `sso` 或 `sso-dev`。不要改回 `http://kratos:...` 或 `http://hydra:...`，因為同主機的 Docker DNS 可能解析到另一個環境。
- **映像檔看起來沒有更新**：先確認 `upload-to-ecr` 的 `target_env` 與 `Run Ansible` 的 `deploy_site` 相同，五個 repositories 的 tag 都已更新，再查看 Ansible 的 untag/pull 步驟。不要只重啟 container。
