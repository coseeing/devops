"""Main loop: check every target each round and notify on state changes."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx

from health_check.checker import check_target
from health_check.config import Config
from health_check.notifier import SlackNotifier
from health_check.state import Notification, TargetState

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self,
        config: Config,
        client: httpx.AsyncClient,
        notifier: SlackNotifier,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._config = config
        self._client = client
        self._notifier = notifier
        self._sleep = sleep
        remind_interval = timedelta(minutes=config.defaults.remind_interval_minutes)
        self._states = {
            target.name: TargetState(target=target, remind_interval=remind_interval)
            for target in config.targets
        }
        # Notifications whose Slack delivery failed, retried next round (newest per target wins).
        self._pending: dict[str, Notification] = {}

    async def run_once(self, now: datetime) -> None:
        results = await asyncio.gather(
            *(check_target(self._client, target, self._config.defaults) for target in self._config.targets),
            return_exceptions=True,
        )
        for target, result in zip(self._config.targets, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):  # CancelledError must propagate, not count as a failed check
                    raise result
                logger.error("check for %s crashed: %r", target.name, result)
                continue
            if result.ok:
                logger.info("%s is UP (HTTP %s)", target.name, result.status_code)
            else:
                logger.warning("%s is DOWN after %d attempts (%s)", target.name, result.attempts, result.reason)
            notification = self._states[target.name].transition(result, now)
            if notification:
                self._pending[target.name] = notification
        for name, notification in list(self._pending.items()):
            if await self._notifier.send(notification):
                del self._pending[name]

    async def run_forever(self) -> None:
        interval = self._config.defaults.check_interval_seconds
        while True:
            started = time.monotonic()
            try:
                await self.run_once(datetime.now(timezone.utc))
            except Exception:
                logger.exception("check round failed unexpectedly")
            elapsed = time.monotonic() - started
            await self._sleep(max(0.0, interval - elapsed))
