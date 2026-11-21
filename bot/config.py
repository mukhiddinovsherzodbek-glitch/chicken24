"""Sozlamalar: barcha qiymatlar muhit o'zgaruvchilaridan o'qiladi.

bot/.env fayli ixtiyoriy: bo'lsa, undagi qiymatlar muhitga yuklanadi, lekin serverda
(panelda) berilgan haqiqiy muhit o'zgaruvchilari doim ustun turadi. .env bo'lmasa ham ishlaydi.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

# bot/ papkasi
BASE_DIR = Path(__file__).resolve().parent

# Menyuning yagona manbasi (Mini App bilan umumiy fayl).
# MENU_PATH muhit o'zgaruvchisi orqali o'zgartirish mumkin.
# Lokal loyihada: ../miniapp/menu.json; serverga yuklangan paketda: bot papkasidagi menu.json.
DEFAULT_MENU_PATH = BASE_DIR.parent / "miniapp" / "menu.json"
BUNDLED_MENU_PATH = BASE_DIR / "menu.json"
# SQLite fayli. DB_PATH muhit o'zgaruvchisi bilan serverdagi doimiy papkaga yo'naltiriladi.
DEFAULT_DB_PATH = BASE_DIR / "chicken24.db"
LOG_PATH = BASE_DIR / "bot.log"
# /start dagi salomlashuv rasmi (Pages'ga emas, faqat bot paketiga kiradi)
WELCOME_IMAGE_PATH = BASE_DIR / "assets" / "welcome.jpg"
WELCOME_IMAGE_RU_PATH = BASE_DIR / "assets" / "welcome_ru.jpg"  # ruscha; topilmasa welcome.jpg

# ---------------------------------------------------------------- vaqt zonasi va ish vaqti
# Samarqand vaqti (UTC+5, yozgi vaqt yo'q). Windows'da IANA bazasi yo'q - tzdata paketi kerak
# (requirements.txt da). U ham bo'lmasa, qat'iy UTC+5 ishlatiladi (natija bir xil).
TIMEZONE_NAME = "Asia/Samarkand"
UTC_OFFSET_HOURS = 5  # Mini App shu qiymatdan foydalanadi (qurilma soatiga bog'liq emas)
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata o'rnatilmagan bo'lsa
    logging.getLogger(__name__).warning("tzdata topilmadi - qat'iy UTC+%d ishlatiladi", UTC_OFFSET_HOURS)
    LOCAL_TZ = timezone(timedelta(hours=UTC_OFFSET_HOURS), name=TIMEZONE_NAME)

# Ish vaqti 10:00 - 03:00 (yarim tundan o'tadi): 10:00-23:59 va 00:00-02:59 ochiq, 03:00-09:59 yopiq.
# Mini App'dagi nusxa: miniapp/app.js -> BUSINESS (test bir xilligini tekshiradi).
OPEN_FROM = time(10, 0)
CLOSE_AT = time(3, 0)

# ---------------------------------------------------------------- narx va to'lov
# Yetkazish narxi masofaga qarab (delivery.py), sozlamalari Config da (muhit o'zgaruvchilari bilan):
# DELIVERY_BASE_FEE (10 000), DELIVERY_BASE_KM (2), DELIVERY_EXTRA_PER_KM (2 500), ROAD_FACTOR (1.3).
# Mini App narxni faqat serverdan oladi (formulani takrorlamaydi). Minimal buyurtma yo'q.
DEFAULT_DELIVERY_BASE_FEE = 10000
DEFAULT_DELIVERY_BASE_KM = 2
DEFAULT_DELIVERY_EXTRA_PER_KM = 2500
DEFAULT_ROAD_FACTOR = 1.3
PAYMENT_METHOD_TEXT = "naqd (kuryerga)"

# Spam himoyasi: bitta foydalanuvchidan shu oraliqda ko'pi bilan 1 ta buyurtma.
ORDER_COOLDOWN_SECONDS = 30

# Menyu Pages'dan (WEBAPP_URL/menu.json) shu oraliqda yangilanadi.
MENU_REFRESH_SECONDS = 300
# Pages oxirgi marta shuncha vaqt oldin olingan bo'lsa ham hali ishlatiladi; undan keyin lokal nusxa.
REMOTE_MENU_MAX_AGE_SECONDS = 900

# ---------------------------------------------------------------- zaxira (backup)
# Har kuni shu vaqtda (Samarqand) baza nusxasi admin chatiga yuboriladi - faqat yangi o'zgarish bo'lsa.
# 04:00 - restoran yopiq payt (ish 03:00 da tugaydi). /backup buyrug'i bilan istalgan payt.
BACKUP_ENABLED = True
BACKUP_TIME = time(4, 0)
# Telegram bot fayl chegarasi 50 MB; zaxira undan katta bo'lsa yuborilmaydi (admin'ga ogohlantirish).
BACKUP_MAX_BYTES = 49 * 1024 * 1024

# ---------------------------------------------------------------- bekor qilish
# Mijoz buyurtmani shu vaqt ichida (soniya) o'zi bekor qila oladi.
CANCEL_WINDOW_SECONDS = 5 * 60

# Admin'ga yetib bormagan (notified=0) buyurtmalar shu oraliqda qayta yuboriladi.
RESEND_INTERVAL_SECONDS = 120
# Yangi buyurtma hali yuborilayotgan bo'lishi mumkin - shundan yoshroqlariga tegilmaydi.
RESEND_MIN_AGE_SECONDS = 60

# ---------------------------------------------------------------- restoran joylashuvi
# Yetkazish masofasi restorandan o'lchanadi: RESTAURANT_LAT / RESTAURANT_LON (muhit o'zgaruvchilari).
# Berilmasa - VAQTINCHA Samarqand shahri markazi (ishga tushganda logga ogohlantirish).
# Hudud cheklovi yo'q: istalgan joyga yetkaziladi, narx masofaga qarab.
DEFAULT_RESTAURANT_LAT = 39.6542
DEFAULT_RESTAURANT_LON = 66.9597

REQUIRED_VARS = ("BOT_TOKEN", "ADMIN_ID", "WEBAPP_URL")

# ---------------------------------------------------------------- server (Render) rejimi
DEFAULT_PORT = 8080
# Telegram secret_token ruxsat bergan belgilar (A-Z a-z 0-9 _ -); xavfsizlik uchun kamida 16 belgi
WEBHOOK_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{16,256}")


class ConfigError(Exception):
    """Sozlamalarda xato (yetishmayotgan yoki noto'g'ri qiymat)."""


@dataclass(frozen=True)
class Config:
    # Maxfiy qiymatlar repr=False: Config tasodifan logga yozilsa ham ko'rinmaydi
    bot_token: str = field(repr=False)
    admin_id: int
    webapp_url: str
    menu_path: Path
    db_path: Path
    # Pages'dagi menu.json (None - faqat lokal nusxa)
    menu_url: str | None = None
    # PostgreSQL (DATABASE_URL). None - lokal SQLite (db_path)
    database_url: str | None = field(default=None, repr=False)
    # Webhook rejimi: ikkalasi ham berilsa. None - polling
    webhook_url: str | None = None
    webhook_secret: str | None = field(default=None, repr=False)
    # Webhook HTTP serveri porti (Render PORT o'zgaruvchisini o'zi beradi)
    port: int = DEFAULT_PORT
    # O'z-o'ziga ping manzili (<ochiq manzil>/health). None - o'chiq (lokal kompyuter)
    self_ping_url: str | None = field(default=None, repr=False)
    # Buyurtma cheklari keladigan guruh (manfiy chat ID). None - cheklar ADMIN_ID ga (avvalgidek)
    orders_chat_id: int | None = field(default=None, repr=False)
    # True - chek guruhdan tashqari admin'ning shaxsiy chatiga ham alohida yuboriladi
    orders_also_admin: bool = False
    # Restoran (yetkazish masofasi shu nuqtadan). restaurant_from_env=False - vaqtinchalik standart nuqta
    restaurant_lat: float = DEFAULT_RESTAURANT_LAT
    restaurant_lon: float = DEFAULT_RESTAURANT_LON
    restaurant_from_env: bool = False
    # Yetkazish narxi (delivery.py): base_fee - base_km gacha, keyin har km uchun extra_per_km
    delivery_base_fee: int = DEFAULT_DELIVERY_BASE_FEE
    delivery_base_km: int = DEFAULT_DELIVERY_BASE_KM
    delivery_extra_per_km: int = DEFAULT_DELIVERY_EXTRA_PER_KM
    road_factor: float = DEFAULT_ROAD_FACTOR  # zaxira: to'g'ri chiziq * shu koeffitsient
    ors_api_key: str | None = field(default=None, repr=False)  # OpenRouteService (faqat server)

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url and self.webhook_secret)


def menu_url_from_webapp(webapp_url: str) -> str:
    """https://x.pages.dev/?a=1#b -> https://x.pages.dev/menu.json"""
    base = webapp_url.split("#", 1)[0].split("?", 1)[0]
    return base.rstrip("/") + "/menu.json"


def _menu_url(webapp_url: str) -> str | None:
    """MENU_URL: berilmasa WEBAPP_URL/menu.json; 'off' - Pages'dan olinmaydi."""
    value = os.getenv("MENU_URL", "").strip()
    if value.lower() == "off":
        return None
    if value:
        if not value.lower().startswith("https://"):
            raise ConfigError("MENU_URL https:// bilan boshlanishi kerak (yoki 'off').")
        return value
    return menu_url_from_webapp(webapp_url)


def _default_menu_path() -> Path:
    """Lokal loyihada miniapp/menu.json, server paketida esa bot yonidagi menu.json."""
    if DEFAULT_MENU_PATH.is_file():
        return DEFAULT_MENU_PATH
    return BUNDLED_MENU_PATH


def _resolve_path(value: str | None, default: Path) -> Path:
    """Nisbiy yo'lni bot/ papkasiga nisbatan hisoblaydi."""
    if not value or not value.strip():
        return default
    path = Path(value.strip())
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def load_config() -> Config:
    """.env ni o'qiydi va tekshiradi. Xato bo'lsa ConfigError ko'taradi."""
    load_dotenv(BASE_DIR / ".env")

    missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise ConfigError(
            "Quyidagi o'zgaruvchilar .env faylida yo'q yoki bo'sh: "
            + ", ".join(missing)
            + ". bot/.env.example dan nusxa olib bot/.env yarating."
        )

    bot_token = os.environ["BOT_TOKEN"].strip()
    if ":" not in bot_token:
        raise ConfigError("BOT_TOKEN noto'g'ri ko'rinishda (BotFather bergan tokenni kiriting).")

    admin_raw = os.environ["ADMIN_ID"].strip()
    try:
        admin_id = int(admin_raw)
    except ValueError:
        raise ConfigError(f"ADMIN_ID butun son bo'lishi kerak, hozir: {admin_raw!r}") from None

    webapp_url = os.environ["WEBAPP_URL"].strip()
    if not webapp_url.lower().startswith("https://"):
        raise ConfigError("WEBAPP_URL https:// bilan boshlanishi kerak (Telegram faqat HTTPS qabul qiladi).")

    webhook_url, webhook_secret = _webhook_settings()
    return Config(
        bot_token=bot_token,
        admin_id=admin_id,
        webapp_url=webapp_url,
        menu_path=_resolve_path(os.getenv("MENU_PATH"), _default_menu_path()),
        db_path=_resolve_path(os.getenv("DB_PATH"), DEFAULT_DB_PATH),
        menu_url=_menu_url(webapp_url),
        database_url=_database_url(),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        port=_port(),
        self_ping_url=_self_ping_url(),
        orders_chat_id=_orders_chat_id(),
        orders_also_admin=_flag("ORDERS_ALSO_ADMIN"),
        **_delivery_settings(),
    )


def _number(name: str, default: float, *, kind: type = float, minimum: float | None = None,
            maximum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ConfigError(f"{name} {'butun son' if kind is int else 'son'} bo'lishi kerak, hozir: {raw!r}") from None
    if kind is float and value != value:  # NaN
        raise ConfigError(f"{name} son bo'lishi kerak")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ConfigError(f"{name} {minimum} .. {maximum} oralig'ida bo'lishi kerak, hozir: {raw!r}")
    return value


def _delivery_settings() -> dict:
    lat_raw = os.getenv("RESTAURANT_LAT", "").strip()
    lon_raw = os.getenv("RESTAURANT_LON", "").strip()
    if bool(lat_raw) != bool(lon_raw):
        raise ConfigError("RESTAURANT_LAT va RESTAURANT_LON ikkalasi birga berilishi kerak.")
    lat = _number("RESTAURANT_LAT", DEFAULT_RESTAURANT_LAT, minimum=-90, maximum=90)
    lon = _number("RESTAURANT_LON", DEFAULT_RESTAURANT_LON, minimum=-180, maximum=180)
    key = os.getenv("ORS_API_KEY", "").strip() or None
    return {
        "restaurant_lat": lat,
        "restaurant_lon": lon,
        "restaurant_from_env": bool(lat_raw),
        "delivery_base_fee": _number("DELIVERY_BASE_FEE", DEFAULT_DELIVERY_BASE_FEE, kind=int, minimum=0,
                                     maximum=10_000_000),
        "delivery_base_km": _number("DELIVERY_BASE_KM", DEFAULT_DELIVERY_BASE_KM, kind=int, minimum=0, maximum=1000),
        "delivery_extra_per_km": _number("DELIVERY_EXTRA_PER_KM", DEFAULT_DELIVERY_EXTRA_PER_KM, kind=int,
                                         minimum=0, maximum=10_000_000),
        "road_factor": _number("ROAD_FACTOR", DEFAULT_ROAD_FACTOR, minimum=1.0, maximum=5.0),
        "ors_api_key": key,
    }


def _orders_chat_id() -> int | None:
    """ORDERS_CHAT_ID: buyurtma cheklari guruhi (manfiy son, masalan -1001234567890). Bo'sh - ADMIN_ID ga."""
    raw = os.getenv("ORDERS_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("ORDERS_CHAT_ID butun son bo'lishi kerak (guruh ID si manfiy, masalan -1001234567890).") from None
    if value >= 0:
        raise ConfigError("ORDERS_CHAT_ID guruh ID si - manfiy son bo'lishi kerak (guruhda /guruh_id yozing).")
    return value


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "ha")


def _self_ping_url() -> str | None:
    """PUBLIC_URL (qo'lda) yoki RENDER_EXTERNAL_URL (Render o'zi beradi) + /health. Ikkalasi yo'q - None."""
    explicit = os.getenv("PUBLIC_URL", "").strip()
    value = explicit or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not value:
        return None
    if not value.lower().startswith(("https://", "http://")) or "?" in value or "#" in value:
        if explicit:
            raise ConfigError("PUBLIC_URL sayt manzili bo'lsin (https://..., ? va # siz).")
        return None  # Render qiymati kutilmagan ko'rinishda - ping o'chiq, bot ishlayveradi
    return value.rstrip("/") + "/health"


def _database_url() -> str | None:
    """DATABASE_URL: berilsa PostgreSQL, bo'lmasa lokal SQLite."""
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        return None
    if value.split("://", 1)[0].lower() not in ("postgres", "postgresql"):
        # Qiymatning o'zi xabarga qo'shilmaydi (ichida parol bor)
        raise ConfigError("DATABASE_URL postgresql:// (yoki postgres://) bilan boshlanishi kerak.")
    return value


def _webhook_settings() -> tuple[str | None, str | None]:
    """WEBHOOK_URL + WEBHOOK_SECRET -> webhook rejimi; WEBHOOK_URL bo'lmasa - polling."""
    url = os.getenv("WEBHOOK_URL", "").strip()
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not url:
        return None, None
    if not url.lower().startswith("https://"):
        raise ConfigError("WEBHOOK_URL https:// bilan boshlanishi kerak (masalan https://chicken24-bot.onrender.com).")
    if "?" in url or "#" in url:
        raise ConfigError("WEBHOOK_URL faqat sayt manzili bo'lsin (? va # siz), masalan https://chicken24-bot.onrender.com")
    if not secret:
        raise ConfigError("WEBHOOK_URL berilgan, lekin WEBHOOK_SECRET yo'q - webhook sirsiz ishlamaydi.")
    if not WEBHOOK_SECRET_RE.fullmatch(secret):
        raise ConfigError("WEBHOOK_SECRET 16-256 belgi bo'lsin, faqat A-Z, a-z, 0-9, _ va - belgilari.")
    return url.rstrip("/"), secret


def _port() -> int:
    raw = os.getenv("PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(f"PORT butun son bo'lishi kerak, hozir: {raw!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"PORT 1-65535 oralig'ida bo'lishi kerak, hozir: {port}")
    return port
