"""Sayt (chicken24.uz) buyurtmasi: POST /api/web-orders.

Telegram'siz mijoz uchun. Mantiq Mini App bilan BIR XIL (handlers.process_order): narx menu.json dan,
ish vaqti, validatsiya, bazaga yozish va admin/guruhga chek.

Farqlari:
- initData yo'q. Himoya: faqat ruxsat etilgan origin (SITE_ORIGINS), IP bo'yicha cheklov va
  (TURNSTILE_SECRET berilsa) Cloudflare Turnstile tekshiruvi.
- Mijoz identifikatori: telefon raqamidan hosil qilingan MANFIY son (-998901234567). Haqiqiy Telegram id'lari
  doim musbat, shuning uchun to'qnashmaydi. Chekda "Sayt buyurtmasi" deb ko'rinadi (handlers.build_receipt).
- Mijozga Telegram xabari yuborilmaydi; rad etilsa sabab matni javobda qaytadi (message).
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
import re
from typing import Any, Callable

import aiohttp
from aiogram.types import User
from aiohttp import web

from delivery import valid_point
from orderapi import WindowLimiter, client_ip
from validators import PHONE_RE, extract_ref

logger = logging.getLogger(__name__)

WEB_ORDERS_PATH = "/api/web-orders"
WEB_CONFIG_PATH = "/api/web-config"   # GET: yetkazish tarifi (ochiq, keshlanadi)
WEB_QUOTE_PATH = "/api/web-quote"     # GET ?lat=&lon=: aniq masofa va yetkazish narxi
QUOTE_LIMIT = 20                      # IP boshiga daqiqasiga
MAX_BODY_BYTES = 8 * 1024
IP_LIMIT = 6                  # IP boshiga 10 daqiqada so'rovlar (muvaffaqiyatsizlari ham hisobga olinadi)
IP_WINDOW_SECONDS = 600.0
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
DEFAULT_ORIGINS = "https://chicken24.uz,https://www.chicken24.uz"
_TAG_RE = re.compile(r"<[^>]+>")


def site_origins() -> frozenset[str]:
    raw = os.getenv("SITE_ORIGINS", "").strip() or DEFAULT_ORIGINS
    return frozenset(o.strip().lower().rstrip("/") for o in raw.split(",") if o.strip())


def web_user_id(phone: str) -> int:
    """+998901234567 -> -998901234567 (har telefonga bitta, Telegram id'lariga to'qnashmaydi)."""
    return -int(phone[1:])


def plain_text(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


class WebOrderAPI:
    def __init__(self, *, bot: Any, config: Any, menu_store: Any, db: Any, limiter: Any,
                 ready: asyncio.Event | None = None, process: Callable[..., Any] | None = None,
                 origins: frozenset[str] | None = None, turnstile_secret: str | None = None,
                 verify_captcha: Callable[..., Any] | None = None, delivery: Any = None,
                 monotonic: Callable[[], float] | None = None) -> None:
        if process is None:
            import handlers
            process = handlers.process_order
        self._bot, self._config, self._menu_store, self._db = bot, config, menu_store, db
        self._limiter = limiter
        if delivery is None:
            import handlers
            delivery = handlers.default_delivery(config)
        self._delivery = delivery
        self._ready = ready
        self._process = process
        self._origins = origins if origins is not None else site_origins()
        self._secret = turnstile_secret if turnstile_secret is not None else os.getenv("TURNSTILE_SECRET", "").strip()
        self._verify_captcha = verify_captcha or self._turnstile
        kwargs = {"clock": monotonic} if monotonic else {}
        self.ip_limiter = WindowLimiter(IP_LIMIT, IP_WINDOW_SECONDS, **kwargs)
        self.quote_limiter = WindowLimiter(QUOTE_LIMIT, 60.0, **kwargs)
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        if not self._secret:
            logger.warning("TURNSTILE_SECRET berilmagan - sayt buyurtmalari faqat IP cheklovi bilan himoyalangan")

    def register(self, app: web.Application) -> None:
        app.router.add_route("POST", WEB_ORDERS_PATH, self.handle_create)
        app.router.add_route("OPTIONS", WEB_ORDERS_PATH, self.handle_preflight)
        app.router.add_route("GET", WEB_CONFIG_PATH, self.handle_config)
        app.router.add_route("GET", WEB_QUOTE_PATH, self.handle_quote)
        app.router.add_route("OPTIONS", WEB_CONFIG_PATH, self.handle_get_preflight)
        app.router.add_route("OPTIONS", WEB_QUOTE_PATH, self.handle_get_preflight)

    def _origin(self, request: web.Request) -> str | None:
        origin = request.headers.get("Origin", "").lower().rstrip("/")
        return origin if origin in self._origins else None

    def _json(self, request: web.Request, status: int, body: dict, **extra: str) -> web.Response:
        headers = {"Vary": "Origin", "Cache-Control": "no-store", **extra}
        origin = self._origin(request)
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
        return web.json_response(body, status=status, headers=headers)

    async def handle_preflight(self, request: web.Request) -> web.Response:
        origin = self._origin(request)
        if not origin:
            return web.Response(status=403)
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "600", "Vary": "Origin",
        })

    async def handle_get_preflight(self, request: web.Request) -> web.Response:
        origin = self._origin(request)
        if not origin:
            return web.Response(status=403)
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET",
            "Access-Control-Max-Age": "600", "Vary": "Origin",
        })

    async def handle_config(self, request: web.Request) -> web.Response:
        """Yetkazish tarifi (sayt matnlari uchun): {"base_km", "base_fee", "extra_per_km"}."""
        if self._origin(request) is None:
            return self._json(request, 403, {"error": "forbidden"})
        return self._json(request, 200, self._delivery.tariff.as_json(), **{"Cache-Control": "public, max-age=3600"})

    async def handle_quote(self, request: web.Request) -> web.Response:
        """Joylashuv bo'yicha aniq yetkazish narxi: {"km", "fee", "approx"} (hisob serverda, buyurtmadagi bilan bir xil)."""
        if self._origin(request) is None:
            return self._json(request, 403, {"error": "forbidden"})
        wait = self.quote_limiter.retry_after(client_ip(request))
        if wait > 0:
            return self._json(request, 429, {"error": "too_many_requests"}, **{"Retry-After": str(max(1, round(wait)))})
        try:
            lat, lon = float(request.query.get("lat", "")), float(request.query.get("lon", ""))
        except ValueError:
            return self._json(request, 400, {"error": "bad_request"})
        if not valid_point(lat, lon):
            return self._json(request, 400, {"error": "bad_request"})
        import handlers
        quote = await handlers.safe_quote(self._delivery, lat, lon)
        return self._json(request, 200, quote.as_json())

    async def _turnstile(self, token: str, ip: str) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(TURNSTILE_URL, data={"secret": self._secret, "response": token,
                                                              "remoteip": ip}) as resp:
                    return bool((await resp.json()).get("success"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Turnstile tekshiruvi bajarilmadi (%s)", type(exc).__name__)
            return False

    async def handle_create(self, request: web.Request) -> web.Response:
        if self._origin(request) is None:
            return self._json(request, 403, {"error": "forbidden"})
        if self._ready is not None and not self._ready.is_set():
            return self._json(request, 503, {"error": "unavailable"})
        ip = client_ip(request)
        wait = self.ip_limiter.retry_after(ip)
        if wait > 0:
            return self._json(request, 429, {"error": "too_many_requests"}, **{"Retry-After": str(max(1, round(wait)))})
        if request.content_type != "application/json":
            return self._json(request, 400, {"error": "bad_request"})
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            return self._json(request, 413, {"error": "too_large"})
        body = await request.content.read(MAX_BODY_BYTES + 1)
        if len(body) > MAX_BODY_BYTES:
            return self._json(request, 413, {"error": "too_large"})
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return self._json(request, 400, {"error": "bad_request"})
        if not isinstance(data, dict):
            return self._json(request, 400, {"error": "bad_request"})

        token = data.pop("cf_token", None)
        if self._secret:
            if not isinstance(token, str) or not token or not await self._verify_captcha(token, ip):
                return self._json(request, 403, {"error": "captcha"})

        ref = extract_ref(data)
        phone = data.get("phone")
        if ref is None or not isinstance(phone, str):
            return self._json(request, 400, {"error": "bad_request"})
        if not PHONE_RE.fullmatch(phone):
            return self._json(request, 200, {"status": "rejected", "n": None, "reason": "val.phone",
                                             "message": None})
        uid = web_user_id(phone)
        name = data.get("name")
        user = User(id=uid, is_bot=False, first_name=(name[:64] if isinstance(name, str) and name else "Sayt"),
                    language_code=data.get("lang") if data.get("lang") in ("uz", "ru") else None)
        texts: list[str] = []

        async def reply(text: str, **_kw: Any) -> None:
            texts.append(text)  # Telegram'ga yuborilmaydi: sayt natijani javobdan oladi

        try:
            lang = await self._db.get_user_lang(uid)
        except Exception:  # noqa: BLE001
            lang = None
        key = (uid, ref)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                outcome = await self._process(
                    bot=self._bot, config=self._config, menu_store=self._menu_store, db=self._db,
                    limiter=self._limiter, user=user, raw=json.dumps(data, ensure_ascii=False),
                    lang=lang, reply=reply, delivery=self._delivery)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sayt buyurtmasi qayta ishlanmadi (%s)", type(exc).__name__)
            return self._json(request, 503, {"error": "unavailable"})
        finally:
            with contextlib.suppress(KeyError):
                if not lock.locked():
                    del self._locks[key]
        result = outcome.as_json()
        result["message"] = plain_text(texts[0]) if outcome.status == "rejected" and texts else None
        return self._json(request, 200, result)
