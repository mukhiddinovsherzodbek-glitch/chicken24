"""Telegram "/" buyruqlar menyusi.

- Mijozlar (BotCommandScopeDefault): /start va /til.
- Admin (BotCommandScopeChat, chat_id=ADMIN_ID): to'liq ro'yxat.
Bot har ishga tushganda (polling'dan oldin) qo'llanadi; avval boshqa scope/tillarga o'rnatilgan
eski buyruqlar tozalanadi, shunda mijozlarga admin buyruqlari qolib ketmaydi.

Muhim: menyuda yashirish - himoya EMAS. Har bir admin buyrug'i handlers.py da ADMIN_ID ni tekshiradi.
"""
from __future__ import annotations

import logging
import re

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

logger = logging.getLogger(__name__)

# Telegram talabi: 1-32 belgi, faqat kichik lotin harflari, raqamlar va pastki chiziq
COMMAND_NAME_RE = re.compile(r"[a-z0-9_]{1,32}")

# Mijozlar menyusi ikki tilli izoh bilan (bitta menyu - hamma mijozlar uchun)
CUSTOMER_COMMANDS: list[tuple[str, str]] = [
    ("start", "Boshlash / Начать"),
    ("til", "Tilni o'zgartirish / Сменить язык"),
]

ADMIN_COMMANDS: list[tuple[str, str]] = [
    ("start", "Boshlash / Начать"),
    ("til", "Tilni o'zgartirish / Сменить язык"),
    ("excel", "Excel: oxirgi 30 kun"),
    ("excel_bugun", "Excel: bugun"),
    ("excel_hafta", "Excel: shu hafta"),
    ("excel_oy", "Excel: shu oy"),
    ("excel_hammasi", "Excel: hamma buyurtmalar"),
    ("hisobot", "Hisobot: bugun / hafta / oy"),
    ("backup", "Bazani zaxiralash"),
]

# Avval o'rnatilgan bo'lishi mumkin bo'lgan tillar (None - tilsiz, umumiy)
CLEANUP_LANGUAGES: tuple[str | None, ...] = (None, "uz", "ru", "en")


def _commands(items: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=name, description=desc) for name, desc in items]


async def apply_commands(bot: Bot, admin_id: int) -> None:
    """Buyruqlar menyusini qayta o'rnatadi. Hech qachon xato tashlamaydi (bot yiqilmaydi)."""
    # 1) Eski buyruqlarni tozalash: barcha umumiy scope'lar va admin chati, har xil tillarda
    scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
        BotCommandScopeChat(chat_id=admin_id),
    ]
    for scope in scopes:
        for lang in CLEANUP_LANGUAGES:
            try:
                await bot.delete_my_commands(scope=scope, language_code=lang)
            except Exception as exc:  # masalan, admin chati hali mavjud emas
                logger.debug("Buyruqlarni tozalab bo'lmadi (%s, %s): %s", scope.type, lang, exc)

    # 2) Mijozlar uchun: faqat /start
    try:
        await bot.set_my_commands(_commands(CUSTOMER_COMMANDS), scope=BotCommandScopeDefault())
    except Exception:
        logger.exception("Mijozlar uchun buyruqlar menyusini o'rnatib bo'lmadi")

    # 3) Admin uchun: to'liq ro'yxat
    try:
        await bot.set_my_commands(_commands(ADMIN_COMMANDS), scope=BotCommandScopeChat(chat_id=admin_id))
        logger.info("Buyruqlar menyusi o'rnatildi: mijozlar - /start, /til; admin - %d ta buyruq", len(ADMIN_COMMANDS))
    except Exception as exc:
        logger.warning(
            "Admin buyruqlar menyusi o'rnatilmadi (admin botga hali /start yozmagan bo'lishi mumkin): %s. "
            "Admin /start yozgach, botni qayta ishga tushiring.", exc,
        )
