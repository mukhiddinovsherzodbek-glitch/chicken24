"""Webhook rejimi (Render kabi serverlar uchun): aiohttp HTTP server.

- POST <sir yo'lak>  - Telegram yangilanishlari. X-Telegram-Bot-Api-Secret-Token sarlavhasi
  WEBHOOK_SECRET bilan solishtiriladi (mos kelmasa 401).
- GET  /health       - {"ok": true}. Bazaga murojaat qilmaydi (Render health check va keep-alive uchun).

Yo'lak WEBHOOK_SECRET dan hisoblanadi (sha256): tasodifiy ko'rinadi, qayta ishga tushganda o'zgarmaydi,
sirning o'zi esa manzilda ham, loglarda ham ko'rinmaydi.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

logger = logging.getLogger(__name__)

HEALTH_PATH = "/health"
WEBHOOK_PREFIX = "/tg/"


def webhook_path(secret: str) -> str:
    digest = hashlib.sha256(f"chicken24-webhook:{secret}".encode()).hexdigest()
    return WEBHOOK_PREFIX + digest[:40]


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def build_app(dp: Dispatcher, bot: Bot, secret: str) -> web.Application:
    app = web.Application()
    app.router.add_get(HEALTH_PATH, health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=webhook_path(secret))
    setup_application(app, dp, bot=bot)
    return app


def install_stop_signals(stop: asyncio.Event) -> None:
    """SIGTERM (Render qayta ishga tushirganda) / SIGINT -> to'g'ri to'xtash. Windows'da qo'llanmaydi."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass


async def serve(dp: Dispatcher, bot: Bot, *, base_url: str, secret: str, port: int,
                host: str = "0.0.0.0", stop: asyncio.Event | None = None) -> None:
    """HTTP serverni ishga tushiradi, Telegram'ga webhook manzilini o'rnatadi va `stop` gacha ishlaydi."""
    stop = stop or asyncio.Event()
    install_stop_signals(stop)
    runner = web.AppRunner(build_app(dp, bot, secret), access_log=None)  # access log'da sir yo'lak bo'lmasin
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
        logger.info("HTTP server ishga tushdi: port %s (%s, webhook %s...)", port, HEALTH_PATH, WEBHOOK_PREFIX)
        await bot.set_webhook(
            base_url.rstrip("/") + webhook_path(secret),
            secret_token=secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Webhook o'rnatildi: %s%s...", base_url.rstrip("/"), WEBHOOK_PREFIX)
        await stop.wait()
        logger.info("To'xtash signali olindi")
    finally:
        await runner.cleanup()
