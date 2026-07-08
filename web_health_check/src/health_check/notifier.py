"""Send notifications to Slack via chat.postMessage."""

import logging
from datetime import timedelta

import httpx

from health_check.config import SlackSettings
from health_check.state import Notification, NotificationKind

logger = logging.getLogger(__name__)

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def format_duration(duration: timedelta) -> str:
    minutes = int(duration.total_seconds() // 60)
    if minutes < 1:
        return "1 分鐘內"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小時 {minutes} 分鐘"
    return f"{minutes} 分鐘"


def render_text(notification: Notification) -> str:
    target = notification.target
    match notification.kind:
        case NotificationKind.DOWN:
            return (
                f"🔴 *[{target.name}]* {target.url} 連線異常"
                f"(連續 {notification.attempts} 次失敗:{notification.reason})"
            )
        case NotificationKind.REMINDER:
            return (
                f"🟡 *[{target.name}]* {target.url} 仍然無法連線,"
                f"已持續 {format_duration(notification.downtime)}({notification.reason})"
            )
        case NotificationKind.RECOVERY:
            return f"🟢 *[{target.name}]* {target.url} 已恢復連線(中斷 {format_duration(notification.downtime)})"


class SlackNotifier:
    def __init__(self, client: httpx.AsyncClient, settings: SlackSettings):
        self._client = client
        self._settings = settings

    async def send(self, notification: Notification) -> bool:
        payload = {"channel": self._settings.channel_id, "text": render_text(notification)}
        headers = {"Authorization": f"Bearer {self._settings.bot_token}"}
        try:
            response = await self._client.post(SLACK_POST_MESSAGE_URL, json=payload, headers=headers)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("failed to send Slack notification for %s: %s", notification.target.name, exc)
            return False
        if not body.get("ok"):
            logger.error(
                "Slack API rejected notification for %s: %s",
                notification.target.name,
                body.get("error", "unknown error"),
            )
            return False
        return True
