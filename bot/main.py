"""Chicken24 bot - kirish nuqtasi.

Ishga tushirish:  python main.py

Rejim muhit o'zgaruvchilariga qarab tanlanadi:
- WEBHOOK_URL + WEBHOOK_SECRET berilgan - webhook (aiohttp server, PORT da; /health bor) - Render uchun.
- berilmagan - polling (lokal kompyuter, avvalgidek).
- DATABASE_URL berilgan - PostgreSQL, berilmagan - SQLite (bot/chicken24.db).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

import backup
import commands
from config import (
    BACKUP_ENABLED,
    LOG_PATH,
    MENU_REFRESH_SECONDS,
    ORDER_COOLDOWN_SECONDS,
    REMOTE_MENU_MAX_AGE_SECONDS,
    RESEND_INTERVAL_SECONDS,
    RESEND_MIN_AGE_SECONDS,
    Config,
    ConfigError,
    load_config,
)
import webhook
from db import Database
from handlers import RateLimiter, resend_if_needed, resend_unnotified, router
from logsafe import SafeFormatter
from menu import MenuError, MenuProvider, MenuStore

logger = logging.getLogger("chicken24")


def setup_logging() -> None:
    # SafeFormatter: telefon, token va baza paroli loglarda yashiriladi
    fmt = SafeFormatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)


async def every(seconds: float, job, name: str) -> None:
    """Fon vazifasi: `job()` ni har `seconds` da chaqiradi; xato bo'lsa log yozadi va davom etadi."""
    while True:
        await asyncio.sleep(seconds)
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fon vazifasida xato: %s", name)


async def remove_webhook_for_polling(bot: Bot) -> None:
    """Webhook o'rnatilgan bo'lsa polling ishlamaydi (Telegram 409). Lokal polling'ga qaytishda
    webhook o'chiriladi; kutib turgan xabarlar saqlanadi. Serverdagi bot shu payt ishlamasligi kerak."""
    info = await bot.get_webhook_info()
    if info.url:
        logger.warning(
            "Telegram'da webhook o'rnatilgan edi - polling uchun o'chirildi. Agar serverdagi (Render) bot "
            "ham ishlab tursa, uni to'xtating: ikkalasi bir vaqtda ishlamaydi."
        )
        await bot.delete_webhook(drop_pending_updates=False)


async def run(config: Config) -> None:
    # Menyu: Pages (WEBAPP_URL/menu.json) asosiy, lokal menu.json - zaxira
    menu_store = MenuProvider(
        MenuStore(config.menu_path), config.menu_url, max_age=REMOTE_MENU_MAX_AGE_SECONDS
    )
    await menu_store.refresh()
    menu = menu_store.get()  # ikkalasi ham bo'lmasa MenuError - darhol aniq xato
    logger.info("Narxlar manbai: %s", "Pages" if menu_store.source == "pages" else f"lokal ({config.menu_path})")
    if not menu.items:
        logger.warning("Menyuda birorta taom yo'q - Mini App'da barcha kategoriyalar 'Tez orada' bo'ladi")

    db = Database.from_config(config)
    await db.connect()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(
        config=config,
        menu_store=menu_store,
        db=db,
        limiter=RateLimiter(ORDER_COOLDOWN_SECONDS),
    )
    dp.include_router(router)

    tasks: list[asyncio.Task] = []
    try:
        me = await bot.get_me()
        logger.info("Bot ishga tushdi: @%s (admin_id=%s)", me.username, config.admin_id)
        # "/" menyusi: mijozlarga faqat /start, admin'ga to'liq ro'yxat (xato bo'lsa ham bot to'xtamaydi)
        await commands.apply_commands(bot, config.admin_id)
        # Oldin admin'ga yetmagan buyurtmalar: avval ishga tushganda (polling'dan oldin, poyga yo'q),
        # keyin har 2 daqiqada (yangi buyurtmalarga 60 soniya tegmasdan)
        await resend_unnotified(bot, config, db, min_age_seconds=0)
        # Bazaga faqat yangi buyurtma yoki yuborish xatosidan keyin murojaat qilinadi (bo'sh so'rov yo'q)
        tasks.append(asyncio.create_task(every(
            RESEND_INTERVAL_SECONDS,
            lambda: resend_if_needed(bot, config, db, min_age_seconds=RESEND_MIN_AGE_SECONDS),
            "qayta yuborish",
        )))
        if config.menu_url:
            tasks.append(asyncio.create_task(every(MENU_REFRESH_SECONDS, menu_store.refresh, "menyu yangilash")))
        # Zaxira: o'tkazib yuborilgan bo'lsa darhol, keyin har kuni BACKUP_TIME da
        if BACKUP_ENABLED:
            try:
                await backup.startup_backup_if_due(bot, config, db)
            except Exception:
                logger.exception("Ishga tushishdagi zaxirada xato")
            tasks.append(asyncio.create_task(backup.backup_loop(bot, config, db)))
        if config.use_webhook:
            logger.info("Rejim: webhook (port %s)", config.port)
            await webhook.serve(dp, bot, base_url=config.webhook_url, secret=config.webhook_secret,
                                port=config.port)
        else:
            logger.info("Rejim: polling")
            await remove_webhook_for_polling(bot)
            # Polling tarmoq xatolarida o'zi qayta ulanadi; handler xatolari router.errors() da ushlanadi
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db.close()
        await bot.session.close()
        logger.info("Bot to'xtadi")


# Kutilmagan xatoda (masalan, ishga tushishda internet yo'q) jarayon yiqilmaydi,
# shu vaqtdan keyin qayta uriniladi.
RESTART_DELAY_SECONDS = 15


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[XATO] Sozlamalar: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging()
    while True:
        try:
            asyncio.run(run(config))
            break  # normal to'xtash (SIGINT/SIGTERM)
        except KeyboardInterrupt:
            break
        except MenuError as exc:
            # menu.json buzuq/yo'q - qayta urinish foyda bermaydi
            logger.error("Menyu xatosi: %s", exc)
            sys.exit(1)
        except TelegramUnauthorizedError:
            logger.error("BOT_TOKEN noto'g'ri yoki bekor qilingan (Telegram 401). To'g'ri tokenni kiriting.")
            sys.exit(1)
        except Exception:
            logger.exception("Kutilmagan xato, bot %s soniyadan keyin qayta ishga tushadi", RESTART_DELAY_SECONDS)
            try:
                time.sleep(RESTART_DELAY_SECONDS)
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    main()
