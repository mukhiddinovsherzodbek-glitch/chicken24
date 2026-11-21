"""Bot handlerlari: til tanlash, /start, /til, Mini App buyurtmasi (web_app_data), bekor qilish,
admin buyruqlari, xatolar.

Mijozga boradigan BARCHA matnlar i18n.py da (uz/ru) - bu faylda qattiq yozilgan mijoz matni yo'q.
Admin xabarlari (chek, /hisobot, /excel, /backup) o'zbekcha qoladi.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardRemove,
    ReplyParameters,
    TelegramObject,
    User,
    WebAppInfo,
)

import backup
import business
import excel_export
import i18n
import reports
from config import (
    CANCEL_WINDOW_SECONDS,
    CLOSE_AT,
    DEFAULT_DELIVERY_BASE_FEE,
    LOCAL_TZ,
    OPEN_FROM,
    PAYMENT_METHOD_TEXT,
    WELCOME_IMAGE_PATH,
    WELCOME_IMAGE_RU_PATH,
    Config,
)
from db import CANCEL_ALREADY, CANCEL_EXPIRED, CANCEL_NOT_FOUND, UNNOTIFIED_BATCH, Database, utc_now
from i18n import format_price, t
from logsafe import mask_id
from menu import MenuError, MenuProvider
from orderapi import WindowLimiter  # orderapi handlers'ni faqat funksiya ichida import qiladi - aylana yo'q
from delivery import DeliveryService, Quote, haversine_km, km_to_tenths, fee_for_tenths
from validators import (
    ValidatedOrder,
    ValidationError,
    extract_ref,
    parse_payload,
    validate_order,
    with_delivery,
)

logger = logging.getLogger(__name__)
router = Router(name="chicken24")

CANCEL_PREFIX = "cancel:"  # callback_data: "cancel:<order_id>"
LANG_PREFIX = "lang:"  # callback_data: "lang:uz" | "lang:ru"



@lru_cache(maxsize=8)
def default_delivery(config: Config) -> DeliveryService:
    """process_order'ga xizmat berilmasa (testlar, sayt) - sozlamadan yasalgan bitta umumiy nusxa."""
    return DeliveryService.from_config(config)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Faqat admin uchun buyruqlar (til tanlanmagan bo'lsa ham ishlaydi; ADMIN_ID va shaxsiy chat har birida tekshiriladi)
ADMIN_COMMANDS = {"backup", "hisobot", "excel", "excel_bugun", "excel_hafta", "excel_oy", "excel_hammasi"}
# Guruhda ishlaydigan YAGONA buyruq: guruh chat ID sini bilish (faqat ADMIN_ID egasi uchun)
GROUP_ID_COMMAND = "guruh_id"

# Admin xabarlari uchun til (o'zbekcha qoladi)
ADMIN_LANG = "uz"

# Flood himoyasi: bitta foydalanuvchidan shu oraliqda ko'pi bilan shuncha xabar/tugma bosish qayta ishlanadi,
# ortig'i JIM tashlab yuboriladi (har bir xabar bazaga so'rov va Telegram'ga 2-4 chaqiruv degani).
# Odam qo'lda bunga yetmaydi; Mini App buyurtmasi va kontakt bundan mustasno (ularning o'z cheklovi bor).
FLOOD_LIMIT = 15
FLOOD_WINDOW_SECONDS = 10.0

# Admin'ga ogohlantirishlar (rad etilgan buyurtma, xato): bir xil sarlavha + foydalanuvchi shu oraliqda 1 marta,
# hammasi bo'lib esa ADMIN_NOTICE_BURST[1] soniyada ko'pi bilan ADMIN_NOTICE_BURST[0] ta. Aks holda bitta
# g'araz foydalanuvchi noto'g'ri buyurtmalar bilan admin chatini to'ldirib, haqiqiy cheklarni ko'mib yuboradi.
ADMIN_NOTICE_COOLDOWN_SECONDS = 5 * 60
ADMIN_NOTICE_BURST = (20, 10 * 60.0)

_UI_CACHE_MAX = 50_000


class BotState:
    """Jarayon xotirasidagi holat (bot nusxasiga bog'langan; qayta ishga tushganda nollanadi - zarari yo'q)."""

    def __init__(self) -> None:
        self.kb_removed: set[int] = set()       # eski pastki klaviaturasi olib tashlangan mijozlar
        self.menu_lang: dict[int, str] = {}     # mijoz -> menyu tugmasi o'rnatilgan til
        self.notice_same = WindowLimiter(1, ADMIN_NOTICE_COOLDOWN_SECONDS)
        self.notice_all = WindowLimiter(*ADMIN_NOTICE_BURST)


_bot_states: "weakref.WeakKeyDictionary[Any, BotState]" = weakref.WeakKeyDictionary()


def bot_state(bot: Any) -> BotState:
    try:
        state = _bot_states.get(bot)
        if state is None:
            state = _bot_states[bot] = BotState()
        return state
    except TypeError:  # kalit bo'la olmaydigan obyekt (testdagi soxta bot) - holat saqlanmaydi
        return BotState()


# ---------------------------------------------------------------- yordamchilar

class RateLimiter:
    """Bitta foydalanuvchidan `seconds` ichida ko'pi bilan 1 ta buyurtma.

    try_acquire() sinxron (await yo'q), shuning uchun asyncio ichida ikki
    parallel xabar bir vaqtda o'tib keta olmaydi.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._last: dict[int, float] = {}

    def try_acquire(self, user_id: int) -> float:
        """0 qaytarsa - ruxsat (vaqt band qilindi). Aks holda kutish kerak bo'lgan soniyalar."""
        now = time.monotonic()
        last = self._last.get(user_id)
        if last is not None and now - last < self.seconds:
            return self.seconds - (now - last)
        self._last[user_id] = now
        if len(self._last) > 10_000:
            self._cleanup(now)
        return 0.0

    def release(self, user_id: int) -> None:
        """Buyurtma qabul qilinmagan bo'lsa, band qilingan vaqtni bo'shatadi."""
        self._last.pop(user_id, None)

    def _cleanup(self, now: float) -> None:
        for uid in [u for u, t_ in self._last.items() if now - t_ >= self.seconds]:
            del self._last[uid]


def webapp_url_with_result(base_url: str, result: dict[str, str] | None) -> str:
    """Past darajadagi yordamchi: havolaga parametrlar qo'shadi (mavjud ?/# saqlanadi).
    To'g'ridan-to'g'ri ishlatmang - tugma havolasi faqat build_webapp_url() orqali yasaladi."""
    if not result:
        return base_url
    base, _, fragment = base_url.partition("#")
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}{urlencode(result)}"
    return f"{url}#{fragment}" if fragment else url


def result_params(result: dict | None) -> dict[str, str]:
    """db.latest_result() -> havola parametrlari: ok=<ref>&n=<raqam> | rej=<ref>&r=<sabab> | cx=<ref>."""
    if not result or not result.get("ref"):
        return {}
    ref = str(result["ref"])
    if result["kind"] == "ok":
        return {"ok": ref, "n": str(result["order_id"])}
    if result["kind"] == "cx":
        return {"cx": ref}
    if result["kind"] == "rej":
        return {"rej": ref, "r": str(result.get("reason") or "data")}
    return {}


async def build_webapp_url(config: Config, db: Database | None, user_id: int, lang: str | None, *,
                           with_result: bool = True) -> str:
    """Mini App havolasi - YAGONA joy (inline tugma, menyu tugmasi). ?lang= doim; with_result=True bo'lsa
    mijozning eng oxirgi buyurtmasi natijasi ham bazadan (?ok= / ?rej= / ?cx= - eski sendData zaxira yo'li uchun;
    Mini App uni idempotent qo'llaydi). Menyu tugmasi uchun with_result=False (u kamdan-kam yangilanadi)."""
    params = {"lang": i18n.normalize(lang)}
    if db is not None and with_result:
        try:
            params.update(result_params(await db.latest_result(user_id)))
        except Exception:
            logger.exception("Oxirgi buyurtma natijasini o'qib bo'lmadi (user_id=%s) - faqat ?lang=", user_id)
    return webapp_url_with_result(config.webapp_url, params)


async def webapp_info(config: Config, db: Database | None, user_id: int, lang: str | None, *,
                      with_result: bool = True) -> WebAppInfo:
    return WebAppInfo(url=await build_webapp_url(config, db, user_id, lang, with_result=with_result))


async def order_keyboard(config: Config, db: Database | None, user_id: int, lang: str | None,
                         extra_rows: list[list[InlineKeyboardButton]] | None = None,
                         feedback: bool = False) -> InlineKeyboardMarkup:
    """Xabar ostidagi INLINE "Buyurtma berish" (web_app) tugmasi. Inline/menyu tugmasidan ochilgan Mini App'da
    Telegram initData beradi - buyurtma bot serveriga HTTP orqali (POST /api/orders) yuboriladi.
    feedback=True - yonida (shu qatorda) "Taklif va shikoyat" URL tugmasi."""
    lang = i18n.normalize(lang)
    button = InlineKeyboardButton(text=t(lang, "btn.order"), web_app=await webapp_info(config, db, user_id, lang))
    row = [button, feedback_button(config, lang)] if feedback else [button]
    return InlineKeyboardMarkup(inline_keyboard=[*(extra_rows or []), row])


def feedback_button(config: Config, lang: str | None) -> InlineKeyboardButton:
    """"Taklif va shikoyat" -> https://t.me/<FEEDBACK_USERNAME>."""
    return InlineKeyboardButton(text=t(i18n.normalize(lang), "btn.feedback"), url=config.feedback_url)


async def remove_reply_keyboard(bot: Bot, chat_id: int) -> bool:
    """Eski pastki (reply) klaviaturani olib tashlash: ReplyKeyboardRemove bilan qisqa xabar va darhol o'chirish.
    Xabar yuborilgan bo'lsa True."""
    try:
        sent = await bot.send_message(chat_id, "⌛", reply_markup=ReplyKeyboardRemove())
    except Exception:
        logger.exception("Pastki klaviatura olib tashlanmadi (chat_id=%s)", chat_id)
        return False
    try:
        await bot.delete_message(chat_id, sent.message_id)
    except Exception:
        pass
    return True


async def set_menu_button(bot: Bot, config: Config, db: Database | None, user_id: int, lang: str | None) -> bool:
    """Matn maydoni yonidagi menyu tugmasi (shu mijoz chati uchun): "Buyurtma" / "Заказ" -> Mini App.
    O'rnatilgan bo'lsa True."""
    lang = i18n.normalize(lang)
    try:
        await bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonWebApp(text=t(lang, "menu.btn"),
                                         web_app=await webapp_info(config, db, user_id, lang, with_result=False)),
        )
    except Exception:
        logger.exception("Menyu tugmasi o'rnatilmadi (user_id=%s)", user_id)
        return False
    return True


async def switch_to_inline(bot: Bot, config: Config, db: Database | None, user_id: int, lang: str | None) -> None:
    """Mijozni yangi interfeysga o'tkazish: pastki klaviatura olib tashlanadi, menyu tugmasi o'rnatiladi.

    Har mijoz uchun BIR MARTA (jarayon ishlab turganda): klaviatura bir marta olib tashlanadi, menyu tugmasi esa
    faqat til o'zgarganda qayta o'rnatiladi. Aks holda har bir oddiy xabarda chatda "⌛" lip etib ko'rinib-yo'qolardi
    va javobdan oldin Telegram'ga 3 ta ortiqcha so'rov ketardi. Muvaffaqiyatsiz urinish eslab qolinmaydi."""
    state = bot_state(bot)
    lang = i18n.normalize(lang)
    if len(state.kb_removed) > _UI_CACHE_MAX or len(state.menu_lang) > _UI_CACHE_MAX:
        state.kb_removed.clear()
        state.menu_lang.clear()
    if user_id not in state.kb_removed and await remove_reply_keyboard(bot, user_id):
        state.kb_removed.add(user_id)
    if state.menu_lang.get(user_id) != lang and await set_menu_button(bot, config, db, user_id, lang):
        state.menu_lang[user_id] = lang


WELCOME_FLAG = "w"  # callback_data "lang:ru:w" - til tanlangach salomlashuv yuboriladi (/start dan)


def lang_keyboard(welcome: bool = False) -> InlineKeyboardMarkup:
    """welcome=True (/start): tanlangach salomlashuv (rasm + matn + inline "Buyurtma berish").
    welcome=False (/til): tanlangach qisqa tasdiq + inline "Buyurtma berish"."""
    suffix = f":{WELCOME_FLAG}" if welcome else ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("uz", "lang.btn_uz"), callback_data=f"{LANG_PREFIX}uz{suffix}"),
        InlineKeyboardButton(text=t("ru", "lang.btn_ru"), callback_data=f"{LANG_PREFIX}ru{suffix}"),
    ]])


async def send_lang_prompt(bot: Bot, chat_id: int, welcome: bool = False) -> None:
    """Til tanlash xabari (ikki tilda) va inline tugmalar."""
    try:
        await bot.send_message(chat_id, t("uz", "lang.prompt"), reply_markup=lang_keyboard(welcome))
    except Exception:
        logger.exception("Til tanlash xabari yuborilmadi (chat_id=%s)", chat_id)


def format_phone(phone: str) -> str:
    # +998901234567 -> +998 90 123 45 67
    d = phone[4:]
    if len(d) != 9:
        return phone
    return f"+998 {d[0:2]} {d[2:5]} {d[5:7]} {d[7:9]}"


def maps_url(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat:.6f},{lon:.6f}"


def user_label(user: User) -> str:
    """Admin uchun foydalanuvchi haqida qisqa ma'lumot (HTML, tozalangan)."""
    parts = [f"ID <code>{user.id}</code>"]
    if user.username:
        parts.append("@" + html.escape(user.username))
    parts.append(f'<a href="tg://user?id={user.id}">profil</a>')
    return ", ".join(parts)


# ---------------------------------------------------------------- til: oraliq qatlam (middleware)

def _command_name(text: str | None) -> str | None:
    """'/excel_bugun@bot arg' -> 'excel_bugun'"""
    if not text or not text.startswith("/"):
        return None
    return text[1:].split(maxsplit=1)[0].split("@", 1)[0].lower() if len(text) > 1 else None


def _is_private_chat(event: TelegramObject) -> bool:
    """Bot faqat shaxsiy chatda ishlaydi. Guruhga qo'shib qo'yilsa: xabarlarga javob bermaydi (aks holda admin
    guruhda /excel yoki /hisobot yozsa, mijozlar ma'lumoti va tushum guruhga chiqib ketardi)."""
    if isinstance(event, Message):
        chat = event.chat
    elif isinstance(event, CallbackQuery):
        chat = getattr(event.message, "chat", None)
    else:
        return True
    return chat is None or getattr(chat, "type", "private") == "private"


async def _answer_quietly(event: TelegramObject) -> None:
    """Tugma bosilgan bo'lsa - "yuklanmoqda" belgisi qolib ketmasligi uchun bo'sh javob."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass


class LanguageMiddleware(BaseMiddleware):
    """Har bir xabar/tugma bosishdan oldin: chat turi va flood tekshiruvi, so'ng mijoz tilini aniqlab
    handlerga `lang` beradi.

    0) Shaxsiy chat bo'lmasa (guruh, kanal) - jim (handler ishlamaydi). Yagona istisno: guruhdagi /guruh_id.
    1) Flood: FLOOD_WINDOW_SECONDS ichida FLOOD_LIMIT tadan ortiq xabar/tugma - ortig'i jim tashlanadi
       (admin, Mini App buyurtmasi va kontakt mustasno).
    2) Tili tanlanmagan mijoz (yangi yoki eski) nima yozsa yoki bossa ham - avval til tanlash xabari.
    Istisnolar:
      - til tanlash tugmasining o'zi;
      - Mini App buyurtmasi (web_app_data) - buyurtma yo'qolmasligi uchun: til payload'dan olinadi va saqlanadi;
      - Telegram kontakti (Mini App "Telegram raqamim") - javobsiz saqlanadi;
      - admin buyruqlari (faqat ADMIN_ID dan).
    """

    def __init__(self) -> None:
        self._flood = WindowLimiter(FLOOD_LIMIT, FLOOD_WINDOW_SECONDS)
        self._flood_logged = WindowLimiter(1, 60.0)  # logga har foydalanuvchi uchun daqiqada 1 qator

    def _flooding(self, event: TelegramObject, user: User, config: Config | None) -> bool:
        if config is not None and user.id == config.admin_id:
            return False
        if isinstance(event, Message) and (event.web_app_data is not None or event.contact is not None):
            return False
        if self._flood.retry_after(user.id) <= 0:
            return False
        if self._flood_logged.retry_after(user.id) <= 0:
            logger.warning("Flood: user_id=%s juda ko'p xabar yuboryapti - ortig'i tashlab yuborilmoqda", user.id)
        return True

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not _is_private_chat(event):
            # Guruhda yagona istisno - /guruh_id (handler o'zi ADMIN_ID va guruh ekanini tekshiradi)
            if isinstance(event, Message) and _command_name(event.text) == GROUP_ID_COMMAND:
                return await handler(event, data)
            await _answer_quietly(event)
            return None
        user: User | None = data.get("event_from_user") or getattr(event, "from_user", None)
        db: Database | None = data.get("db")
        if user is None or db is None:
            return await handler(event, data)
        if self._flooding(event, user, data.get("config")):
            await _answer_quietly(event)
            return None
        try:
            lang = await db.get_user_lang(user.id)
        except Exception:
            logger.exception("Mijoz tilini o'qib bo'lmadi (user_id=%s)", user.id)
            lang = None
        data["lang"] = lang
        if lang:
            return await handler(event, data)

        # --- til hali tanlanmagan
        if isinstance(event, CallbackQuery) and (event.data or "").startswith(LANG_PREFIX):
            return await handler(event, data)
        if isinstance(event, Message):
            if event.web_app_data is not None or event.contact is not None:
                return await handler(event, data)
            config: Config | None = data.get("config")
            if config is not None and user.id == config.admin_id and _command_name(event.text) in ADMIN_COMMANDS:
                return await handler(event, data)
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except Exception:
                pass
        bot: Bot | None = data.get("bot")
        if bot is not None:
            await send_lang_prompt(bot, user.id)
        return None


# Bitta nusxa: flood hisobi xabarlar va tugma bosishlar uchun UMUMIY
_middleware = LanguageMiddleware()
router.message.outer_middleware(_middleware)
router.callback_query.outer_middleware(_middleware)


# ---------------------------------------------------------------- admin cheki (o'zbekcha)

@dataclass(frozen=True)
class ReceiptData:
    """Admin cheki uchun ma'lumot: yangi buyurtmadan ham, bazadan ham yasaladi."""

    order_id: int
    created_at: datetime
    user_id: int
    username: str | None
    name: str
    phone: str
    address_type: str
    address_text: str | None
    lat: float | None
    lon: float | None
    address_details: str | None
    comment: str | None
    lines: tuple[tuple[str, int, int], ...]  # (nom, soni, qator summasi)
    total: int  # umumiy
    items_total: int
    delivery_fee: int
    phone_verified: bool = False  # telefon mijozning Telegram raqami bilan bir xil
    distance_km: float | None = None  # restorandan yo'l masofasi (eski buyurtmalarda None)
    distance_approx: bool = False     # taxminiy masofa (ORS emas)

    @classmethod
    def from_order(
        cls, order_id: int, created_at: datetime, user: User, order: ValidatedOrder, phone_verified: bool = False
    ) -> "ReceiptData":
        return cls(
            order_id=order_id,
            created_at=created_at,
            user_id=user.id,
            username=user.username,
            name=order.name,
            phone=order.phone,
            address_type=order.address_type,
            address_text=order.address_text,
            lat=order.lat,
            lon=order.lon,
            address_details=order.address_details,
            comment=order.comment,
            lines=tuple((line.name, line.qty, line.line_total) for line in order.lines),
            total=order.total,
            items_total=order.items_total,
            delivery_fee=order.delivery_fee,
            phone_verified=phone_verified,
            distance_km=order.distance_km,
            distance_approx=order.distance_approx,
        )

    @classmethod
    def from_db(cls, row: dict, items: list[dict]) -> "ReceiptData":
        return cls(
            order_id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            user_id=row["user_id"],
            username=row["username"],
            name=row["customer_name"],
            phone=row["phone"],
            address_type=row["address_type"],
            address_text=row["address_text"],
            lat=row["lat"],
            lon=row["lon"],
            address_details=row["address_details"],
            comment=row["comment"],
            lines=tuple((i["name"], i["qty"], i["line_total"]) for i in items),
            total=row["total"],
            items_total=row.get("items_total") if row.get("items_total") is not None else row["total"],
            delivery_fee=row.get("delivery_fee") or 0,
            phone_verified=bool(row.get("phone_verified")),
            distance_km=row.get("distance_km"),
            distance_approx=bool(row.get("distance_approx")),
        )

    @property
    def has_location(self) -> bool:
        return self.address_type == "location" and self.lat is not None and self.lon is not None


def build_receipt(data: ReceiptData, resent: bool = False) -> str:
    """Admin uchun HTML chek (o'zbekcha). Barcha foydalanuvchi matnlari html.escape qilinadi."""
    local_time = data.created_at.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")
    lines = [f"🧾 <b>Chicken24 — yangi buyurtma #{data.order_id}</b>"]
    if resent:
        lines.append("♻️ <i>Qayta yuborildi: avval admin'ga yetib bormagan edi</i>")
    lines += [
        f"🕒 Vaqt: {local_time}",
        "",
        f"👤 Mijoz: <b>{html.escape(data.name)}</b>",
        f"📞 Telefon: {html.escape(format_phone(data.phone))}"
        + (" ✅ <i>(Telegram raqami)</i>" if data.phone_verified else ""),
    ]
    if data.user_id < 0:  # sayt buyurtmasi (webapi.py): Telegram profili yo'q
        lines.append("🌐 Manba: <b>sayt (chicken24.uz)</b>")
    else:
        if data.username:
            lines.append(f"💬 Username: @{html.escape(data.username)}")
        lines.append(f'🆔 Telegram: <code>{data.user_id}</code> (<a href="tg://user?id={data.user_id}">profil</a>)')

    if data.has_location:
        url = maps_url(data.lat, data.lon)
        lines.append(f'📍 Manzil: joriy joylashuv — <a href="{html.escape(url)}">Google Maps</a>')
        lines.append(f"<code>{data.lat:.6f}, {data.lon:.6f}</code>")
    else:
        # Qo'lda yozilgan manzil mijoz yozganidek (faqat HTML tozalanadi)
        lines.append(f"📍 Manzil: {html.escape(data.address_text or '')}")
    if data.address_details:
        lines.append(f"🏠 Uy / qavat / xonadon: {html.escape(data.address_details)}")
    if data.comment:
        lines.append(f"📝 Izoh: {html.escape(data.comment)}")

    lines.append("")
    lines.append("🍽 <b>Taomlar:</b>")
    for name, qty, line_total in data.lines:
        lines.append(f"• {html.escape(name)} × {qty} = {format_price(line_total, ADMIN_LANG)}")
    lines.append("")
    lines += [
        f"Taomlar: {format_price(data.items_total, ADMIN_LANG)}",
        f"🚚 Yetkazish: {format_price(data.delivery_fee, ADMIN_LANG)}" + distance_note(data.distance_km, data.distance_approx),
        f"💰 <b>Jami: {format_price(data.total, ADMIN_LANG)}</b>",
        f"💵 To'lov: {PAYMENT_METHOD_TEXT}",
    ]
    return "\n".join(lines)


def distance_note(distance_km: float | None, approx: bool) -> str:
    """Admin cheki: ' — 3.4 km' yoki ' — 3.4 km (taxminiy masofa)'; masofa yo'q (eski) - bo'sh."""
    if distance_km is None:
        return ""
    return f" — {distance_km:.1f} km" + (" <i>(taxminiy masofa)</i>" if approx else "")


# ---------------------------------------------------------------- mijoz matnlari

def totals_lines(items_total: int, delivery_fee: int, total: int, lang: str | None,
                 distance_km: float | None = None, approx: bool = False) -> list[str]:
    """Taomlar / Yetkazish (masofa bilan) / Jami / To'lov qatorlari - mijoz tilida."""
    if distance_km is None:
        delivery = t(lang, "totals.delivery", sum=format_price(delivery_fee, lang))
    else:
        delivery = t(lang, "totals.delivery_km", sum=format_price(delivery_fee, lang), km=f"{distance_km:.1f}",
                     approx=t(lang, "totals.approx") if approx else "")
    return [
        t(lang, "totals.items", sum=format_price(items_total, lang)),
        delivery,
        t(lang, "totals.total", sum=format_price(total, lang)),
        t(lang, "totals.payment"),
    ]


def build_customer_text(order_id: int, order: ValidatedOrder, lang: str | None = "uz") -> str:
    """Mijozga tasdiq: buyurtma raqami, taomlar (nom × soni = summa) va jami (narxlar menyudan).
    Taom nomlari tarjima qilinmaydi (ruscha xabarda ham o'zbekcha)."""
    lines = [t(lang, "order.accepted"), t(lang, "order.number", id=order_id), ""]
    for line in order.lines:
        lines.append(f"• {html.escape(line.name)} × {line.qty} = {format_price(line.line_total, lang)}")
    lines.append("")
    lines += totals_lines(order.items_total, order.delivery_fee, order.total, lang,
                          order.distance_km, order.distance_approx)
    lines.append("")
    lines.append(t(lang, "order.courier"))
    return "\n".join(lines)


async def notify_admin(bot: Bot, config: Config, title: str, user: User | None, detail: str = "") -> None:
    """Admin'ga xato haqida log (o'zbekcha). O'zi hech qachon xato tashlamaydi.
    Cheklangan (ADMIN_NOTICE_*): bir xil ogohlantirish 5 daqiqada 1 marta, hammasi 10 daqiqada 20 tagacha."""
    try:
        state = bot_state(bot)
        key = (title, user.id if user is not None else detail[:200])
        if state.notice_same.retry_after(key) > 0 or state.notice_all.retry_after("all") > 0:
            logger.info("Admin'ga ogohlantirish yuborilmadi (takror yoki juda ko'p): %s", title)
            return
        text = f"⚠️ <b>{html.escape(title)}</b>"
        if user is not None:
            text += f"\nFoydalanuvchi: {user_label(user)}"
        if detail:
            text += f"\n<pre>{html.escape(detail[:1500])}</pre>"
        await bot.send_message(config.admin_id, text, link_preview_options=NO_PREVIEW)
    except Exception:
        logger.exception("Admin'ga xato logini yuborib bo'lmadi")


async def safe_answer(message: Message, text: str, **kwargs) -> Any:
    try:
        return await message.answer(text, **kwargs)
    except Exception:
        logger.exception("Mijozga javob yuborib bo'lmadi (user_id=%s)", message.chat.id)
        return None


# ---------------------------------------------------------------- salomlashuv

CAPTION_LIMIT = 1024  # Telegram photo caption chegarasi


def build_welcome_caption(first_name: str | None, is_open: bool, lang: str | None = "uz",
                          base_fee: int = DEFAULT_DELIVERY_BASE_FEE) -> str:
    """Salomlashuv matni (HTML) mijoz tilida. Ism tozalanadi va qisqartiriladi.
    1024 belgidan (rasm izohi chegarasi) oshsa send_welcome rasmni va matnni alohida yuboradi."""
    name = html.escape((first_name or t(lang, "welcome.friend"))[:64])
    lines = [
        t(lang, "welcome.hello", name=name),
        t(lang, "welcome.title"),
        "",
        t(lang, "welcome.hours", open=f"{OPEN_FROM:%H:%M}", close=f"{CLOSE_AT:%H:%M}"),
        t(lang, "welcome.delivery", fee=format_price(base_fee, lang)),
        t(lang, "welcome.payment"),
    ]
    if not is_open:
        lines += ["", t(lang, "welcome.closed", open=f"{OPEN_FROM:%H:%M}")]
    lines += ["", t(lang, "welcome.cta")]
    return "\n".join(lines)


class WelcomePhoto:
    """Salomlashuv rasmi: birinchi marta fayldan yuklanadi, keyin Telegram file_id qayta ishlatiladi
    (har bir rasm fayli uchun alohida)."""

    file_ids: dict[str, str] = {}


def welcome_image_path(lang: str | None) -> Path:
    """uz -> welcome.jpg, ru -> welcome_ru.jpg (topilmasa welcome.jpg va logga ogohlantirish)."""
    if i18n.normalize(lang) == "ru":
        if WELCOME_IMAGE_RU_PATH.is_file():
            return WELCOME_IMAGE_RU_PATH
        logger.warning("Ruscha salomlashuv rasmi topilmadi (%s) - o'zbekcha rasm yuboriladi", WELCOME_IMAGE_RU_PATH)
    return WELCOME_IMAGE_PATH


async def send_welcome(
    bot: Bot, chat_id: int, config: Config, db: Database | None, lang: str | None, first_name: str | None
) -> None:
    """Salomlashuv: rasm (tilga mos) + matn + ostida inline "Buyurtma berish" va "Taklif va shikoyat". Izoh 1024 belgidan oshsa -
    rasm alohida, matn va tugma alohida xabarda. Rasm yuborilmasa - faqat matn va tugma.
    Shaxsiy chatda chat_id = user_id."""
    lang = i18n.normalize(lang)
    caption = build_welcome_caption(first_name, business.is_open_now(), lang, config.delivery_base_fee)
    keyboard = await order_keyboard(config, db, chat_id, lang, feedback=True)
    fits = len(caption) <= CAPTION_LIMIT
    path = welcome_image_path(lang)
    key = str(path)

    async def photo(source) -> Any:
        if fits:
            return await bot.send_photo(chat_id, source, caption=caption, reply_markup=keyboard)
        return await bot.send_photo(chat_id, source)

    sent_photo = False
    # 1) Saqlangan file_id bilan (tez, qayta yuklamasdan)
    file_id = WelcomePhoto.file_ids.get(key)
    if file_id:
        try:
            await photo(file_id)
            sent_photo = True
        except Exception:
            logger.exception("Saqlangan file_id bilan rasm yuborilmadi - fayldan qayta yuklanadi")
            WelcomePhoto.file_ids.pop(key, None)

    # 2) Fayldan (photo sifatida, hujjat emas)
    if not sent_photo:
        if path.is_file():
            try:
                sent = await photo(FSInputFile(path))
                if sent and sent.photo:
                    WelcomePhoto.file_ids[key] = sent.photo[-1].file_id
                sent_photo = True
            except Exception:
                logger.exception("Salomlashuv rasmi yuborilmadi - matnli salomlashuv yuboriladi")
        else:
            logger.error("Salomlashuv rasmi topilmadi: %s - matnli salomlashuv yuboriladi", path)

    # 3) Matn alohida: izoh sig'magan yoki rasm yuborilmagan bo'lsa
    if not (sent_photo and fits):
        await bot.send_message(chat_id, caption, reply_markup=keyboard)


# ---------------------------------------------------------------- /start, /til, til tanlash

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    """/start har doim tilni so'raydi (til saqlangan bo'lsa ham, admin uchun ham).
    Tanlangach - salomlashuv (on_lang_chosen)."""
    await send_lang_prompt(bot, message.chat.id, welcome=True)


@router.message(Command("til"))
async def cmd_lang(message: Message, bot: Bot) -> None:
    """Tilni istalgan payt almashtirish."""
    await send_lang_prompt(bot, message.chat.id)


@router.message(Command("taklif"))
async def cmd_feedback(message: Message, bot: Bot, config: Config, lang: str | None = None) -> None:
    """/taklif: qisqa matn va "Taklif va shikoyat" tugmasi (https://t.me/<FEEDBACK_USERNAME>)."""
    lang = i18n.normalize(lang)
    await bot.send_message(message.chat.id, t(lang, "feedback.text"),
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[feedback_button(config, lang)]]))


@router.callback_query(F.data.startswith(LANG_PREFIX))
async def on_lang_chosen(
    callback: CallbackQuery, bot: Bot, config: Config, db: Database, lang: str | None = None
) -> None:
    """Til tanlandi: saqlanadi (avvalgisi almashtiriladi).
    /start dan yoki birinchi marta bo'lsa - salomlashuv; /til dan - qisqa tasdiq.
    Ikkalasida ham: eski pastki klaviatura olib tashlanadi, menyu tugmasi yangi tilda o'rnatiladi,
    xabar ostida inline "Buyurtma berish".
    callback_data: "lang:<til>" yoki "lang:<til>:w" (w - salomlashuv bilan)."""
    new_lang, _, flag = (callback.data or "")[len(LANG_PREFIX):].partition(":")
    if new_lang not in i18n.LANGS or flag not in ("", WELCOME_FLAG):
        await callback.answer()
        return
    user = callback.from_user
    first_time = not lang or flag == WELCOME_FLAG
    await db.set_user_lang(user.id, new_lang, datetime.now(timezone.utc))
    logger.info("Mijoz tili: user_id=%s %s -> %s%s", user.id, lang or "-", new_lang,
                " (salomlashuv bilan)" if first_time else "")
    try:
        await callback.answer()
    except Exception:
        pass
    # Tanlash xabaridagi tugmalarni olib tashlaymiz
    try:
        if callback.message is not None:
            await callback.message.edit_text(t(new_lang, "lang.picked"))
    except Exception:
        pass
    await switch_to_inline(bot, config, db, user.id, new_lang)
    if first_time:
        await send_welcome(bot, user.id, config, db, new_lang, user.first_name)
    else:
        try:
            keyboard = await order_keyboard(config, db, user.id, new_lang)
            await bot.send_message(user.id, t(new_lang, "lang.changed"), reply_markup=keyboard)
        except Exception:
            logger.exception("Til o'zgargani haqidagi xabar yuborilmadi (user_id=%s)", user.id)


# ---------------------------------------------------------------- Mini App buyurtmasi

async def resolve_order_lang(db: Database, user: User, saved: str | None, payload: dict) -> str:
    """Buyurtma javobi tili: saqlangan til; bo'lmasa Mini App yuborgan til yoki Telegram tili (va saqlanadi)."""
    if saved:
        return saved
    payload_lang = payload.get("lang") if isinstance(payload, dict) else None
    lang = payload_lang if payload_lang in i18n.LANGS else i18n.lang_from_code(getattr(user, "language_code", None))
    try:
        await db.set_user_lang(user.id, lang, datetime.now(timezone.utc))
    except Exception:
        logger.exception("Mijoz tilini saqlab bo'lmadi (user_id=%s)", user.id)
    return lang


@dataclass(frozen=True)
class OrderOutcome:
    """Buyurtma natijasi (ikki yo'l uchun bir xil): accepted (n - raqam) yoki rejected (reason - sabab kodi)."""

    status: str
    order_id: int | None = None
    reason: str | None = None
    lang: str = "uz"
    duplicate: bool = False  # shu (user_id, ref) avval qayta ishlangan - yangi buyurtma yaratilmadi

    def as_json(self) -> dict:
        return {"status": self.status, "n": self.order_id, "reason": self.reason}


# reply(text, reply_markup=...) -> yuborilgan xabar yoki None (xato yutiladi)
Reply = Callable[..., Awaitable[Any]]


async def existing_outcome(db: Database, user_id: int, ref: str | None, lang: str) -> OrderOutcome | None:
    """Idempotentlik: shu mijozning shu ref li buyurtmasi avval qayta ishlangan bo'lsa - o'sha natija."""
    if not ref:
        return None
    try:
        found = await db.order_status(ref, user_id=user_id)
    except Exception:
        logger.exception("Avvalgi natija tekshirilmadi (user_id=%s)", user_id)
        return None
    if found["status"] in ("accepted", "cancelled"):
        return OrderOutcome("accepted", order_id=found["n"], lang=lang, duplicate=True)
    if found["status"] == "rejected":
        return OrderOutcome("rejected", reason=found["reason"], lang=lang, duplicate=True)
    return None


async def process_order(
    *,
    bot: Bot,
    config: Config,
    menu_store: MenuProvider,
    db: Database,
    limiter: RateLimiter,
    user: User,
    raw: str,
    lang: str | None,
    reply: Reply,
    delivery: DeliveryService | None = None,
) -> OrderOutcome:
    """Buyurtmani qayta ishlash - YAGONA mantiq (sendData / web_app_data va POST /api/orders uchun).

    Ish vaqti -> 30 soniyalik himoya -> tekshiruv va narxni menu.json dan hisoblash (lokatsiya shart) ->
    yetkazish narxi yo'l masofasidan (delivery.py, kesh; xato bo'lsa taxminiy) ->
    saqlash -> mijozga tasdiq (ostida inline "Buyurtma berish") -> bekor qilish tugmasi (5 daqiqa) ->
    admin'ga chek (xato bo'lsa notified=0, keyin qayta yuboriladi). Rad etilsa - sabab mijozga va bazaga
    (last_rejections). Bir xil (user_id, ref) qayta kelsa - yangi buyurtma yaratilmaydi, xabar yuborilmaydi.
    """
    try:
        data = parse_payload(raw)
        parse_error: ValidationError | None = None
    except ValidationError as exc:
        data, parse_error = {}, exc
    ref = extract_ref(data)
    lang = await resolve_order_lang(db, user, lang, data)

    previous = await existing_outcome(db, user.id, ref, lang)
    if previous is not None:
        logger.info("Takroriy buyurtma so'rovi (user_id=%s) - avvalgi natija qaytarildi: %s", user.id, previous.status)
        return previous

    async def rejected(code: str, text: str) -> OrderOutcome:
        """Rad etish: natija bazaga (last_rejections), mijozga sabab va inline "Buyurtma berish"."""
        if ref:
            try:
                await db.record_rejection(user.id, ref, code, utc_now())
            except Exception:
                logger.exception("Rad etish natijasi saqlanmadi (user_id=%s)", user.id)
        await reply(text, reply_markup=await order_keyboard(config, db, user.id, lang))
        return OrderOutcome("rejected", reason=code, lang=lang)

    # 0) Ish vaqti (asosiy tekshiruv shu yerda; yopiq bo'lsa buyurtma bazaga yozilmaydi)
    if not business.is_open_now():
        return await rejected("closed", t(lang, "order.closed", open=f"{OPEN_FROM:%H:%M}", close=f"{CLOSE_AT:%H:%M}"))

    # 1) Spam himoyasi (await'dan oldin - atomar)
    wait = limiter.try_acquire(user.id)
    if wait > 0:
        return await rejected("limit", t(lang, "order.limit", sec=math.ceil(wait)))

    # 2) Validatsiya + summani menyudan hisoblash
    try:
        if parse_error is not None:
            raise parse_error
        menu = menu_store.get()
        # Yangi buyurtma - joriy lokatsiya shart (narx masofadan); hudud cheklovi yo'q
        order = validate_order(data, menu, require_location=True)
    except ValidationError as exc:
        limiter.release(user.id)
        logger.warning("Noto'g'ri buyurtma (user_id=%s): %s", user.id, exc.detail)
        outcome = await rejected(exc.code, t(lang, "order.rejected", reason=html.escape(exc.message(lang))))
        await notify_admin(bot, config, "Buyurtma rad etildi (validatsiya)", user, f"{exc.detail}\n\n{raw[:800]}")
        return outcome
    except MenuError as exc:
        limiter.release(user.id)
        logger.exception("Menyu o'qilmadi")
        outcome = await rejected("server", f"{t(lang, 'error.generic')}\n\n{t(lang, 'error.cart_saved')}")
        await notify_admin(bot, config, "menu.json o'qilmadi", user, str(exc))
        return outcome

    # 2b) Yetkazish narxi - FAQAT serverda, yo'l masofasidan (mijoz yuborgan summa e'tiborsiz)
    quote = await safe_quote(delivery or default_delivery(config), order.lat, order.lon)
    order = with_delivery(order, quote.fee, quote.km, quote.approx)

    # 3) Saqlash (telefon mijoz Telegram orqali ulashgan raqam bilan bir xilmi - tasdiqlangan belgisi)
    created_at = utc_now()
    try:
        verified_phone = await db.get_verified_phone(user.id)
        phone_verified = verified_phone is not None and verified_phone == order.phone
        order_id = await db.save_order(
            user_id=user.id, username=user.username, order=order, created_at=created_at,
            client_ref=ref, phone_verified=phone_verified,
        )
    except Exception as exc:
        # Parallel ikkinchi so'rov unique indeksga urilgan bo'lishi mumkin - unda birinchisining natijasi
        same = await existing_outcome(db, user.id, ref, lang)
        if same is not None:
            # Buyurtma bazada bor (parallel so'rov yozgan yoki yozuv o'tib, javobi yo'qolgan). Chek admin'ga
            # yetmagan bo'lishi mumkin - fon tekshiruvi bazaga qarab, kerak bo'lsa qayta yuborsin.
            db.note_unnotified()
            return same
        limiter.release(user.id)
        logger.exception("Buyurtmani bazaga saqlashda xato")
        outcome = await rejected("server", f"{t(lang, 'error.generic')}\n\n{t(lang, 'error.cart_saved')}")
        await notify_admin(bot, config, "Buyurtmani saqlashda xato", user, f"{type(exc).__name__}: {exc}")
        return outcome

    # Tartib: 3) bazada saqlandi -> 4) mijozga tasdiq -> 5) bekor qilish tugmasi -> 6) admin'ga chek.
    # Admin'ga yetkazishdagi xato (ADMIN_ID noto'g'ri, admin /start bosmagan, Telegram xatosi) buyurtmani
    # bekor qilmaydi va mijoz tasdig'ini to'xtatmaydi: notified=0 qoladi, resend_unnotified() keyin yuboradi.

    # 4) Mijozga tasdiq o'z tilida, ostida inline "Buyurtma berish" (yangi buyurtma uchun)
    await reply(build_customer_text(order_id, order, lang), reply_markup=await order_keyboard(config, db, user.id, lang))

    # 5) Bekor qilish tugmasi - alohida xabarda (5 daqiqadan keyin tugma olib tashlanadi)
    await send_cancel_offer(bot, user.id, reply, order_id, lang)

    # 6) Admin'ga chek (o'zbekcha)
    try:
        delivered = await deliver_receipt(
            bot, config, db, ReceiptData.from_order(order_id, created_at, user, order, phone_verified=phone_verified)
        )
    except Exception:
        logger.exception("Admin'ga chek tayyorlanmadi (order_id=%s) - keyin qayta yuboriladi", order_id)
        db.note_unnotified()
        delivered = False
    logger.info("Buyurtma #%s qabul qilindi: user_id=%s, til=%s, taomlar=%s, yetkazish=%s, jami=%s, admin'ga yetdi=%s",
                order_id, user.id, lang, order.items_total, order.delivery_fee, order.total, delivered)
    if not delivered:
        # Mijoz qayta buyurtma bermasligi uchun (buyurtma saqlangan, avtomatik qayta yuboriladi)
        await reply(t(lang, "order.delayed"))
    return OrderOutcome("accepted", order_id=order_id, lang=lang)


async def safe_quote(service: DeliveryService, lat: float, lon: float) -> Quote:
    """Masofa xizmati hech qachon buyurtmani to'xtatmaydi: kutilmagan xatoda ham taxminiy masofa."""
    try:
        return await service.quote(lat, lon)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Masofa hisoblanmadi (%s) - taxminiy masofa", type(exc).__name__)
        km = haversine_km(service.origin[0], service.origin[1], lat, lon) * service.road_factor
        tenths = km_to_tenths(repr(km))
        return Quote(tenths=tenths, fee=fee_for_tenths(tenths, service.tariff), approx=True)


@router.message(F.web_app_data)
async def on_web_app_data(
    message: Message,
    bot: Bot,
    config: Config,
    menu_store: MenuProvider,
    db: Database,
    limiter: RateLimiter,
    lang: str | None = None,
    delivery: DeliveryService | None = None,
) -> None:
    """ZAXIRA yo'l: eski pastki (reply) klaviatura tugmasidan ochilgan Mini App sendData() bilan yuboradi.
    Mantiq - process_order (POST /api/orders bilan bir xil). Keyin mijoz yangi interfeysga o'tkaziladi."""
    user = message.from_user
    if user is None:
        return
    raw = message.web_app_data.data if message.web_app_data else ""

    async def reply(text: str, **kwargs) -> Any:
        return await safe_answer(message, text, **kwargs)

    outcome = await process_order(bot=bot, config=config, menu_store=menu_store, db=db, limiter=limiter,
                                  user=user, raw=raw, lang=lang, reply=reply, delivery=delivery)
    await switch_to_inline(bot, config, db, user.id, outcome.lang)


# ---------------------------------------------------------------- bekor qilish (5 daqiqa)

def cancel_keyboard(order_id: int, lang: str | None = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "cancel.btn"), callback_data=f"{CANCEL_PREFIX}{order_id}")
    ]])


async def send_cancel_offer(bot: Bot, chat_id: int, reply: Reply, order_id: int, lang: str | None = "uz") -> None:
    minutes = CANCEL_WINDOW_SECONDS // 60
    try:
        sent = await reply(t(lang, "cancel.offer", id=order_id, min=minutes), reply_markup=cancel_keyboard(order_id, lang))
    except Exception:
        logger.exception("Bekor qilish tugmasi yuborilmadi (order_id=%s)", order_id)
        return
    message_id = getattr(sent, "message_id", None)
    if message_id is not None:
        # Muddat tugagach tugma olib tashlanadi (bot qayta yonsa ham - bosilganda muddat baribir tekshiriladi)
        _keep_task(asyncio.create_task(_expire_cancel_button(bot, chat_id, message_id, order_id, lang)))


_background_tasks: set[asyncio.Task] = set()


def _keep_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _expire_cancel_button(bot: Bot, chat_id: int, message_id: int, order_id: int, lang: str | None) -> None:
    await asyncio.sleep(CANCEL_WINDOW_SECONDS + 1)
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=t(lang, "cancel.expired", id=order_id))
    except Exception:
        pass  # xabar allaqachon o'zgartirilgan (bekor qilingan) bo'lishi mumkin


@router.callback_query(F.data.startswith(CANCEL_PREFIX))
async def on_cancel_order(
    callback: CallbackQuery, bot: Bot, config: Config, db: Database, lang: str | None = None
) -> None:
    """Mijoz "Buyurtmani bekor qilish" ni bosdi: faqat o'z buyurtmasi, faqat 5 daqiqa ichida."""
    lang = i18n.normalize(lang)
    try:
        order_id = int((callback.data or "")[len(CANCEL_PREFIX):])
    except ValueError:
        await callback.answer(t(lang, "cancel.bad"), show_alert=True)
        return

    result = await db.cancel_order(order_id, callback.from_user.id, datetime.now(timezone.utc), CANCEL_WINDOW_SECONDS)
    if result == CANCEL_NOT_FOUND:
        await callback.answer(t(lang, "cancel.not_found"), show_alert=True)
        return
    if result in (CANCEL_EXPIRED, CANCEL_ALREADY):
        text = (t(lang, "cancel.already", id=order_id) if result == CANCEL_ALREADY
                else t(lang, "cancel.too_late", min=CANCEL_WINDOW_SECONDS // 60))
        await callback.answer(text, show_alert=True)
        await _safe_edit(callback, text)
        return

    # Bekor qilindi
    order = await db.get_order(order_id) or {}
    logger.info("Buyurtma #%s mijoz tomonidan bekor qilindi (user_id=%s)", order_id, callback.from_user.id)
    await callback.answer(t(lang, "cancel.done_alert"))
    await _safe_edit(callback, t(lang, "cancel.done_edit", id=order_id))

    # Mijozga: ostida inline "Buyurtma berish" (havolada bazadagi natija ?cx=<ref> - Mini App tarixi uchun)
    try:
        await bot.send_message(
            callback.from_user.id,
            t(lang, "cancel.done_msg", id=order_id),
            reply_markup=await order_keyboard(config, db, callback.from_user.id, lang),
        )
    except Exception:
        logger.exception("Mijozga bekor qilish xabari yuborilmadi (order_id=%s)", order_id)

    # Buyurtmalar chatiga (o'zbekcha): chek turgan chatda chekka javob sifatida
    # (chek yetib bormagan bo'lsa - xabar ham yuborilmaydi)
    if order.get("notified"):
        await notify_cancelled(bot, config, order_id, order)


async def notify_cancelled(bot: Bot, config: Config, order_id: int, order: dict) -> None:
    """Bekor qilish xabari chek turgan chatga (admin_chat_id; eski cheklarda - ADMIN_ID), chekka javob.
    U chatga yuborib bo'lmasa - send_to_orders_chat (guruh, keyin admin zaxirasi)."""
    text = f"🚫 <b>Mijoz buyurtma #{order_id} ni bekor qildi.</b> Tayyorlamang."
    chat_id = order.get("admin_chat_id") or config.admin_id
    kwargs: dict[str, Any] = {}
    if order.get("admin_msg_id"):
        kwargs["reply_parameters"] = ReplyParameters(message_id=order["admin_msg_id"], allow_sending_without_reply=True)
    try:
        await bot.send_message(chat_id, text, **kwargs)
        if config.orders_also_admin and chat_id != config.admin_id:
            try:
                await bot.send_message(config.admin_id, text)
            except Exception:
                pass
        return
    except Exception as exc:
        logger.warning("Bekor qilish xabari chek chatiga yuborilmadi (order_id=%s): %s", order_id, type(exc).__name__)
    try:
        await send_to_orders_chat(bot, config, text)
    except Exception:
        logger.exception("Bekor qilish xabari yuborilmadi (order_id=%s)", order_id)


async def _safe_edit(callback: CallbackQuery, text: str) -> None:
    """Tugmali xabarni oddiy matnga almashtiradi (tugma yo'qoladi)."""
    try:
        if callback.message is not None:
            await callback.message.edit_text(text)
    except Exception:
        pass


# ---------------------------------------------------------------- admin'ga chek va qayta yuborish

async def send_to_orders_chat(bot: Bot, config: Config, text: str, group_suffix: str = "",
                              **kwargs: Any) -> tuple[Any, int]:
    """Buyurtma xabari (chek, bekor qilish, ...) -> ORDERS_CHAT_ID guruhi; berilmagan bo'lsa ADMIN_ID.
    Guruhga yuborib bo'lmasa (bot chiqarilgan, guruh supergruppaga ko'chgan, ruxsat yo'q) - ZAXIRA: admin'ning
    shaxsiy chati + ogohlantirish. Admin'ga ham yetmasa - xato tashlanadi (chaqiruvchi notified=0 qoldiradi).
    ORDERS_ALSO_ADMIN=1 bo'lsa - guruhdan tashqari admin'ga ham nusxa. Qaytadi: (yuborilgan xabar, chat ID).
    group_suffix - faqat guruhdagi xabar oxiriga qo'shiladi (admin nusxasi/zaxirasida yo'q)."""
    target = config.orders_chat_id
    if target is None:
        return await bot.send_message(config.admin_id, text, **kwargs), config.admin_id
    note = "⚠️ <b>Guruhga yuborib bo'lmadi</b> — xabar shaxsiy chatga yuborildi."
    try:
        sent = await bot.send_message(target, text + group_suffix, **kwargs)
    except TelegramMigrateToChat as exc:
        logger.warning("Guruhga yuborib bo'lmadi: guruh supergruppaga ko'chgan - admin'ga zaxira")
        note += (f"\nGuruh supergruppaga ko'chgan. Yangi ID: <code>{exc.migrate_to_chat_id}</code> — "
                 "Render → Environment → <code>ORDERS_CHAT_ID</code> ni shu qiymatga almashtiring.")
    except Exception as exc:
        logger.warning("Guruhga yuborib bo'lmadi (%s) - admin'ga zaxira", type(exc).__name__)
        note += ("\nBot guruhda bormi, xabar yozishga ruxsati bormi? Guruhda <code>/guruh_id</code> yozib, "
                 "Render'dagi ORDERS_CHAT_ID ni tekshiring.")
    else:
        if config.orders_also_admin:
            try:
                await bot.send_message(config.admin_id, text, **{k: v for k, v in kwargs.items()
                                                                 if k != "reply_parameters"})
            except Exception as exc:
                logger.warning("Admin'ga nusxa yuborilmadi (%s)", type(exc).__name__)
        return sent, target
    sent = await bot.send_message(config.admin_id, text, **{k: v for k, v in kwargs.items() if k != "reply_parameters"})
    try:
        await bot.send_message(config.admin_id, note)
    except Exception:
        pass
    return sent, config.admin_id


def group_mention(config: Config) -> str:
    """Guruhdagi YANGI buyurtma chekining eng oxirgi qatori: @<FEEDBACK_USERNAME> (Telegram mention qiladi).
    Username faqat [A-Za-z0-9_] - HTML parse_mode uchun xavfsiz."""
    return f"\n\n@{config.feedback_username}"


async def deliver_receipt(bot: Bot, config: Config, db: Database, data: ReceiptData, resent: bool = False) -> bool:
    """Chekni buyurtmalar guruhiga (yoki admin'ga) yuboradi va notified=1 qiladi. Chek yetib borgan bo'lsa True.

    notified chek yuborilishi bilan DARHOL belgilanadi (lokatsiyadan oldin), shunda
    qayta yuborishda dublikat chek bo'lmaydi. Hech qayerga yetmasa - notified=0, keyin qayta yuboriladi.
    """
    try:
        sent, chat_id = await send_to_orders_chat(
            bot, config, build_receipt(data, resent=resent), group_suffix=group_mention(config),
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        logger.exception("Chek yuborilmadi (order_id=%s) - keyin qayta yuboriladi", data.order_id)
        db.note_unnotified()
        return False
    try:
        # Chek xabari ID si va chati: mijoz bekor qilsa, shu chekka javob sifatida yoziladi
        await db.mark_notified(data.order_id, getattr(sent, "message_id", None), chat_id)
    except Exception:
        logger.exception("notified belgisi qo'yilmadi (order_id=%s)", data.order_id)
        db.note_unnotified()
    if data.has_location:
        targets = [chat_id]
        if config.orders_also_admin and chat_id != config.admin_id:
            targets.append(config.admin_id)
        for target in targets:
            try:
                await bot.send_location(target, latitude=data.lat, longitude=data.lon)
            except Exception as exc:
                logger.warning("Lokatsiya yuborilmadi (order_id=%s): %s", data.order_id, type(exc).__name__)
    return True


async def resend_unnotified(bot: Bot, config: Config, db: Database, min_age_seconds: float = 0,
                            before: datetime | None = None) -> int:
    """notified=0 buyurtmalarni admin'ga qayta yuboradi. Yuborilganlar soni qaytadi.

    - Bir vaqtda faqat bitta qayta yuborish ishlaydi (lock) - dublikat bo'lmaydi.
    - min_age_seconds: hozir yuborilayotgan yangi buyurtmalarga tegmaslik uchun.
    - Admin'ga yuborib bo'lmasa, qolganlari keyingi safarga qoldiriladi.
    - Hammasi yuborilsa, baza "tekshirildi" deb belgilanadi: keyingi fon tekshiruvlari yangi buyurtma
      yoki yuborish xatosi bo'lmaguncha bazaga murojaat qilmaydi (resend_if_needed).
    """
    async with db.resend_lock:
        # before berilsa (ishga tushish payti) - faqat undan oldingi buyurtmalar; yangilarini handler yuboradi
        if before is None:
            before = datetime.now(timezone.utc) - timedelta(seconds=min_age_seconds)
        pending = await db.get_unnotified(before)
        sent = 0
        for row, items in pending:
            if not await deliver_receipt(bot, config, db, ReceiptData.from_db(row, items), resent=True):
                break
            sent += 1
        if pending:
            logger.info("Admin'ga yetmagan buyurtmalar: %d ta topildi, %d ta qayta yuborildi", len(pending), sent)
        if sent == len(pending) and len(pending) < UNNOTIFIED_BATCH:
            db.resend_checked(before)
        return sent


async def resend_if_needed(bot: Bot, config: Config, db: Database, min_age_seconds: float = 0) -> int:
    """Fon vazifasi uchun: oxirgi tekshiruvdan keyin yangi buyurtma yoki yuborish xatosi bo'lmagan bo'lsa,
    bazaga so'rov yuborilmaydi (Neon bepul rejasida baza uxlay oladi)."""
    if not db.resend_needed():
        return 0
    return await resend_unnotified(bot, config, db, min_age_seconds=min_age_seconds)


# ---------------------------------------------------------------- Telegram kontakti (telefon)

def normalize_uz_phone(raw: str | None) -> str | None:
    """'998901234567' / '+998 90 123-45-67' -> '+998901234567'; O'zbekiston raqami bo'lmasa None."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit() and ch.isascii())
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None


@router.message(F.contact)
async def on_contact(message: Message, db: Database) -> None:
    """Mini App'dagi "Telegram raqamim" tugmasi (requestContact) mijozning kontaktini botga yuboradi.
    Faqat o'z raqami (contact.user_id = yuboruvchi) tasdiqlangan deb saqlanadi; chatga javob yozilmaydi."""
    contact = message.contact
    user = message.from_user
    if contact is None or user is None or contact.user_id != user.id:
        return
    phone = normalize_uz_phone(contact.phone_number)
    if phone is None:
        logger.info("Kontakt O'zbekiston raqami emas (user_id=%s) - saqlanmadi", user.id)
        return
    try:
        await db.save_verified_phone(user.id, phone, datetime.now(timezone.utc))
    except Exception:
        logger.exception("Tasdiqlangan telefon saqlanmadi (user_id=%s)", user.id)


# ---------------------------------------------------------------- admin buyruqlari (o'zbekcha)

def is_admin(message: Message, config: Config) -> bool:
    """Har bir admin buyrug'ida tekshiriladi: FAQAT botning shaxsiy chatida va FAQAT ADMIN_ID.
    Guruhda (hatto admin yozsa ham) va begona foydalanuvchiga bot JIM (buyruq borligini ham bildirmaydi).
    Menyuda yashirish himoya emas - shuning uchun har buyruqda alohida tekshiriladi."""
    return (message.from_user is not None and message.from_user.id == config.admin_id
            and getattr(message.chat, "type", None) == "private")


@router.message(Command(GROUP_ID_COMMAND))
async def cmd_group_id(message: Message, config: Config) -> None:
    """/guruh_id - FAQAT guruhda va FAQAT ADMIN_ID yozganda guruh chat ID si (ORDERS_CHAT_ID uchun).
    Shaxsiy chatda va boshqalarga - jim."""
    chat_type = getattr(message.chat, "type", None)
    if chat_type not in ("group", "supergroup") or message.from_user is None or message.from_user.id != config.admin_id:
        return
    await safe_answer(
        message,
        f"🆔 Guruh ID: <code>{message.chat.id}</code>\n"
        "Render → Environment → <code>ORDERS_CHAT_ID</code> ga shu qiymatni yozing.",
    )


@router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot, config: Config, db: Database) -> None:
    """Bazaning zaxira nusxasi (faqat admin)."""
    if not is_admin(message, config):
        return
    if not await backup.send_backup(bot, config, db, reason="/backup buyrug'i"):
        await message.answer("⚠️ Zaxirani yuborib bo'lmadi. Batafsil: bot logi.")


@router.message(Command("hisobot"))
async def cmd_report(message: Message, config: Config, db: Database) -> None:
    """Bugun / shu hafta / shu oy: buyurtmalar soni, tushum, eng ko'p sotilganlar (faqat admin)."""
    if not is_admin(message, config):
        return
    await message.answer(await reports.build_report(db))


# /excel_bugun = "/excel bugun" va h.k. (Telegram buyruq nomida bo'sh joy bo'lishi mumkin emas)
EXCEL_SHORTCUTS = {
    "excel_bugun": "bugun",
    "excel_hafta": "hafta",
    "excel_oy": "oy",
    "excel_hammasi": "hammasi",
}


@router.message(Command("excel"))
async def cmd_excel(message: Message, command: CommandObject, config: Config, db: Database) -> None:
    """/excel [bugun|hafta|oy|hammasi] - buyurtmalar Excel faylda (faqat admin)."""
    if not is_admin(message, config):
        return
    await send_excel(message, command.args or "", db)


@router.message(Command(*EXCEL_SHORTCUTS))
async def cmd_excel_shortcut(message: Message, command: CommandObject, config: Config, db: Database) -> None:
    """/excel_bugun, /excel_hafta, /excel_oy, /excel_hammasi (faqat admin)."""
    if not is_admin(message, config):
        return
    await send_excel(message, EXCEL_SHORTCUTS[command.command.lower()], db)


async def send_excel(message: Message, period_arg: str, db: Database) -> None:
    """Buyurtmalarni Excel (.xlsx) fayl qilib yuboradi. Chaqiruvchi admin ekanini tekshirgan bo'lishi shart."""
    now = datetime.now(timezone.utc)
    period = excel_export.export_range(period_arg, now)
    if period is None:
        await message.answer("Noma'lum davr. Variantlar:\n" + excel_export.PERIOD_HELP)
        return
    orders = await db.orders_with_items(period.start, period.end)
    if not orders:
        await message.answer(f"Bu davrda buyurtma yo'q.\n<i>{html.escape(period.title)}</i>")
        return
    try:
        # Fayl xotirada; og'ir hisoblash alohida oqimda - bot boshqa xabarlarni kutib qolmaydi
        data = await asyncio.to_thread(excel_export.build_workbook, orders, period, now)
    except Exception:
        logger.exception("Excel fayl yaratilmadi")
        await message.answer("⚠️ Excel faylni yaratib bo'lmadi. Batafsil: bot logi.")
        return
    cancelled = sum(1 for row, _ in orders if row.get("status") == "cancelled")
    await message.answer_document(
        BufferedInputFile(data, filename=excel_export.export_filename(now)),
        caption=(f"📑 <b>Buyurtmalar (Excel)</b>\n{html.escape(period.title)}\n"
                 f"Buyurtmalar: {len(orders) - cancelled} ta, bekor qilingan: {cancelled} ta"),
    )


# ---------------------------------------------------------------- bot guruhga qo'shildi

@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot, config: Config) -> None:
    """Bot guruh/kanalga qo'shildi:
    - qo'shgan ADMIN_ID emas - darhol chiqib ketadi;
    - ADMIN_ID qo'shgan va ORDERS_CHAT_ID berilmagan yoki shu guruh - qoladi;
    - ADMIN_ID qo'shgan, lekin ORDERS_CHAT_ID boshqa guruh - chiqib ketadi va admin'ga shaxsiy chatda yozadi."""
    chat = event.chat
    if chat.type == "private":
        return
    joined = (event.new_chat_member.status in ("member", "administrator", "restricted")
              and event.old_chat_member.status in ("left", "kicked"))
    if not joined:
        return
    adder = event.from_user.id if event.from_user else None
    if config.orders_chat_id is not None and chat.id == config.orders_chat_id:
        logger.info("Bot buyurtmalar guruhiga qo'shildi (%s)", mask_id(chat.id))
        return
    if adder != config.admin_id:
        logger.warning("Bot begona guruhga qo'shildi (%s) - chiqib ketildi", mask_id(chat.id))
        try:
            await bot.leave_chat(chat.id)
        except Exception as exc:
            logger.warning("Guruhdan chiqib bo'lmadi: %s", type(exc).__name__)
        return
    if config.orders_chat_id is None:
        logger.info("Admin botni guruhga qo'shdi (%s) - ORDERS_CHAT_ID hali berilmagan", mask_id(chat.id))
        return
    logger.warning("Admin botni boshqa guruhga qo'shdi (%s) - ORDERS_CHAT_ID boshqa, chiqib ketildi", mask_id(chat.id))
    try:
        await bot.leave_chat(chat.id)
    except Exception as exc:
        logger.warning("Guruhdan chiqib bo'lmadi: %s", type(exc).__name__)
    try:
        await bot.send_message(
            config.admin_id,
            f"ℹ️ Bot «{html.escape(chat.title or '')}» guruhiga qo'shildi, lekin buyurtmalar guruhi "
            f"(ORDERS_CHAT_ID) boshqa — bot chiqib ketdi.\n"
            f"Shu guruhni ishlatmoqchi bo'lsangiz: Render → Environment → <code>ORDERS_CHAT_ID</code> = "
            f"<code>{chat.id}</code>, keyin botni guruhga qayta qo'shing.",
        )
    except Exception as exc:
        logger.warning("Admin'ga xabar yuborilmadi: %s", type(exc).__name__)


# ---------------------------------------------------------------- boshqa xabarlar

@router.message(F.text)
async def fallback_text(message: Message, bot: Bot, config: Config, db: Database, lang: str | None = None) -> None:
    """Boshqa har qanday matn: eski pastki klaviatura olib tashlanadi, menyu tugmasi o'rnatiladi va
    inline "Buyurtma berish" yuboriladi (eski mijozlarni yangi interfeysga ko'chirish).
    Admin buyruqlari bu yerga kelmaydi - ular o'z handlerida (begona foydalanuvchiga javob berilmaydi)."""
    user = message.from_user
    if user is None or _command_name(message.text) in ADMIN_COMMANDS:
        return
    if getattr(message.chat, "type", "private") != "private":
        # guruhdan faqat /guruh_id... o'tadi; boshqa botga yozilgan /guruh_id@boshqabot shu yerga tushadi - jim
        return
    await switch_to_inline(bot, config, db, user.id, lang)
    await safe_answer(message, t(lang, "fallback", btn=t(lang, "btn.order")),
                      reply_markup=await order_keyboard(config, db, user.id, lang))


@router.errors()
async def on_error(event: ErrorEvent, **data) -> bool:
    """Kutilmagan xatolar: log + admin'ga xabar. Bot to'xtamaydi."""
    logger.error("Kutilmagan xato: %r", event.exception, exc_info=event.exception)
    bot: Bot | None = data.get("bot")
    config: Config | None = data.get("config")
    if bot is not None and config is not None:
        await notify_admin(bot, config, "Kutilmagan xato", None, f"{type(event.exception).__name__}: {event.exception}")
    return True
