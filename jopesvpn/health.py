from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

if TYPE_CHECKING:
    from aiogram import Bot
    from aiohttp import web

logger = logging.getLogger(__name__)


class BotHealth:
    def __init__(self, *, interval: int = 60, max_age: int = 180):
        self.interval = interval
        self.max_age = max_age
        self.started_at = time.monotonic()
        self.last_success: float | None = None
        self.ready = False
        self.failed = False

    def record_success(self) -> None:
        self.last_success = time.monotonic()
        if self.failed:
            logger.info("Telegram health probe recovered")
        self.failed = False

    def healthy(self) -> bool:
        return (
            self.ready
            and self.last_success is not None
            and time.monotonic() - self.last_success <= self.max_age
        )

    async def handler(self, _request: web.Request) -> web.Response:
        from aiohttp import web

        healthy = self.healthy()
        return web.json_response(
            {
                "status": "ok" if healthy else "degraded",
                "uptime_seconds": int(time.monotonic() - self.started_at),
            },
            status=200 if healthy else 503,
        )

    async def probe(self, bot: Bot) -> None:
        from aiogram.exceptions import TelegramAPIError

        while True:
            try:
                await bot.get_me()
            except TelegramAPIError as error:
                if not self.failed:
                    logger.warning(
                        "Telegram health probe failed (%s)", type(error).__name__
                    )
                self.failed = True
            else:
                self.record_success()
            await asyncio.sleep(self.interval)


def check(url: str = "http://127.0.0.1:8081/healthz") -> int:
    try:
        with urlopen(url, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except (HTTPError, URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8081/healthz")
    raise SystemExit(check(parser.parse_args().url))
