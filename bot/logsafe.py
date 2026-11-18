"""Loglarga maxfiy ma'lumot tushmasligi uchun: mijoz telefoni, bot tokeni, baza paroli yashiriladi.

SafeFormatter hamma log yozuvlariga (traceback'lar bilan birga) qo'llanadi - main.setup_logging().
"""
from __future__ import annotations

import logging
import re
from typing import Any

# O'zbekiston raqami: +998 90 123 45 67 / 998901234567 / +998-90-123-45-67 -> +998 ** *** ** 67
_UZ_PHONE = re.compile(r"(?<!\d)\+?998[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?(\d{2})(?!\d)")
# Telegram bot tokeni: 123456789:AAH... (35 belgili qism)
_BOT_TOKEN = re.compile(r"(?<![\w])\d{5,}:[A-Za-z0-9_-]{30,}")
# postgresql://user:PAROL@host -> postgresql://user:***@host
_DSN_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", re.IGNORECASE)


def scrub(text: str) -> str:
    text = _BOT_TOKEN.sub("<BOT_TOKEN>", text)
    text = _DSN_PASSWORD.sub(r"\1***@", text)
    return _UZ_PHONE.sub(lambda m: f"+998 ** *** ** {m.group(1)}", text)


def mask_phone(value: Any) -> str:
    """Noto'g'ri formatdagi telefon ham logga to'liq tushmasin: '+99890*******67' ko'rinishi."""
    if not isinstance(value, str):
        return f"<{type(value).__name__}>"
    if len(value) <= 4:
        return "*" * len(value)
    keep_head = min(6, max(1, len(value) - 6))
    return value[:keep_head] + "*" * (len(value) - keep_head - 2) + value[-2:]


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))
