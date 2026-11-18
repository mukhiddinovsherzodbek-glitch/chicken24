"""Bazani avtomatik zaxiralash: nusxa admin chatiga hujjat sifatida yuboriladi.

- Har kuni config.BACKUP_TIME da (Samarqand vaqti), faqat oxirgi zaxiradan keyin o'zgarish bo'lsa.
- Bot ishga tushganda: oxirgi zaxiradan 24 soatdan ko'p o'tgan va o'zgarish bo'lsa - darhol.
- /backup buyrug'i (faqat admin) - istalgan payt, o'zgarish bo'lmasa ham.
Nusxa har doim SQLite .db fayl (bot to'xtamaydi, yozuvlar izchil):
- SQLite rejimida - SQLite backup API;
- PostgreSQL rejimida - Postgres ma'lumotidan (bitta snapshot) yasalgan SQLite nusxa. Uni lokal botga
  qo'yish yoki tools/migrate_sqlite_to_postgres.py bilan Postgres'ga qaytarish mumkin.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import BufferedInputFile

import config
from db import Database

logger = logging.getLogger(__name__)

META_LAST_AT = "backup_last_at"  # UTC ISO
META_LAST_SIG = "backup_last_sig"
STARTUP_MAX_AGE = timedelta(hours=24)


def _size_text(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def seconds_until(target: time, now_local: datetime) -> float:
    """Keyingi `target` (mahalliy vaqt) gacha qolgan soniyalar (har doim > 0)."""
    run_at = now_local.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if run_at <= now_local:
        run_at += timedelta(days=1)
    return (run_at - now_local).total_seconds()


async def has_changes(db: Database) -> bool:
    return await db.data_signature() != await db.get_meta(META_LAST_SIG)


async def send_backup(bot: Bot, cfg: config.Config, db: Database, *, reason: str) -> bool:
    """Zaxirani admin'ga yuboradi. Muvaffaqiyatli bo'lsa True (va meta yangilanadi)."""
    signature = await db.data_signature()
    total, cancelled = await db.order_counts()
    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(config.LOCAL_TZ)
    filename = f"chicken24_{local:%Y-%m-%d_%H%M}.db"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        await db.backup_to(path)
        data = path.read_bytes()

    if len(data) > config.BACKUP_MAX_BYTES:
        logger.error("Zaxira juda katta (%s) - Telegram orqali yuborilmadi", _size_text(len(data)))
        try:
            await bot.send_message(
                cfg.admin_id,
                f"⚠️ Zaxira juda katta ({_size_text(len(data))}) — Telegram orqali yuborib bo'lmaydi. "
                + ("Neon paneli orqali eksport qiling." if db.kind == "postgres" else "Bazani panel orqali yuklab oling."),
            )
        except Exception:
            logger.exception("Admin'ga ogohlantirish yuborilmadi")
        return False

    caption = (
        f"🗄 <b>Zaxira nusxa</b> · {local:%d.%m.%Y %H:%M}\n"
        f"Buyurtmalar: {total} ta (bekor qilingan: {cancelled})\n"
        f"Hajm: {_size_text(len(data))} · Sabab: {reason}\n\n"
        "Faylda mijozlar ma'lumoti bor — boshqalarga yubormang."
    )
    try:
        await bot.send_document(cfg.admin_id, BufferedInputFile(data, filename=filename), caption=caption)
    except Exception:
        logger.exception("Zaxira admin'ga yuborilmadi")
        return False
    await db.set_meta(META_LAST_AT, now_utc.isoformat())
    await db.set_meta(META_LAST_SIG, signature)
    logger.info("Zaxira yuborildi: %s (%s, %s)", filename, _size_text(len(data)), reason)
    return True


async def startup_backup_if_due(bot: Bot, cfg: config.Config, db: Database) -> bool:
    """Bot o'chiq paytda kunlik zaxira o'tib ketgan bo'lsa - ishga tushganda yuboradi."""
    if not await has_changes(db):
        return False
    last = await db.get_meta(META_LAST_AT)
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) < STARTUP_MAX_AGE:
                return False
        except ValueError:
            pass
    total, _ = await db.order_counts()
    if not total:
        return False
    return await send_backup(bot, cfg, db, reason="ishga tushganda (o'tkazib yuborilgan zaxira)")


async def backup_loop(bot: Bot, cfg: config.Config, db: Database) -> None:
    """Fon vazifasi: har kuni BACKUP_TIME da zaxira (o'zgarish bo'lsa)."""
    while True:
        now_local = datetime.now(timezone.utc).astimezone(config.LOCAL_TZ)
        await asyncio.sleep(seconds_until(config.BACKUP_TIME, now_local))
        try:
            if await has_changes(db):
                await send_backup(bot, cfg, db, reason="kunlik")
            else:
                logger.info("Kunlik zaxira: yangi o'zgarish yo'q - yuborilmadi")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kunlik zaxirada xato")
        await asyncio.sleep(61)  # bir daqiqa ichida ikki marta ishlamasligi uchun
