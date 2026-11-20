"""Mini App uchun buyurtma holati: GET /api/order-status?ref=<client_ref>.

Mini App ochiq turganda bot unga natija yubora olmaydi - shuning uchun Mini App holatni o'zi so'raydi.

Xavfsizlik - buyurtmaning client_ref'i o'zi sir:
- Yangi ref'lar 32 belgi (128 bit, crypto.getRandomValues) - taxmin qilib bo'lmaydi.
- Klaviatura tugmasidan ochilgan Mini App'da Telegram initData BERMAYDI - shuning uchun initData shart emas.
  Kelsa va imzosi to'g'ri bo'lsa (HMAC-SHA256, "WebAppData" + bot tokeni, constant-time, 24 soat) -
  buyurtma shu foydalanuvchiniki ekani ham tekshiriladi; noto'g'ri initData e'tiborsiz qoldiriladi.
- Javobda faqat status / n / reason (ism, telefon, manzil yo'q).
- So'rov cheklovi: har ref'ga 2 soniyada 1 ta; IP boshiga daqiqasiga 120 ta; eski QISQA ref'lar
  (16 belgidan kam - taxmin qilish osonroq) uchun IP boshiga daqiqasiga 30 ta (429).
- CORS faqat GET va faqat Mini App origin'i (WEBAPP_URL) uchun, OPTIONS preflight bilan.
- Xatolarda umumiy xabar; logda initData, token, ref va shaxsiy ma'lumot yo'q.
- Baza tayyor emas yoki xato - 503 (/health ga ta'sir qilmaydi).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

from validators import REF_RE

logger = logging.getLogger(__name__)

STATUS_PATH = "/api/order-status"
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60
INIT_DATA_MAX_FUTURE_SECONDS = 5 * 60     # soat farqi uchun kichik zaxira
INIT_DATA_MAX_LEN = 4096
SHORT_REF_LEN = 16                        # bundan qisqa ref'lar - eski, kuchsiz
REF_INTERVAL_SECONDS = 2.0                # har ref'ga: 2 soniyada 1 so'rov
IP_LIMIT_PER_MINUTE = 120                 # IP boshiga (uzun ref'lar)
SHORT_REF_IP_LIMIT_PER_MINUTE = 30        # IP boshiga (qisqa ref'lar) - taxmin qilishga qarshi
_TABLE_MAX = 20_000
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
    if not received or not received.isascii() or not hmac.compare_digest(
            init_data_hash(fields, bot_token), received.lower()):
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


def client_ip(request: web.Request) -> str:
    """Mijoz IP: Render oldidagi Cloudflare CF-Connecting-IP ni o'zi qo'yadi (mijoz soxtalashtira olmaydi);
    bo'lmasa X-Forwarded-For ning birinchisi, u ham bo'lmasa to'g'ridan-to'g'ri ulanish."""
    cf = request.headers.get("CF-Connecting-IP", "").strip()
    if cf:
        return cf
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.remote or "-"


class WindowLimiter:
    """Kalit boshiga: `window` soniyada ko'pi bilan `limit` ta so'rov (sirpanuvchi oyna)."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[Any, deque] = {}

    def retry_after(self, key: Any) -> float:
        """0 - ruxsat (va hisobga olinadi); >0 - shuncha soniya kutish kerak."""
        now = self._clock()
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= _TABLE_MAX:
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self._window}
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return self._window - (now - hits[0])
        hits.append(now)
        return 0.0


class OrderStatusAPI:
    def __init__(self, *, db: Any, bot_token: str, allowed_origin: str, ready: asyncio.Event | None = None,
                 clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._db = db
        self._token = bot_token
        self._origin = allowed_origin.lower().rstrip("/")
        self._ready = ready
        self._clock = clock
        self.ref_limiter = WindowLimiter(1, REF_INTERVAL_SECONDS, monotonic)
        self.ip_limiter = WindowLimiter(IP_LIMIT_PER_MINUTE, 60.0, monotonic)
        self.short_ref_ip_limiter = WindowLimiter(SHORT_REF_IP_LIMIT_PER_MINUTE, 60.0, monotonic)

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

    def _too_many(self, request: web.Request, wait: float) -> web.Response:
        return self._json(request, 429, {"error": "too_many_requests"}, **{"Retry-After": str(max(1, round(wait)))})

    def _user_from_init_data(self, request: web.Request) -> int | None:
        """initData ixtiyoriy: bo'sh yoki noto'g'ri bo'lsa None (rad etilmaydi - ref'ning o'zi sir)."""
        init_data = init_data_from_request(request)
        if not init_data:
            return None
        try:
            return validate_init_data(init_data, self._token, now=self._clock())
        except InitDataError:
            return None

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
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": ALLOWED_HEADERS,
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        })

    async def handle_status(self, request: web.Request) -> web.Response:
        if not self._origin_ok(request):
            return self._json(request, 403, {"error": "forbidden"})
        ref = request.query.get("ref", "")
        if not REF_RE.fullmatch(ref):
            return self._json(request, 400, {"error": "bad_request"})
        ip = client_ip(request)
        if len(ref) < SHORT_REF_LEN:
            wait = self.short_ref_ip_limiter.retry_after(ip)
            if wait > 0:
                return self._too_many(request, wait)
        for limiter, key in ((self.ip_limiter, ip), (self.ref_limiter, ref)):
            wait = limiter.retry_after(key)
            if wait > 0:
                return self._too_many(request, wait)
        if self._ready is not None and not self._ready.is_set():
            return self._json(request, 503, {"error": "unavailable"})
        user_id = self._user_from_init_data(request)
        try:
            result = await self._db.order_status(ref, user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - baza ulanmagan/uzilgan: tafsilot javobga ham, logga ham yo'q
            logger.warning("Buyurtma holati olinmadi (%s)", type(exc).__name__)
            return self._json(request, 503, {"error": "unavailable"})
        return self._json(request, 200, {
            "status": result["status"],
            "n": result.get("n"),
            "reason": result.get("reason"),
        })
