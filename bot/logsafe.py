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


# Sozlamadagi chat ID lar (ADMIN_ID, ORDERS_CHAT_ID) - loglarda ochiq chiqmaydi (main.py ro'yxatga oladi)
_SENSITIVE_IDS: list[re.Pattern] = []


def register_sensitive_ids(*values: object) -> None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if len(text.lstrip("-")) >= 4:
            _SENSITIVE_IDS.append(re.compile(rf"(?<![\d-]){re.escape(text)}(?!\d)"))


def mask_id(value: object) -> str:
    """-1001234567890 -> -100*******890 (logda qaysi ekanini taxminan bilish uchun oxiri qoladi)."""
    text = str(value)
    sign, digits = ("-", text[1:]) if text.startswith("-") else ("", text)
    if len(digits) <= 4:
        return sign + "*" * len(digits)
    head = 3 if sign else 0
    return sign + digits[:head] + "*" * (len(digits) - head - 3) + digits[-3:]


def scrub(text: str) -> str:
    for pattern in _SENSITIVE_IDS:
        text = pattern.sub(lambda m: mask_id(m.group(0)), text)
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
