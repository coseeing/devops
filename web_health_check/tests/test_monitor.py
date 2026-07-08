import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from health_check.checker import CheckResult
from health_check.config import Config, Defaults, SlackSettings, Target
from health_check.monitor import Monitor
from health_check.notifier import SLACK_POST_MESSAGE_URL, SlackNotifier

T0 = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS = SlackSettings(bot_token="xoxb-123", channel_id="C0ABC")


def make_config(**defaults_overrides):
    defaults = Defaults(timeout_seconds=1, retry_delay_seconds=0, **defaults_overrides)
    return Config(
        defaults=defaults,
        targets=[
            Target(name="Frontend", url="https://frontend.example.com/"),
            Target(name="Backend", url="https://backend.example.com/"),
        ],
    )


def make_monitor(client, config=None, sleep=None):
    config = config or make_config()
    kwargs = {"sleep": sleep} if sleep else {}
    return Monitor(config=config, client=client, notifier=SlackNotifier(client, SETTINGS), **kwargs)


def slack_texts(route):
    return [json.loads(call.request.content)["text"] for call in route.calls]


@respx.mock
async def test_run_once_alerts_only_for_failing_target():
    respx.get("https://frontend.example.com/").respond(200)
    respx.get("https://backend.example.com/").side_effect = httpx.ConnectError("refused")
    slack = respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    async with httpx.AsyncClient() as client:
        await make_monitor(client).run_once(T0)

    texts = slack_texts(slack)
    assert len(texts) == 1
    assert "Backend" in texts[0]
    assert "🔴" in texts[0]


@respx.mock
async def test_run_once_all_healthy_sends_nothing():
    respx.get("https://frontend.example.com/").respond(200)
    respx.get("https://backend.example.com/").respond(302)
    slack = respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    async with httpx.AsyncClient() as client:
        await make_monitor(client).run_once(T0)

    assert slack.call_count == 0


@respx.mock
async def test_recovery_across_rounds_sends_recovery():
    frontend = respx.get("https://frontend.example.com/")
    frontend.respond(200)
    backend = respx.get("https://backend.example.com/")
    backend.side_effect = httpx.ConnectError("refused")
    slack = respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    async with httpx.AsyncClient() as client:
        monitor = make_monitor(client)
        await monitor.run_once(T0)
        backend.side_effect = None
        backend.respond(200)
        await monitor.run_once(T0 + timedelta(minutes=5))

    texts = slack_texts(slack)
    assert len(texts) == 2
    assert "🔴" in texts[0]
    assert "🟢" in texts[1]
    assert "5 分鐘" in texts[1]


@respx.mock
async def test_run_forever_repeats_rounds_with_interval():
    frontend = respx.get("https://frontend.example.com/").respond(200)
    respx.get("https://backend.example.com/").respond(200)
    respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async with httpx.AsyncClient() as client:
        monitor = make_monitor(client, make_config(check_interval_seconds=60), sleep=fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await monitor.run_forever()

    assert frontend.call_count == 2
    assert len(sleeps) == 2
    assert all(0 <= s <= 60 for s in sleeps)


@respx.mock
async def test_unexpected_check_error_does_not_abort_round():
    respx.get("https://frontend.example.com/").side_effect = ValueError("boom")
    respx.get("https://backend.example.com/").side_effect = httpx.ConnectError("refused")
    slack = respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    async with httpx.AsyncClient() as client:
        await make_monitor(client).run_once(T0)

    texts = slack_texts(slack)
    assert len(texts) == 1
    assert "Backend" in texts[0]


@respx.mock
async def test_unexpected_error_does_not_kill_run_forever():
    frontend = respx.get("https://frontend.example.com/")
    frontend.side_effect = ValueError("boom")
    respx.get("https://backend.example.com/").respond(200)
    respx.post(SLACK_POST_MESSAGE_URL).respond(200, json={"ok": True})

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async with httpx.AsyncClient() as client:
        monitor = make_monitor(client, sleep=fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await monitor.run_forever()

    assert frontend.call_count == 2


async def test_cancellation_is_not_swallowed(monkeypatch):
    # KeyboardInterrupt/SystemExit escape the event loop on their own; CancelledError is the
    # BaseException that gather(return_exceptions=True) captures as a result and must be re-raised.
    async def raising_check(client, target, defaults):
        if target.name == "Frontend":
            raise asyncio.CancelledError
        return CheckResult(target=target, ok=True, attempts=1, status_code=200)

    monkeypatch.setattr("health_check.monitor.check_target", raising_check)

    async with httpx.AsyncClient() as client:
        with pytest.raises(asyncio.CancelledError):
            await make_monitor(client).run_once(T0)


@respx.mock
async def test_failed_down_send_is_retried_next_round():
    respx.get("https://frontend.example.com/").respond(200)
    respx.get("https://backend.example.com/").side_effect = httpx.ConnectError("refused")
    slack = respx.post(SLACK_POST_MESSAGE_URL)
    slack.side_effect = [
        httpx.Response(200, json={"ok": False, "error": "fatal_error"}),
        httpx.Response(200, json={"ok": True}),
        httpx.Response(200, json={"ok": True}),
    ]

    async with httpx.AsyncClient() as client:
        monitor = make_monitor(client)
        await monitor.run_once(T0)
        await monitor.run_once(T0 + timedelta(minutes=1))
        await monitor.run_once(T0 + timedelta(minutes=2))

    texts = slack_texts(slack)
    assert len(texts) == 2  # failed send retried once, then no more resends
    assert all("🔴" in text for text in texts)


@respx.mock
async def test_failed_recovery_send_is_retried_next_round():
    respx.get("https://frontend.example.com/").respond(200)
    backend = respx.get("https://backend.example.com/")
    backend.side_effect = httpx.ConnectError("refused")
    slack = respx.post(SLACK_POST_MESSAGE_URL)
    slack.side_effect = [
        httpx.Response(200, json={"ok": True}),  # DOWN alert delivered
        httpx.Response(500),  # RECOVERY send fails
        httpx.Response(200, json={"ok": True}),  # RECOVERY retried
    ]

    async with httpx.AsyncClient() as client:
        monitor = make_monitor(client)
        await monitor.run_once(T0)
        backend.side_effect = None
        backend.respond(200)
        await monitor.run_once(T0 + timedelta(minutes=1))
        await monitor.run_once(T0 + timedelta(minutes=2))

    texts = slack_texts(slack)
    assert len(texts) == 3
    assert "🔴" in texts[0]
    assert "🟢" in texts[1]
    assert "🟢" in texts[2]
