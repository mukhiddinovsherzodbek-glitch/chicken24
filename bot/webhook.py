"""Webhook rejimi (Render kabi serverlar uchun): aiohttp HTTP server.

- POST <sir yo'lak>  - Telegram yangilanishlari. X-Telegram-Bot-Api-Secret-Token sarlavhasi
  WEBHOOK_SECRET bilan solishtiriladi (mos kelmasa 401).
- POST /api/orders, GET /api/order-status - Mini App API (orderapi.py).
- GET/HEAD /health   - {"ok": true}. Bazaga murojaat qilmaydi (Render health check, keep-alive, UptimeRobot).

Tez ishga tushish: server ENG BIRINCHI ochiladi (Render port'ni darhol ko'radi, /health darhol javob beradi),
baza va menyu keyin tayyorlanadi. Shu orada kelgan Telegram yangilanishi `ready` hodisasini kutadi
(uzog'i bilan READY_TIMEOUT_SECONDS; o'tib ketsa 503 - Telegram keyinroq qayta yuboradi, xabar yo'qolmaydi).

Yo'lak WEBHOOK_SECRET dan hisoblanadi (sha256): tasodifiy ko'rinadi, qayta ishga tushganda o'zgarmaydi,
sirning o'zi esa manzilda ham, loglarda ham ko'rinmaydi.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

logger = logging.getLogger(__name__)

HEALTH_PATH = "/health"
WEBHOOK_PREFIX = "/tg/"
READY_TIMEOUT_SECONDS = 50.0  # Telegram webhook so'rovini ~60 soniya kutadi


def webhook_path(secret: str) -> str:
    digest = hashlib.sha256(f"chicken24-webhook:{secret}".encode()).hexdigest()
    return WEBHOOK_PREFIX + digest[:40]


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


class GatedRequestHandler(SimpleRequestHandler):
    """Sir tekshiruvidan keyin bot tayyor bo'lishini (baza ulangan) kutadi, so'ng aiogram'ga uzatadi."""

    def __init__(self, *args, ready: asyncio.Event | None = None,
                 ready_timeout: float = READY_TIMEOUT_SECONDS, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ready = ready
        self._ready_timeout = ready_timeout

    async def handle(self, request: web.Request) -> web.Response:
        bot = await self.resolve_bot(request)
        if not self.verify_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), bot):
            return web.Response(body="Unauthorized", status=401)
        if self._ready is not None and not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), self._ready_timeout)
            except asyncio.TimeoutError:
                logger.warning("Bot hali tayyor emas - Telegram yangilanishi keyinroq qayta yuboriladi (503)")
                return web.Response(status=503)
        return await super().handle(request)


SERVER_NAME = "chicken24"


async def harden_headers(_request: web.Request, response: web.StreamResponse) -> None:
    """Har bir javobga: server/Python/aiohttp versiyasi oshkor qilinmaydi (aiohttp odatda
    'Server: Python/3.12 aiohttp/3.x' yuboradi - hujumchiga aniq versiya kerak emas); brauzer javob turini
    o'zicha taxmin qilmaydi (nosniff)."""
    response.headers["Server"] = SERVER_NAME
    response.headers.setdefault("X-Content-Type-Options", "nosniff")


def build_app(dp: Dispatcher, bot: Bot, secret: str, *, ready: asyncio.Event | None = None,
              ready_timeout: float = READY_TIMEOUT_SECONDS, api: Any = None) -> web.Application:
    app = web.Application()
    app.on_response_prepare.append(harden_headers)
    app.router.add_get(HEALTH_PATH, health)  # aiohttp HEAD ni ham avtomatik qo'shadi
    for one in (api if isinstance(api, (list, tuple)) else [api] if api is not None else []):
        one.register(app)  # Mini App: POST /api/orders, GET /api/order-status (orderapi.py)
    GatedRequestHandler(dispatcher=dp, bot=bot, secret_token=secret, ready=ready,
                        ready_timeout=ready_timeout).register(app, path=webhook_path(secret))
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


async def start_server(dp: Dispatcher, bot: Bot, *, secret: str, port: int, host: str = "0.0.0.0",
                       ready: asyncio.Event | None = None, api: Any = None) -> web.AppRunner:
    """HTTP serverni ochadi va darhol qaytadi (to'xtatish: `await runner.cleanup()`)."""
    runner = web.AppRunner(build_app(dp, bot, secret, ready=ready, api=api), access_log=None)  # access log'da sir yo'lak bo'lmasin
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except Exception:
        await runner.cleanup()
        raise
    logger.info("HTTP server ochildi: port %s (%s, webhook %s...)", port, HEALTH_PATH, WEBHOOK_PREFIX)
    return runner


async def set_webhook(dp: Dispatcher, bot: Bot, *, base_url: str, secret: str) -> None:
    """Har ishga tushishda chaqiriladi (idempotent): manzil + sir; kutib turgan xabarlar o'chirilmaydi."""
    await bot.set_webhook(
        base_url.rstrip("/") + webhook_path(secret),
        secret_token=secret,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    logger.info("Webhook o'rnatildi (%s...)", WEBHOOK_PREFIX)


async def serve(dp: Dispatcher, bot: Bot, *, base_url: str, secret: str, port: int,
                host: str = "0.0.0.0", stop: asyncio.Event | None = None) -> None:
    """Server + setWebhook, `stop` gacha ishlaydi (bot allaqachon tayyor bo'lganda ishlatiladi)."""
    stop = stop or asyncio.Event()
    install_stop_signals(stop)
    runner = await start_server(dp, bot, secret=secret, port=port, host=host)
    try:
        await set_webhook(dp, bot, base_url=base_url, secret=secret)
        await stop.wait()
        logger.info("To'xtash signali olindi")
    finally:
        await runner.cleanup()
