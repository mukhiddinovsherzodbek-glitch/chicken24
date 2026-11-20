"""Mini App uchun buyurtma holati: GET /api/order-status?ref=<client_ref>.

Mini App ochiq turganda bot unga natija yubora olmaydi - shuning uchun Mini App holatni o'zi so'raydi.

Xavfsizlik:
- Foydalanuvchi Telegram initData orqali aniqlanadi (sarlavha `Authorization: tma <initData>` yoki
  `X-Telegram-Init-Data`). Imzo Telegram qoidasi bo'yicha: secret = HMAC_SHA256("WebAppData", bot_token),
  hash = HMAC_SHA256(secret, data_check_string); taqqoslash constant-time. auth_date 24 soatdan eski - rad.
- user_id faqat initData dan olinadi - so'rovdan emas. Faqat o'z buyurtmasi qaytadi.
- Javobda faqat holat, buyurtma raqami va sabab kodi (ism/telefon/manzil yo'q).
- CORS faqat Mini App origin'i (WEBAPP_URL) uchun, OPTIONS preflight bilan.
- Har foydalanuvchiga 2 soniyada 1 so'rov (429). Xatolarda umumiy xabar, logda initData/token yo'q.
- Baza tayyor emas yoki xato - 503 (/health ga ta'sir qilmaydi).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

from validators import REF_RE

logger = logging.getLogger(__name__)

STATUS_PATH = "/api/order-status"
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60
INIT_DATA_MAX_FUTURE_SECONDS = 5 * 60     # soat farqi uchun kichik zaxira
INIT_DATA_MAX_LEN = 4096
RATE_LIMIT_SECONDS = 2.0
_RATE_TABLE_MAX = 10_000
ALLOWED_HEADERS = "Authorization, X-Telegram-Init-Data"


class InitDataError(Exception):
    """initData noto'g'ri, imzosi mos emas yoki eskirgan (sababi logga yozilmaydi)."""


def origin_of(url: str) -> str:
    """https://chicken24.pages.dev/?a=1 -> https://chicken24.pages.dev"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def init_data_hash(fields: dict[str, str], bot_token: str) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()


def validate_init_data(init_data: str, bot_token: str, *, now: float | None = None,
                       max_age: float = INIT_DATA_MAX_AGE_SECONDS) -> int:
    """initData ni tekshiradi va Telegram user_id ni qaytaradi. Xato bo'lsa InitDataError."""
    if not init_data or len(init_data) > INIT_DATA_MAX_LEN:
        raise InitDataError("length")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InitDataError("format") from None
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InitDataError("duplicate")
    received = fields.pop("hash", "")
    if not received or not received.isascii() or not hmac.compare_digest(init_data_hash(fields, bot_token), received.lower()):
        raise InitDataError("signature")
    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError:
        raise InitDataError("auth_date") from None
    now = time.time() if now is None else now
    if now - auth_date > max_age or auth_date - now > INIT_DATA_MAX_FUTURE_SECONDS:
        raise InitDataError("expired")
    try:
        user = json.loads(fields.get("user", ""))
        user_id = user["id"]
    except (ValueError, TypeError, KeyError):
        raise InitDataError("user") from None
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise InitDataError("user")
    return user_id


def init_data_from_request(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth[:4].lower() == "tma ":
        return auth[4:].strip()
    return request.headers.get("X-Telegram-Init-Data", "").strip()


class RateLimiter:
    """Foydalanuvchi boshiga: `interval` soniyada 1 so'rov."""

    def __init__(self, interval: float = RATE_LIMIT_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval
        self._clock = clock
        self._last: dict[int, float] = {}

    def retry_after(self, user_id: int) -> float:
        """0 - ruxsat (va belgilanadi); >0 - shuncha soniya kutish kerak."""
        now = self._clock()
        last = self._last.get(user_id)
        if last is not None and now - last < self._interval:
            return self._interval - (now - last)
        if len(self._last) >= _RATE_TABLE_MAX:
            self._last = {k: v for k, v in self._last.items() if now - v < self._interval}
        self._last[user_id] = now
        return 0.0


class OrderStatusAPI:
    def __init__(self, *, db: Any, bot_token: str, allowed_origin: str, ready: asyncio.Event | None = None,
                 limiter: RateLimiter | None = None, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._token = bot_token
        self._origin = allowed_origin.lower().rstrip("/")
        self._ready = ready
        self._limiter = limiter or RateLimiter()
        self._clock = clock

    # ------------------------------------------------------------ yordamchilar

    def _cors(self, request: web.Request) -> dict[str, str]:
        headers = {"Vary": "Origin", "Cache-Control": "no-store"}
        if request.headers.get("Origin", "").lower().rstrip("/") == self._origin:
            headers["Access-Control-Allow-Origin"] = self._origin
        return headers

    def _json(self, request: web.Request, status: int, body: dict, **extra: str) -> web.Response:
        return web.json_response(body, status=status, headers={**self._cors(request), **extra})

    def _origin_ok(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin")
        return origin is None or origin.lower().rstrip("/") == self._origin

    # ------------------------------------------------------------ marshrutlar

    def register(self, app: web.Application) -> None:
        app.router.add_route("GET", STATUS_PATH, self.handle_status)
        app.router.add_route("OPTIONS", STATUS_PATH, self.handle_preflight)

    async def handle_preflight(self, request: web.Request) -> web.Response:
        if request.headers.get("Origin", "").lower().rstrip("/") != self._origin:
            return web.Response(status=403)
        if request.headers.get("Access-Control-Request-Method", "GET").upper() != "GET":
            return web.Response(status=403)
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": self._origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": ALLOWED_HEADERS,
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        })

    async def handle_status(self, request: web.Request) -> web.Response:
        if not self._origin_ok(request):
            return self._json(request, 403, {"error": "forbidden"})
        try:
            user_id = validate_init_data(init_data_from_request(request), self._token, now=self._clock())
        except InitDataError:
            return self._json(request, 401, {"error": "unauthorized"})
        ref = request.query.get("ref", "")
        if not REF_RE.fullmatch(ref):
            return self._json(request, 400, {"error": "bad_request"})
        wait = self._limiter.retry_after(user_id)
        if wait > 0:
            return self._json(request, 429, {"error": "too_many_requests"}, **{"Retry-After": str(max(1, round(wait)))})
        if self._ready is not None and not self._ready.is_set():
            return self._json(request, 503, {"error": "unavailable"})
        try:
            result = await self._db.order_status(user_id, ref)
        except Exception as exc:  # noqa: BLE001 - baza ulanmagan/uzilgan: tafsilot javobga ham, logga ham yo'q
            logger.warning("Buyurtma holati olinmadi (%s)", type(exc).__name__)
            return self._json(request, 503, {"error": "unavailable"})
        return self._json(request, 200, {
            "status": result["status"],
            "n": result.get("n"),
            "reason": result.get("reason"),
        })
