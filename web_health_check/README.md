# web_health_check

HTTP 健康檢查服務:定期檢查多個網址,連不上時發 Slack 通知,恢復時也會通知,持續異常期間定期重複提醒。

## 運作方式

- 每 `check_interval_seconds`(預設 60 秒)檢查一次所有目標
- HTTP 狀態碼 200–399 視為正常;4xx/5xx、連線失敗、逾時視為異常
- 單輪內失敗會重試,連續 `max_attempts`(預設 3)次都失敗才判定 DOWN 並發 🔴 告警
- 恢復時發 🟢 通知(含中斷時長);持續 DOWN 每 `remind_interval_minutes`(預設 30 分鐘)發 🟡 提醒

## 設定

1. 複製 `.env.example` 為 `.env`,填入 Slack bot token 與 channel ID(bot 需有 `chat:write` 權限並已加入該 channel):

   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_CHANNEL_ID=C0XXXXXXXXX
   ```

2. 編輯 `targets.yaml`,列出要監控的網址(前端、後端皆可):

   ```yaml
   targets:
     - name: Frontend
       url: https://example.com
     - name: Backend API
       url: https://api.example.com/health
   ```

## 部署(docker compose)

```bash
docker compose up -d --build
docker compose logs -f      # 查看每輪檢查紀錄
```

修改 `targets.yaml` 後執行 `docker compose restart` 生效。

## 本機開發

```bash
uv sync
uv run pytest        # 跑測試
uv run python -m health_check
```
