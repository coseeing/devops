import json
from datetime import timedelta

import httpx
import respx

from health_check.config import SlackSettings, Target
from health_check.notifier import SlackNotifier, format_duration
from health_check.state import Notification, NotificationKind

TARGET = Target(name="Frontend", url="https://example.com/")
SETTINGS = SlackSettings(bot_token="xoxb-123", channel_id="C0ABC")

SLACK_API = "https://slack.com/api/chat.postMessage"


def down_notification():
    return Notification(kind=NotificationKind.DOWN, target=TARGET, reason="HTTP 503", attempts=3)


@respx.mock
async def test_send_down_posts_alert_to_slack():
    route = respx.post(SLACK_API).respond(200, json={"ok": True})

    async with httpx.AsyncClient() as client:
        sent = await SlackNotifier(client, SETTINGS).send(down_notification())

    assert sent is True
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer xoxb-123"
    body = json.loads(request.content)
    assert body["channel"] == "C0ABC"
    assert "🔴" in body["text"]
    assert "Frontend" in body["text"]
    assert "https://example.com/" in body["text"]
    assert "HTTP 503" in body["text"]
    assert "連續 3 次失敗" in body["text"]


@respx.mock
async def test_send_recovery_mentions_downtime():
    route = respx.post(SLACK_API).respond(200, json={"ok": True})
    notification = Notification(
        kind=NotificationKind.RECOVERY, target=TARGET, downtime=timedelta(minutes=45)
    )

    async with httpx.AsyncClient() as client:
        await SlackNotifier(client, SETTINGS).send(notification)

    text = json.loads(route.calls.last.request.content)["text"]
    assert "🟢" in text
    assert "45 分鐘" in text


@respx.mock
async def test_send_reminder_mentions_elapsed_downtime():
    route = respx.post(SLACK_API).respond(200, json={"ok": True})
    notification = Notification(
        kind=NotificationKind.REMINDER,
        target=TARGET,
        reason="HTTP 503",
        downtime=timedelta(minutes=31),
    )

    async with httpx.AsyncClient() as client:
        await SlackNotifier(client, SETTINGS).send(notification)

    text = json.loads(route.calls.last.request.content)["text"]
    assert "🟡" in text
    assert "31 分鐘" in text


@respx.mock
async def test_slack_api_error_returns_false_without_raising():
    respx.post(SLACK_API).respond(200, json={"ok": False, "error": "channel_not_found"})

    async with httpx.AsyncClient() as client:
        sent = await SlackNotifier(client, SETTINGS).send(down_notification())

    assert sent is False


@respx.mock
async def test_network_error_returns_false_without_raising():
    respx.post(SLACK_API).side_effect = httpx.ConnectError("boom")

    async with httpx.AsyncClient() as client:
        sent = await SlackNotifier(client, SETTINGS).send(down_notification())

    assert sent is False


def test_format_duration_minutes():
    assert format_duration(timedelta(minutes=45)) == "45 分鐘"


def test_format_duration_hours_and_minutes():
    assert format_duration(timedelta(hours=1, minutes=30)) == "1 小時 30 分鐘"


def test_format_duration_under_a_minute():
    assert format_duration(timedelta(seconds=40)) == "1 分鐘內"
