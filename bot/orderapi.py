"""Mini App API (bot serveri, faqat webhook rejimida):

- POST /api/orders              - buyurtma (inline/menyu tugmasidan ochilgan Mini App; initData SHART).
- GET  /api/order-status?ref=   - buyurtma holati (sendData zaxira yo'li va tarmoq xatosidan keyin tekshirish).

POST /api/orders:
- `Authorization: tma <initData>` - imzo Telegram qoidasi bo'yicha (HMAC-SHA256, "WebAppData" + bot tokeni,
  constant-time), auth_date 24 soatdan eski - rad. user_id FAQAT initData dan (tanadagi user_id e'tiborsiz).
  initData bo'sh yoki noto'g'ri - 401.
- Mantiq web_app_data bilan BIR XIL: handlers.process_order (narx menu.json dan, ish vaqti, hudud, 30 soniya,
  admin cheki, mijozga tasdiq). Idempotent: (user_id, client_ref) takror kelsa - avvalgi natija.
- Javob: {"status": "accepted"|"rejected", "n": raqam, "reason": kod} - shaxsiy ma'lumotsiz.
- Tana 20 KB gacha, noto'g'ri JSON - 400, foydalanuvchiga daqiqasiga 6 so'rov (429).
  CORS: POST + OPTIONS, faqat Mini App origin'i; Authorization va Content-Type sarlavhalari.

GET /api/order-status:

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
import contextlib
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from aiogram.types import User
from aiohttp import web

from validators import REF_RE, extract_ref

logger = logging.getLogger(__name__)

STATUS_PATH = "/api/order-status"
ORDERS_PATH = "/api/orders"
MAX_BODY_BYTES = 20 * 1024
SUBMIT_LIMIT_PER_MINUTE = 6               # foydalanuvchi boshiga (30 soniyalik buyurtma cheklovi alohida)
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
    return parse_init_data(init_data, bot_token, now=now, max_age=max_age)["id"]


def parse_init_data(init_data: str, bot_token: str, *, now: float | None = None,
                    max_age: float = INIT_DATA_MAX_AGE_SECONDS) -> dict:
    """initData ni tekshiradi va foydalanuvchi ma'lumotini qaytaradi (id - int, qolganlari - str yoki None)."""
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

    def text(key: str, limit: int = 128) -> str | None:
        value = user.get(key)
        return value[:limit] if isinstance(value, str) and value else None

    return {"id": user_id, "first_name": text("first_name") or "", "last_name": text("last_name"),
            "username": text("username", 64), "language_code": text("language_code", 16)}


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
        return preflight(request, self._origin, "GET", ALLOWED_HEADERS)

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


def preflight(request: web.Request, origin: str, method: str, headers: str) -> web.Response:
    """CORS preflight: faqat Mini App origin'i va faqat shu marshrutning usuli."""
    if request.headers.get("Origin", "").lower().rstrip("/") != origin:
        return web.Response(status=403)
    if request.headers.get("Access-Control-Request-Method", method).upper() != method:
        return web.Response(status=403)
    return web.Response(status=204, headers={
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": method,
        "Access-Control-Allow-Headers": headers,
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    })


class OrderSubmitAPI:
    """POST /api/orders - Mini App buyurtmasi HTTP orqali (inline/menyu tugmasidan ochilganda)."""

    def __init__(self, *, bot: Any, config: Any, menu_store: Any, db: Any, limiter: Any,
                 ready: asyncio.Event | None = None, process: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic) -> None:
        if process is None:
            import handlers  # handlers orderapi'ni import qilmaydi - aylana yo'q
            process = handlers.process_order
        self._bot = bot
        self._config = config
        self._menu_store = menu_store
        self._db = db
        self._limiter = limiter          # 30 soniyalik buyurtma cheklovi - web_app_data bilan UMUMIY
        self._ready = ready
        self._process = process
        self._clock = clock
        self._origin = origin_of(config.webapp_url)
        self.user_limiter = WindowLimiter(SUBMIT_LIMIT_PER_MINUTE, 60.0, monotonic)
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def register(self, app: web.Application) -> None:
        app.router.add_route("POST", ORDERS_PATH, self.handle_create)
        app.router.add_route("OPTIONS", ORDERS_PATH, self.handle_preflight)

    async def handle_preflight(self, request: web.Request) -> web.Response:
        return preflight(request, self._origin, "POST", "Authorization, Content-Type")

    def _json(self, request: web.Request, status: int, body: dict, **extra: str) -> web.Response:
        headers = {"Vary": "Origin", "Cache-Control": "no-store", **extra}
        if request.headers.get("Origin", "").lower().rstrip("/") == self._origin:
            headers["Access-Control-Allow-Origin"] = self._origin
        return web.json_response(body, status=status, headers=headers)

    async def _reply_to(self, user_id: int, text: str, **kwargs: Any) -> Any:
        try:
            return await self._bot.send_message(user_id, text, **kwargs)
        except Exception as exc:  # noqa: BLE001 - mijoz botni bloklagan bo'lishi mumkin
            logger.warning("Mijozga xabar yuborilmadi (user_id=%s): %s", user_id, type(exc).__name__)
            return None

    async def handle_create(self, request: web.Request) -> web.Response:
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower().rstrip("/") != self._origin:
            return self._json(request, 403, {"error": "forbidden"})
        if self._ready is not None and not self._ready.is_set():
            return self._json(request, 503, {"error": "unavailable"})
        try:
            tg = parse_init_data(init_data_from_request(request), self._config.bot_token, now=self._clock())
        except InitDataError:
            return self._json(request, 401, {"error": "unauthorized"})
        user_id = tg["id"]  # FAQAT initData dan
        wait = self.user_limiter.retry_after(user_id)
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
            raw = body.decode("utf-8")
            data = json.loads(raw)
        except (UnicodeDecodeError, ValueError, RecursionError):  # RecursionError: minglab ichma-ich "[[[[..."
            return self._json(request, 400, {"error": "bad_request"})
        ref = extract_ref(data) if isinstance(data, dict) else None
        if ref is None:  # idempotentlik uchun ref shart
            return self._json(request, 400, {"error": "bad_request"})

        user = User(id=user_id, is_bot=False, first_name=tg["first_name"], last_name=tg["last_name"],
                    username=tg["username"], language_code=tg["language_code"])
        try:
            lang = await self._db.get_user_lang(user_id)
        except Exception:  # noqa: BLE001
            lang = None

        key = (user_id, ref)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:  # bir xil (user, ref) parallel kelsa - ikkinchisi birinchisining natijasini oladi
                outcome = await self._process(
                    bot=self._bot, config=self._config, menu_store=self._menu_store, db=self._db,
                    limiter=self._limiter, user=user, raw=raw, lang=lang,
                    reply=lambda text, **kw: self._reply_to(user_id, text, **kw),
                )
        except Exception as exc:  # noqa: BLE001 - tafsilot javobga ham, logga ham yo'q
            logger.warning("Buyurtma qayta ishlanmadi (%s)", type(exc).__name__)
            return self._json(request, 503, {"error": "unavailable"})
        finally:
            with contextlib.suppress(KeyError):
                if not lock.locked():
                    del self._locks[key]
        return self._json(request, 200, outcome.as_json())


CONFIG_PATH = "/api/config"
QUOTE_PATH = "/api/delivery-quote"
QUOTE_LIMIT_PER_MINUTE = 20               # foydalanuvchi boshiga (lokatsiya o'zgarganda qayta so'raladi)


class DeliveryAPI:
    """Yetkazish narxi Mini App uchun (narx FAQAT serverda hisoblanadi - Mini App formulani takrorlamaydi):

    - GET /api/config          - autentifikatsiyasiz, keshlanadi: {"base_km", "base_fee", "extra_per_km"}.
    - GET /api/delivery-quote  - ?lat=&lon=, initData SHART (401), foydalanuvchiga daqiqasiga 20 ta (429),
      lat/lon diapazoni (400) -> {"km": 3.4, "fee": 12500, "approx": false}.
    CORS: faqat GET (+ OPTIONS), faqat Mini App origin'i.
    """

    def __init__(self, *, config: Any, delivery: Any = None, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if delivery is None:
            import handlers  # bitta umumiy nusxa (kesh va kunlik hisoblagich buyurtmalar bilan umumiy)
            delivery = handlers.default_delivery(config)
        self._config = config
        self._delivery = delivery
        self._clock = clock
        self._origin = origin_of(config.webapp_url)
        self.user_limiter = WindowLimiter(QUOTE_LIMIT_PER_MINUTE, 60.0, monotonic)

    def register(self, app: web.Application) -> None:
        app.router.add_route("GET", CONFIG_PATH, self.handle_config)
        app.router.add_route("OPTIONS", CONFIG_PATH, self.handle_config_preflight)
        app.router.add_route("GET", QUOTE_PATH, self.handle_quote)
        app.router.add_route("OPTIONS", QUOTE_PATH, self.handle_quote_preflight)

    def _headers(self, request: web.Request, cache: str = "no-store") -> dict[str, str]:
        headers = {"Vary": "Origin", "Cache-Control": cache}
        if request.headers.get("Origin", "").lower().rstrip("/") == self._origin:
            headers["Access-Control-Allow-Origin"] = self._origin
        return headers

    def _json(self, request: web.Request, status: int, body: dict, **extra: str) -> web.Response:
        return web.json_response(body, status=status, headers={**self._headers(request), **extra})

    def _origin_ok(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin")
        return origin is None or origin.lower().rstrip("/") == self._origin

    async def handle_config_preflight(self, request: web.Request) -> web.Response:
        return preflight(request, self._origin, "GET", "Content-Type")

    async def handle_quote_preflight(self, request: web.Request) -> web.Response:
        return preflight(request, self._origin, "GET", ALLOWED_HEADERS)

    async def handle_config(self, request: web.Request) -> web.Response:
        if not self._origin_ok(request):
            return self._json(request, 403, {"error": "forbidden"})
        return web.json_response(self._delivery.tariff.as_json(),
                                 headers=self._headers(request, cache="public, max-age=3600"))

    async def handle_quote(self, request: web.Request) -> web.Response:
        if not self._origin_ok(request):
            return self._json(request, 403, {"error": "forbidden"})
        try:
            user_id = parse_init_data(init_data_from_request(request), self._config.bot_token, now=self._clock())["id"]
        except InitDataError:
            return self._json(request, 401, {"error": "unauthorized"})
        wait = self.user_limiter.retry_after(user_id)
        if wait > 0:
            return self._json(request, 429, {"error": "too_many_requests"}, **{"Retry-After": str(max(1, round(wait)))})
        try:
            lat, lon = float(request.query.get("lat", "")), float(request.query.get("lon", ""))
        except ValueError:
            return self._json(request, 400, {"error": "bad_request"})
        from delivery import valid_point
        if not valid_point(lat, lon):
            return self._json(request, 400, {"error": "bad_request"})
        import handlers
        quote = await handlers.safe_quote(self._delivery, lat, lon)
        return self._json(request, 200, quote.as_json())
