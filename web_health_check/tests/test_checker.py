import httpx
import respx

from health_check.checker import CheckResult, check_target
from health_check.config import Defaults, Target

TARGET = Target(name="Frontend", url="https://example.com/")
DEFAULTS = Defaults(timeout_seconds=1, max_attempts=3, retry_delay_seconds=0)


@respx.mock
async def test_healthy_on_first_attempt():
    respx.get("https://example.com/").respond(200)

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert isinstance(result, CheckResult)
    assert result.ok is True
    assert result.attempts == 1
    assert result.status_code == 200


@respx.mock
async def test_3xx_counts_as_healthy():
    respx.get("https://example.com/").respond(302)

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert result.ok is True


@respx.mock
async def test_5xx_retries_up_to_max_attempts_then_fails():
    route = respx.get("https://example.com/").respond(503)

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert result.ok is False
    assert result.attempts == 3
    assert route.call_count == 3
    assert "503" in result.reason


@respx.mock
async def test_connection_error_retries_then_fails():
    route = respx.get("https://example.com/")
    route.side_effect = httpx.ConnectError("connection refused")

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert result.ok is False
    assert result.attempts == 3
    assert result.status_code is None
    assert "connection refused" in result.reason


@respx.mock
async def test_recovers_within_same_round():
    route = respx.get("https://example.com/")
    route.side_effect = [httpx.Response(500), httpx.Response(500), httpx.Response(200)]

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert result.ok is True
    assert result.attempts == 3


@respx.mock
async def test_timeout_counts_as_failure():
    route = respx.get("https://example.com/")
    route.side_effect = httpx.ReadTimeout("timed out")

    async with httpx.AsyncClient() as client:
        result = await check_target(client, TARGET, DEFAULTS)

    assert result.ok is False
    assert "timed out" in result.reason
