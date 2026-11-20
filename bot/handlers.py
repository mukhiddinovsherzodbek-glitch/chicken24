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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
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
    DELIVERY_AREA_NAME,
    DELIVERY_CENTER_LAT,
    DELIVERY_CENTER_LON,
    DELIVERY_FEE,
    DELIVERY_RADIUS_KM,
    LOCAL_TZ,
    OPEN_FROM,
    PAYMENT_METHOD_TEXT,
    WELCOME_IMAGE_PATH,
    WELCOME_IMAGE_RU_PATH,
    Config,
)
from db import CANCEL_ALREADY, CANCEL_EXPIRED, CANCEL_NOT_FOUND, UNNOTIFIED_BATCH, Database
from i18n import format_price, t
from menu import MenuError, MenuProvider
from validators import (
    DeliveryArea,
    ValidatedOrder,
    ValidationError,
    extract_ref,
    parse_payload,
    validate_order,
)

logger = logging.getLogger(__name__)
router = Router(name="chicken24")

CANCEL_PREFIX = "cancel:"  # callback_data: "cancel:<order_id>"
LANG_PREFIX = "lang:"  # callback_data: "lang:uz" | "lang:ru"

# Yetkazish hududi (sozlama config.py da) - serverdagi asosiy tekshiruv
DELIVERY_AREA = DeliveryArea(DELIVERY_AREA_NAME, DELIVERY_CENTER_LAT, DELIVERY_CENTER_LON, DELIVERY_RADIUS_KM)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Faqat admin uchun buyruqlar (til tanlanmagan bo'lsa ham ishlaydi; ADMIN_ID har birida tekshiriladi)
ADMIN_COMMANDS = {"backup", "hisobot", "excel", "excel_bugun", "excel_hafta", "excel_oy", "excel_hammasi"}

# Admin xabarlari uchun til (o'zbekcha qoladi)
ADMIN_LANG = "uz"


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


async def build_webapp_url(config: Config, db: Database | None, user_id: int, lang: str | None) -> str:
    """Mini App tugmasi havolasi - YAGONA joy. ?lang= doim; mijozning eng oxirgi buyurtmasi natijasi bazadan.

    sendData() Mini App'ni yopadi va bot unga to'g'ridan-to'g'ri javob bera olmaydi, shuning uchun natija
    keyingi ochilishda tugma havolasi orqali yetkaziladi. Telegram'da chatda bitta doimiy klaviatura bor:
    yangi klaviatura eskisining o'rnini egallaydi. Natija bazadan olingani uchun klaviatura qayerda qayta
    yuborilmasin (/start, /til, til tanlash, ...) yoki bot qayta yonsa ham natija yo'qolmaydi.
    Mini App natijani idempotent qo'llaydi (bir xil natija qayta kelsa hech narsa o'zgarmaydi).
    """
    params = {"lang": i18n.normalize(lang)}
    if db is not None:
        try:
            params.update(result_params(await db.latest_result(user_id)))
        except Exception:
            logger.exception("Oxirgi buyurtma natijasini o'qib bo'lmadi (user_id=%s) - faqat ?lang=", user_id)
    return webapp_url_with_result(config.webapp_url, params)


async def main_keyboard(config: Config, db: Database | None, user_id: int, lang: str | None) -> ReplyKeyboardMarkup:
    # sendData() FAQAT reply-klaviatura tugmasi orqali ochilgan Mini App'da ishlaydi,
    # shuning uchun inline tugma emas, KeyboardButton + WebAppInfo.
    lang = i18n.normalize(lang)
    url = await build_webapp_url(config, db, user_id, lang)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn.order"), web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t(lang, "kb.placeholder"),
    )


WELCOME_FLAG = "w"  # callback_data "lang:ru:w" - til tanlangach salomlashuv yuboriladi (/start dan)


def lang_keyboard(welcome: bool = False) -> InlineKeyboardMarkup:
    """welcome=True (/start): tanlangach salomlashuv (rasm + matn + klaviatura).
    welcome=False (/til): tanlangach qisqa tasdiq + yangi klaviatura."""
    suffix = f":{WELCOME_FLAG}" if welcome else ""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("uz", "lang.btn_uz"), callback_data=f"{LANG_PREFIX}uz{suffix}"),
        InlineKeyboardButton(text=t("ru", "lang.btn_ru"), callback_data=f"{LANG_PREFIX}ru{suffix}"),
    ]])


async def send_lang_prompt(bot: Bot, chat_id: int, welcome: bool = False) -> None:
    """Til tanlash xabari (ikki tilda) va inline tugmalar.
    Inline klaviatura chatdagi doimiy (reply) klaviaturaga tegmaydi - eski Mini App tugmasi ishlayveradi."""
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


class LanguageMiddleware(BaseMiddleware):
    """Har bir xabar/tugma bosishdan oldin mijoz tilini aniqlaydi va handlerga `lang` beradi.

    Tili tanlanmagan mijoz (yangi yoki eski) nima yozsa yoki bossa ham - avval til tanlash xabari.
    Istisnolar:
      - til tanlash tugmasining o'zi;
      - Mini App buyurtmasi (web_app_data) - buyurtma yo'qolmasligi uchun: til payload'dan olinadi va saqlanadi;
      - Telegram kontakti (Mini App "Telegram raqamim") - javobsiz saqlanadi;
      - admin buyruqlari (faqat ADMIN_ID dan).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user") or getattr(event, "from_user", None)
        db: Database | None = data.get("db")
        if user is None or db is None:
            return await handler(event, data)
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


router.message.outer_middleware(LanguageMiddleware())
router.callback_query.outer_middleware(LanguageMiddleware())


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
        f"🚚 Yetkazish: {format_price(data.delivery_fee, ADMIN_LANG)}",
        f"💰 <b>Jami: {format_price(data.total, ADMIN_LANG)}</b>",
        f"💵 To'lov: {PAYMENT_METHOD_TEXT}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- mijoz matnlari

def totals_lines(items_total: int, delivery_fee: int, total: int, lang: str | None) -> list[str]:
    """Taomlar / Yetkazish / Jami / To'lov qatorlari - mijoz tilida."""
    return [
        t(lang, "totals.items", sum=format_price(items_total, lang)),
        t(lang, "totals.delivery", sum=format_price(delivery_fee, lang)),
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
    lines += totals_lines(order.items_total, order.delivery_fee, order.total, lang)
    lines.append("")
    lines.append(t(lang, "order.courier"))
    return "\n".join(lines)


async def notify_admin(bot: Bot, config: Config, title: str, user: User | None, detail: str = "") -> None:
    """Admin'ga xato haqida log (o'zbekcha). O'zi hech qachon xato tashlamaydi."""
    try:
        text = f"⚠️ <b>{html.escape(title)}</b>"
        if user is not None:
            text += f"\nFoydalanuvchi: {user_label(user)}"
        if detail:
            text += f"\n<pre>{html.escape(detail[:1500])}</pre>"
        await bot.send_message(config.admin_id, text, link_preview_options=NO_PREVIEW)
    except Exception:
        logger.exception("Admin'ga xato logini yuborib bo'lmadi")


async def safe_answer(message: Message, text: str, **kwargs) -> None:
    try:
        await message.answer(text, **kwargs)
    except Exception:
        logger.exception("Mijozga javob yuborib bo'lmadi (user_id=%s)", message.chat.id)


# ---------------------------------------------------------------- salomlashuv

CAPTION_LIMIT = 1024  # Telegram photo caption chegarasi


def build_welcome_caption(first_name: str | None, is_open: bool, lang: str | None = "uz") -> str:
    """Salomlashuv matni (HTML) mijoz tilida. Ism tozalanadi va qisqartiriladi - 1024 belgidan oshmaydi."""
    name = html.escape((first_name or t(lang, "welcome.friend"))[:64])
    lines = [
        t(lang, "welcome.hello", name=name),
        t(lang, "welcome.title"),
        "",
        t(lang, "welcome.hours", open=f"{OPEN_FROM:%H:%M}", close=f"{CLOSE_AT:%H:%M}"),
        t(lang, "welcome.delivery", fee=format_price(DELIVERY_FEE, lang)),
        t(lang, "welcome.payment"),
    ]
    if not is_open:
        lines += ["", t(lang, "welcome.closed", open=f"{OPEN_FROM:%H:%M}")]
    lines += ["", t(lang, "welcome.cta")]
    return "\n".join(lines)[:CAPTION_LIMIT]


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
    """Salomlashuv: rasm (tilga mos) + matn + klaviatura. Rasm yuborilmasa - oddiy matn.
    Shaxsiy chatda chat_id = user_id (klaviatura havolasidagi natija shu mijozniki)."""
    lang = i18n.normalize(lang)
    caption = build_welcome_caption(first_name, business.is_open_now(), lang)
    keyboard = await main_keyboard(config, db, chat_id, lang)
    path = welcome_image_path(lang)
    key = str(path)

    # 1) Saqlangan file_id bilan (tez, qayta yuklamasdan)
    file_id = WelcomePhoto.file_ids.get(key)
    if file_id:
        try:
            await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=keyboard)
            return
        except Exception:
            logger.exception("Saqlangan file_id bilan rasm yuborilmadi - fayldan qayta yuklanadi")
            WelcomePhoto.file_ids.pop(key, None)

    # 2) Fayldan (photo sifatida, hujjat emas)
    if path.is_file():
        try:
            sent = await bot.send_photo(chat_id, FSInputFile(path), caption=caption, reply_markup=keyboard)
            if sent and sent.photo:
                WelcomePhoto.file_ids[key] = sent.photo[-1].file_id
            return
        except Exception:
            logger.exception("Salomlashuv rasmi yuborilmadi - matnli salomlashuv yuboriladi")
    else:
        logger.error("Salomlashuv rasmi topilmadi: %s - matnli salomlashuv yuboriladi", path)

    # 3) Zaxira: oddiy matn
    await bot.send_message(chat_id, caption, reply_markup=keyboard)


# ---------------------------------------------------------------- /start, /til, til tanlash

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    """/start har doim tilni so'raydi (til saqlangan bo'lsa ham, admin uchun ham).
    Tanlangach - salomlashuv (on_lang_chosen). Shu oraliqda eski Mini App tugmasi chatda qoladi."""
    await send_lang_prompt(bot, message.chat.id, welcome=True)


@router.message(Command("til"))
async def cmd_lang(message: Message, bot: Bot) -> None:
    """Tilni istalgan payt almashtirish."""
    await send_lang_prompt(bot, message.chat.id)


@router.callback_query(F.data.startswith(LANG_PREFIX))
async def on_lang_chosen(
    callback: CallbackQuery, bot: Bot, config: Config, db: Database, lang: str | None = None
) -> None:
    """Til tanlandi: saqlanadi (avvalgisi almashtiriladi).
    /start dan yoki birinchi marta bo'lsa - salomlashuv; /til dan - tasdiq + yangi klaviatura.
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
    if first_time:
        await send_welcome(bot, user.id, config, db, new_lang, user.first_name)
    else:
        try:
            keyboard = await main_keyboard(config, db, user.id, new_lang)
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


@router.message(F.web_app_data)
async def on_web_app_data(
    message: Message,
    bot: Bot,
    config: Config,
    menu_store: MenuProvider,
    db: Database,
    limiter: RateLimiter,
    lang: str | None = None,
) -> None:
    user = message.from_user
    if user is None:
        return

    raw = message.web_app_data.data if message.web_app_data else ""

    # Mini App'dagi yozuv belgisi: javob tugma havolasi orqali qaytariladi
    try:
        data = parse_payload(raw)
        parse_error: ValidationError | None = None
    except ValidationError as exc:
        data, parse_error = {}, exc
    ref = extract_ref(data)
    lang = await resolve_order_lang(db, user, lang, data)

    async def rejected(code: str) -> ReplyKeyboardMarkup:
        """Rad etish: natija bazaga yoziladi (last_rejections), klaviatura havolasi bazadan yasaladi."""
        if ref:
            try:
                await db.record_rejection(user.id, ref, code, datetime.now(timezone.utc))
            except Exception:
                logger.exception("Rad etish natijasi saqlanmadi (user_id=%s)", user.id)
        return await main_keyboard(config, db, user.id, lang)

    # 0) Ish vaqti (asosiy tekshiruv shu yerda; yopiq bo'lsa buyurtma bazaga yozilmaydi)
    if not business.is_open_now():
        await safe_answer(
            message,
            t(lang, "order.closed", open=f"{OPEN_FROM:%H:%M}", close=f"{CLOSE_AT:%H:%M}"),
            reply_markup=await rejected("closed"),
        )
        return

    # 1) Spam himoyasi (await'dan oldin - atomar)
    wait = limiter.try_acquire(user.id)
    if wait > 0:
        await safe_answer(message, t(lang, "order.limit", sec=math.ceil(wait)), reply_markup=await rejected("limit"))
        return

    # 2) Validatsiya + summani menyudan hisoblash
    try:
        if parse_error is not None:
            raise parse_error
        menu = menu_store.get()
        order = validate_order(data, menu, area=DELIVERY_AREA, delivery_fee=DELIVERY_FEE)
    except ValidationError as exc:
        limiter.release(user.id)
        logger.warning("Noto'g'ri buyurtma (user_id=%s): %s", user.id, exc.detail)
        await safe_answer(
            message,
            t(lang, "order.rejected", reason=html.escape(exc.message(lang))),
            reply_markup=await rejected(exc.code),
        )
        await notify_admin(bot, config, "Buyurtma rad etildi (validatsiya)", user, f"{exc.detail}\n\n{raw[:800]}")
        return
    except MenuError as exc:
        limiter.release(user.id)
        logger.exception("Menyu o'qilmadi")
        await safe_answer(message, f"{t(lang, 'error.generic')}\n\n{t(lang, 'error.cart_saved')}",
                          reply_markup=await rejected("server"))
        await notify_admin(bot, config, "menu.json o'qilmadi", user, str(exc))
        return

    # 3) Saqlash (telefon mijoz Telegram orqali ulashgan raqam bilan bir xilmi - tasdiqlangan belgisi)
    created_at = datetime.now(timezone.utc)
    try:
        verified_phone = await db.get_verified_phone(user.id)
        phone_verified = verified_phone is not None and verified_phone == order.phone
        order_id = await db.save_order(
            user_id=user.id, username=user.username, order=order, created_at=created_at,
            client_ref=ref, phone_verified=phone_verified,
        )
    except Exception as exc:
        limiter.release(user.id)
        logger.exception("Buyurtmani bazaga saqlashda xato")
        await safe_answer(message, f"{t(lang, 'error.generic')}\n\n{t(lang, 'error.cart_saved')}",
                          reply_markup=await rejected("server"))
        await notify_admin(bot, config, "Buyurtmani saqlashda xato", user, f"{type(exc).__name__}: {exc}")
        return

    # Tartib: 3) bazada saqlandi -> 4) mijozga tasdiq -> 5) bekor qilish tugmasi -> 6) admin'ga chek.
    # Admin'ga yetkazishdagi xato (ADMIN_ID noto'g'ri, admin /start bosmagan, Telegram xatosi) buyurtmani
    # bekor qilmaydi va mijoz tasdig'ini to'xtatmaydi: notified=0 qoladi, resend_unnotified() keyin yuboradi.

    # 4) Mijozga tasdiq o'z tilida (havolada bazadagi natija ?ok=<ref>&n=<raqam>: Mini App savatni tozalaydi)
    await safe_answer(message, build_customer_text(order_id, order, lang),
                      reply_markup=await main_keyboard(config, db, user.id, lang))

    # 5) Bekor qilish tugmasi - alohida xabarda (bitta xabarda ham reply-, ham inline-klaviatura bo'lolmaydi;
    #    yuqoridagi reply-klaviaturadagi natijali havola shu tufayli buzilmaydi)
    await send_cancel_offer(bot, message, order_id, lang)

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
        await safe_answer(message, t(lang, "order.delayed"))


# ---------------------------------------------------------------- bekor qilish (5 daqiqa)

def cancel_keyboard(order_id: int, lang: str | None = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "cancel.btn"), callback_data=f"{CANCEL_PREFIX}{order_id}")
    ]])


async def send_cancel_offer(bot: Bot, message: Message, order_id: int, lang: str | None = "uz") -> None:
    minutes = CANCEL_WINDOW_SECONDS // 60
    try:
        sent = await message.answer(
            t(lang, "cancel.offer", id=order_id, min=minutes),
            reply_markup=cancel_keyboard(order_id, lang),
        )
    except Exception:
        logger.exception("Bekor qilish tugmasi yuborilmadi (order_id=%s)", order_id)
        return
    message_id = getattr(sent, "message_id", None)
    if message_id is not None:
        # Muddat tugagach tugma olib tashlanadi (bot qayta yonsa ham - bosilganda muddat baribir tekshiriladi)
        _keep_task(asyncio.create_task(_expire_cancel_button(bot, message.chat.id, message_id, order_id, lang)))


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

    # Mijozga: Mini App tarixida ham "Bekor qilindi" bo'lishi uchun natijali havola (bazadan: ?cx=<ref>)
    try:
        await bot.send_message(
            callback.from_user.id,
            t(lang, "cancel.done_msg", id=order_id),
            reply_markup=await main_keyboard(config, db, callback.from_user.id, lang),
        )
    except Exception:
        logger.exception("Mijozga bekor qilish xabari yuborilmadi (order_id=%s)", order_id)

    # Admin'ga (o'zbekcha): chek xabariga javob sifatida (chek yetib bormagan bo'lsa - qayta yuborilmaydi)
    if order.get("notified"):
        kwargs = {}
        if order.get("admin_msg_id"):
            kwargs["reply_parameters"] = ReplyParameters(message_id=order["admin_msg_id"], allow_sending_without_reply=True)
        try:
            await bot.send_message(
                config.admin_id,
                f"🚫 <b>Mijoz buyurtma #{order_id} ni bekor qildi.</b> Tayyorlamang.",
                **kwargs,
            )
        except Exception:
            logger.exception("Admin'ga bekor qilish xabari yuborilmadi (order_id=%s)", order_id)


async def _safe_edit(callback: CallbackQuery, text: str) -> None:
    """Tugmali xabarni oddiy matnga almashtiradi (tugma yo'qoladi)."""
    try:
        if callback.message is not None:
            await callback.message.edit_text(text)
    except Exception:
        pass


# ---------------------------------------------------------------- admin'ga chek va qayta yuborish

async def deliver_receipt(bot: Bot, config: Config, db: Database, data: ReceiptData, resent: bool = False) -> bool:
    """Chekni admin'ga yuboradi va notified=1 qiladi. Chek yetib borgan bo'lsa True.

    notified chek yuborilishi bilan DARHOL belgilanadi (lokatsiyadan oldin), shunda
    qayta yuborishda dublikat chek bo'lmaydi.
    """
    try:
        sent = await bot.send_message(
            config.admin_id, build_receipt(data, resent=resent), link_preview_options=NO_PREVIEW
        )
    except Exception:
        logger.exception("Admin'ga chek yuborilmadi (order_id=%s) - keyin qayta yuboriladi", data.order_id)
        db.note_unnotified()
        return False
    try:
        # Chek xabari ID si: mijoz bekor qilsa, admin'ga shu chekka javob sifatida yoziladi
        await db.mark_notified(data.order_id, getattr(sent, "message_id", None))
    except Exception:
        logger.exception("notified belgisi qo'yilmadi (order_id=%s)", data.order_id)
        db.note_unnotified()
    if data.has_location:
        try:
            await bot.send_location(config.admin_id, latitude=data.lat, longitude=data.lon)
        except Exception:
            logger.exception("Admin'ga lokatsiya yuborilmadi (order_id=%s)", data.order_id)
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
    """Har bir admin buyrug'ida tekshiriladi. Menyuda yashirish himoya emas - mijoz qo'lda yozishi mumkin.
    Admin bo'lmaganlarga bot JIM qoladi (buyruq borligini ham bildirmaydi)."""
    return message.from_user is not None and message.from_user.id == config.admin_id


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


# ---------------------------------------------------------------- boshqa xabarlar

@router.message(F.text)
async def fallback_text(message: Message, lang: str | None = None) -> None:
    # Klaviatura qayta yuborilmaydi: aks holda oxirgi buyurtma natijasi (tugma havolasi) o'chib ketadi
    await message.answer(t(lang, "fallback", btn=t(lang, "btn.order")))


@router.errors()
async def on_error(event: ErrorEvent, **data) -> bool:
    """Kutilmagan xatolar: log + admin'ga xabar. Bot to'xtamaydi."""
    logger.error("Kutilmagan xato: %r", event.exception, exc_info=event.exception)
    bot: Bot | None = data.get("bot")
    config: Config | None = data.get("config")
    if bot is not None and config is not None:
        await notify_admin(bot, config, "Kutilmagan xato", None, f"{type(event.exception).__name__}: {event.exception}")
    return True
