"""Entry point: load config, wire components, run the monitor loop."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from health_check.config import load_config, load_slack_settings
from health_check.monitor import Monitor
from health_check.notifier import SlackNotifier


async def run() -> None:
    config = load_config(Path(os.environ.get("TARGETS_FILE", "targets.yaml")))
    settings = load_slack_settings(os.environ)
    logging.info("monitoring %d targets every %ss", len(config.targets), config.defaults.check_interval_seconds)
    async with httpx.AsyncClient() as client:
        monitor = Monitor(config=config, client=client, notifier=SlackNotifier(client, settings))
        await monitor.run_forever()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    def handle_sigterm(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.info("shutting down")


if __name__ == "__main__":
    main()
