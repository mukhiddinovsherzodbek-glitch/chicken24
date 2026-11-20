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
from datetime import datetime, timezone
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
import orderapi
import selfping
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


STARTUP_MENU_TIMEOUT_SECONDS = 5.0  # webhook rejimida Pages menyusini ishga tushishda shuncha kutamiz


async def load_menu(menu_store: MenuProvider, config: Config, *, timeout: float | None) -> None:
    """Pages menyusini oladi (timeout bilan - sekin tarmoq ishga tushishni ushlamasin), so'ng tekshiradi."""
    try:
        if timeout is None:
            await menu_store.refresh()
        else:
            await asyncio.wait_for(menu_store.refresh(), timeout)
    except asyncio.TimeoutError:
        logger.warning("Pages menyusi %.0f soniyada olinmadi - lokal menu.json, keyin fonda yangilanadi", timeout)
    menu = menu_store.get()  # ikkalasi ham bo'lmasa MenuError - darhol aniq xato
    logger.info("Narxlar manbai: %s", "Pages" if menu_store.source == "pages" else f"lokal ({config.menu_path})")
    if not menu.items:
        logger.warning("Menyuda birorta taom yo'q - Mini App'da barcha kategoriyalar 'Tez orada' bo'ladi")


async def startup_jobs(bot: Bot, config: Config, db: Database, started_at: datetime) -> None:
    """Telegram'ga bog'liq boshlang'ich ishlar. Webhook rejimida fonda (javob berishni sekinlashtirmaydi)."""
    me = await bot.get_me()
    logger.info("Bot: @%s (admin_id=%s)", me.username, config.admin_id)
    # "/" menyusi: mijozlarga faqat /start, admin'ga to'liq ro'yxat (xato bo'lsa ham bot to'xtamaydi)
    await commands.apply_commands(bot, config.admin_id)
    # Ishga tushishdan OLDINGI, admin'ga yetmagan buyurtmalar (keyingilarini handler o'zi yuboradi - poyga yo'q)
    await resend_unnotified(bot, config, db, before=started_at)
    # Zaxira: o'tkazib yuborilgan bo'lsa darhol
    if BACKUP_ENABLED:
        try:
            await backup.startup_backup_if_due(bot, config, db)
        except Exception:
            logger.exception("Ishga tushishdagi zaxirada xato")


async def guarded(coro, name: str) -> None:
    """Fon vazifasi xatosi botni to'xtatmasin."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Fon vazifasida xato: %s", name)


def background_tasks(bot: Bot, config: Config, db: Database, menu_store: MenuProvider) -> list[asyncio.Task]:
    tasks = [asyncio.create_task(every(
        RESEND_INTERVAL_SECONDS,
        # Bazaga faqat yangi buyurtma yoki yuborish xatosidan keyin murojaat qilinadi (bo'sh so'rov yo'q)
        lambda: resend_if_needed(bot, config, db, min_age_seconds=RESEND_MIN_AGE_SECONDS),
        "qayta yuborish",
    ))]
    if config.menu_url:
        tasks.append(asyncio.create_task(every(MENU_REFRESH_SECONDS, menu_store.refresh, "menyu yangilash")))
    if BACKUP_ENABLED:
        tasks.append(asyncio.create_task(backup.backup_loop(bot, config, db)))
    if config.self_ping_url:
        tasks.append(asyncio.create_task(selfping.self_ping_loop(config.self_ping_url)))
    return tasks


def startup_summary(config: Config, db: Database) -> str:
    """Bitta qator: rejim, baza turi, self-ping. Manzil, parol va token yo'q."""
    ping = f"yoqilgan (har {selfping.SELF_PING_SECONDS // 60} daqiqa)" if config.self_ping_url else "o'chiq"
    return (f"Ishga tushdi: rejim={'webhook' if config.use_webhook else 'polling'}, "
            f"baza={db.kind}, self-ping={ping}")


async def run(config: Config) -> None:
    started_at = datetime.now(timezone.utc)
    # Menyu: Pages (WEBAPP_URL/menu.json) asosiy, lokal menu.json - zaxira
    menu_store = MenuProvider(
        MenuStore(config.menu_path), config.menu_url, max_age=REMOTE_MENU_MAX_AGE_SECONDS
    )
    db = Database.from_config(config)
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(
        config=config,
        menu_store=menu_store,
        db=db,
        limiter=RateLimiter(ORDER_COOLDOWN_SECONDS),
    )
    dp.include_router(router)

    tasks: list[asyncio.Task] = []
    runner = None
    try:
        if config.use_webhook:
            # 1) HTTP server ENG BIRINCHI: Render port'ni darhol ko'radi, /health darhol javob beradi
            stop = asyncio.Event()
            webhook.install_stop_signals(stop)
            ready = asyncio.Event()
            # Mini App buyurtma holatini shu serverdan so'raydi (CORS - faqat WEBAPP_URL origin'i)
            api = orderapi.OrderStatusAPI(db=db, bot_token=config.bot_token,
                                          allowed_origin=orderapi.origin_of(config.webapp_url), ready=ready)
            runner = await webhook.start_server(dp, bot, secret=config.webhook_secret, port=config.port,
                                                ready=ready, api=api)
            # 2) Baza va menyu - parallel (Neon uyg'onishi va Pages so'rovi bir-birini kutmaydi)
            await asyncio.gather(
                db.connect(),
                load_menu(menu_store, config, timeout=STARTUP_MENU_TIMEOUT_SECONDS),
            )
            ready.set()  # shu paytdan Telegram yangilanishlari qayta ishlanadi
            # 3) setWebhook - har ishga tushishda (idempotent, kutib turgan xabarlar saqlanadi)
            await webhook.set_webhook(dp, bot, base_url=config.webhook_url, secret=config.webhook_secret)
            # 4) Qolgani fonda
            tasks.append(asyncio.create_task(guarded(startup_jobs(bot, config, db, started_at), "boshlang'ich ishlar")))
            tasks += background_tasks(bot, config, db, menu_store)
            logger.info(startup_summary(config, db))
            await stop.wait()
            logger.info("To'xtash signali olindi")
        else:
            await load_menu(menu_store, config, timeout=None)
            await db.connect()
            await startup_jobs(bot, config, db, started_at)
            tasks += background_tasks(bot, config, db, menu_store)
            logger.info(startup_summary(config, db))
            await remove_webhook_for_polling(bot)
            # Polling tarmoq xatolarida o'zi qayta ulanadi; handler xatolari router.errors() da ushlanadi
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if runner is not None:
            await runner.cleanup()
        await db.close()
        await bot.session.close()
        detach_router(dp)
        logger.info("Bot to'xtadi")


def detach_router(dp: Dispatcher) -> None:
    """Xatodan keyin run() qayta chaqirilganda router yangi Dispatcher'ga ulana olsin
    (aiogram bitta routerni ikkinchi marta ulashga ruxsat bermaydi: 'Router is already attached')."""
    if router in dp.sub_routers:
        dp.sub_routers.remove(router)
    router._parent_router = None  # aiogram'da ajratish uchun ochiq usul yo'q


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
