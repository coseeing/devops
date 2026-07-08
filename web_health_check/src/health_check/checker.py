"""Check a single target with in-round retries."""

import asyncio
from dataclasses import dataclass

import httpx

from health_check.config import Defaults, Target


@dataclass
class CheckResult:
    target: Target
    ok: bool
    attempts: int
    status_code: int | None = None
    reason: str | None = None


async def check_target(client: httpx.AsyncClient, target: Target, defaults: Defaults) -> CheckResult:
    status_code: int | None = None
    reason: str | None = None
    attempts = 0

    for attempt in range(1, defaults.max_attempts + 1):
        attempts = attempt
        try:
            response = await client.get(target.url, timeout=defaults.timeout_seconds)
            status_code = response.status_code
            if 200 <= status_code < 400:
                return CheckResult(target=target, ok=True, attempts=attempts, status_code=status_code)
            reason = f"HTTP {status_code}"
        except httpx.HTTPError as exc:
            status_code = None
            reason = f"{type(exc).__name__}: {exc}"

        if attempt < defaults.max_attempts:
            await asyncio.sleep(defaults.retry_delay_seconds)

    return CheckResult(target=target, ok=False, attempts=attempts, status_code=status_code, reason=reason)
