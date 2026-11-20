"""PostgreSQL testlari: TEST_DATABASE_URL berilganda ishlaydi, bo'lmasa o'tkazib yuboriladi (SKIP).

    $env:TEST_DATABASE_URL = "postgresql://..."   # alohida TEST bazasi (ishchi baza emas!)
    .\\venv\\Scripts\\python.exe tests\\test_postgres.py

Har test o'z vaqtinchalik sxemasida ishlaydi (c24test_<tasodifiy>) va oxirida o'sha sxema o'chiriladi -
bazadagi boshqa jadvallarga tegilmaydi.

Asosiy g'oya - bir xil ssenariy SQLite va Postgres'da bajariladi va natijalar solishtiriladi.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sqlite3
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import backup  # noqa: E402
import business  # noqa: E402
import config as config_module  # noqa: E402
from db import TABLES, Database, DatabaseError, pg_connect_params  # noqa: E402
from handlers import (  # noqa: E402
    build_webapp_url,
    on_cancel_order,
    on_web_app_data,
    resend_unnotified,
)
from test_flow import Env, FakeBot, FakeCallback, good, make_message  # noqa: E402

business.is_open_now = lambda: True

DSN = os.environ.get("TEST_DATABASE_URL", "").strip()
BIG_USER = 7_123_456_789  # Telegram ID 2^31 dan katta bo'lishi mumkin


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def pg_schema():
    """Vaqtinchalik sxema: shu sxemaga ulangan Database qaytaradi, oxirida sxema o'chiriladi."""
    import asyncpg

    schema = "c24test_" + secrets.token_hex(6)
    dsn, ssl = pg_connect_params(DSN)
    admin = await asyncpg.connect(dsn, ssl=ssl, statement_cache_size=0)
    await admin.execute(f"CREATE SCHEMA {schema}")
    try:
        yield schema, {"search_path": schema}
    finally:
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


def pg_db(settings: dict) -> Database:
    return Database(dsn=DSN, server_settings=settings)


async def scenario(env: Env) -> dict:
    """Bir xil ssenariy: handlerlar orqali buyurtma, rad, bekor, til, telefon, hisobot, eksport."""
    db = env.db
    bot = FakeBot()
    now = datetime.now(timezone.utc)
    out: dict = {}
    await db.set_user_lang(BIG_USER, "ru", now)
    await db.set_user_lang(BIG_USER, "uz", now)  # ON CONFLICT yangilanadi
    await db.save_verified_phone(BIG_USER, "+998901234567", now)
    await on_web_app_data(make_message(BIG_USER, good())[0], **env.deps(bot), lang="uz")              # #1 ok
    env.limiter.release(BIG_USER)
    await on_web_app_data(make_message(BIG_USER, good(ref="rej00001", lat=41.2995, lon=69.2401))[0],
                          **env.deps(bot), lang="uz")                                                    # rad (hudud)
    out["url_after_rej"] = await build_webapp_url(env.config, db, BIG_USER, "ru")
    await on_web_app_data(make_message(2, good(ref="cx000002", address_type="text", address_text="Registon 1",
                                                lat=None, lon=None, comment="Domofon 5"))[0],
                          **env.deps(bot), lang="ru")                                                    # #2
    cb = FakeCallback(2, "cancel:2")
    await on_cancel_order(cb, bot, env.config, db, lang="ru")
    out["cancel_again"] = await db.cancel_order(2, 2, datetime.now(timezone.utc), 300)
    out["cancel_foreign"] = await db.cancel_order(1, 2, datetime.now(timezone.utc), 300)
    out["cancel_expired"] = await db.cancel_order(1, BIG_USER, datetime.now(timezone.utc) + timedelta(minutes=6), 300)
    await on_web_app_data(make_message(3, good(ref="ok000003", items=[{"id": "burger-2", "qty": 3}]))[0],
                          **env.deps(bot), lang="uz")                                                    # #3
    out["results"] = {u: await db.latest_result(u) for u in (BIG_USER, 2, 3, 4)}
    out["urls"] = {u: await build_webapp_url(env.config, db, u, "uz") for u in (BIG_USER, 2, 3, 4)}
    out["langs"] = {u: await db.get_user_lang(u) for u in (BIG_USER, 2, 4)}
    out["phone"] = await db.get_verified_phone(BIG_USER)
    out["order1"] = await db.get_order(1)
    out["missing"] = await db.get_order(99)
    out["signature"] = await db.data_signature()
    out["counts"] = await db.order_counts()
    stats = await db.period_stats(now - timedelta(hours=1), now + timedelta(hours=1))
    out["stats"] = stats
    out["stats_empty"] = await db.period_stats(now + timedelta(days=1), now + timedelta(days=2))
    out["export_all"] = await db.orders_with_items()
    out["export_window"] = len(await db.orders_with_items(now - timedelta(hours=1), now + timedelta(hours=1)))
    out["export_none"] = len(await db.orders_with_items(None, now - timedelta(days=1)))
    await db.set_meta("k", "1")
    await db.set_meta("k", "2")
    out["meta"] = await db.get_meta("k")
    out["unnotified"] = len(await db.get_unnotified(datetime.now(timezone.utc)))
    # Mini App holati (orderapi): o'zi / boshqa foydalanuvchi / noma'lum ref
    out["order_status"] = {f"{u}:{ref}": await db.order_status(ref, user_id=u) for u, ref in (
        (BIG_USER, "abc123xyz"), (BIG_USER, "rej00001"), (2, "cx000002"), (3, "ok000003"),
        (2, "ok000003"), (BIG_USER, "nothere1"))}
    out["order_status_by_ref"] = {ref: await db.order_status(ref) for ref in ("abc123xyz", "rej00001", "nothere1")}
    out["tables"] = await db.export_tables()
    return out


def normalize(out: dict) -> dict:
    """Vaqt belgilari ikkala bazada farq qiladi (har biri o'z vaqtida yozilgan) - solishtirishdan oldin olib tashlanadi."""
    def strip(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in ("created_at", "cancelled_at")}

    norm = dict(out)
    norm["order1"] = strip(out["order1"])
    norm["export_all"] = [(strip(o), items) for o, items in out["export_all"]]
    tables = {}
    for table, rows in out["tables"].items():
        cols = TABLES[table]
        tables[table] = [tuple(v for c, v in zip(cols, r) if c not in ("created_at", "cancelled_at", "updated_at"))
                         for r in rows]
    norm["tables"] = tables
    return norm


def skip() -> bool:
    if not DSN:
        print("SKIP: TEST_DATABASE_URL berilmagan - Postgres testlari o'tkazib yuborildi")
        return True
    return False


# ------------------------------------------------------------ SQLite == Postgres

def test_same_results_on_sqlite_and_postgres():
    if skip():
        return

    async def go():
        lite = Env()
        await lite.db.connect()
        a = await scenario(lite)
        await lite.db.close()
        async with pg_schema() as (_, settings):
            pg = Env()
            pg.db = pg_db(settings)
            await pg.db.connect()
            b = await scenario(pg)
            await pg.db.close()
        return a, b

    a, b = run(go())
    na, nb = normalize(a), normalize(b)
    for key in na:
        assert na[key] == nb[key], f"{key}: SQLite={na[key]!r}  Postgres={nb[key]!r}"
    # mazmun ham to'g'ri
    # BIG_USER: avval qabul, keyin rad (eng oxirgisi - rad); 2: bekor qilingan; 3: qabul
    assert b["results"][BIG_USER]["kind"] == "rej" and b["results"][2]["kind"] == "cx"
    assert b["results"][3] == {"kind": "ok", "ref": "ok000003", "order_id": 3, "reason": None}
    assert b["results"][4] is None and "rej=rej00001" in b["url_after_rej"]
    assert b["cancel_again"] == "already" and b["cancel_foreign"] == "not_found" and b["cancel_expired"] == "expired"
    assert b["order1"]["user_id"] == BIG_USER and isinstance(b["order1"]["lat"], float)
    assert b["stats"].orders == 2 and b["stats"].cancelled == 1 and b["stats"].top[0] == ("Cheese <b>", 4, 120000)
    assert b["counts"] == (3, 1) and b["signature"] == "3:3:1"


def test_types_and_precision():
    if skip():
        return

    async def go():
        async with pg_schema() as (_, settings):
            env = Env()
            env.db = pg_db(settings)
            await env.db.connect()
            await on_web_app_data(make_message(BIG_USER, good(lat=39.654812345678, lon=66.975712345678))[0],
                                  **env.deps(FakeBot()), lang="uz")
            row = await env.db.get_order(1)
            await env.db.close()
            return row

    row = run(go())
    assert row["lat"] == 39.654812345678 and row["lon"] == 66.975712345678, "DOUBLE PRECISION - aniqlik yo'qolmaydi"
    assert row["user_id"] == BIG_USER and isinstance(row["total"], int) and isinstance(row["created_at"], str)


def test_reconnect_after_connections_dropped():
    """Neon uxlab qolganda ulanishlar uziladi - keyingi so'rov yangi ulanish bilan ishlaydi."""
    if skip():
        return

    async def go():
        async with pg_schema() as (_, settings):
            db = pg_db(settings)
            await db.connect()
            await db.set_user_lang(1, "ru", datetime.now(timezone.utc))
            async with db.pg_connection() as con:
                await con.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                  "WHERE application_name = 'chicken24-bot' AND pid <> pg_backend_pid()")
            lang = await db.get_user_lang(1)
            await db.close()
            return lang

    assert run(go()) == "ru"


def test_error_message_has_no_row_values():
    if skip():
        return

    async def go():
        async with pg_schema() as (_, settings):
            db = pg_db(settings)
            await db.connect()
            try:
                await db._db.execute("INSERT INTO orders (user_id, customer_name, phone, address_type, total, "
                                     "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                     (1, "Aziz", "+998901234567", "noto'g'ri", 1, "2026-01-01T00:00:00+00:00"))
            except DatabaseError as exc:
                return str(exc)
            finally:
                await db.close()
        return ""

    msg = run(go())
    assert msg.startswith("CheckViolationError [23514]") and "901234567" not in msg and "Aziz" not in msg


# ------------------------------------------------------------ zaxira: Postgres -> SQLite .db

def test_backup_from_postgres_is_sqlite_file():
    if skip():
        return

    class DocBot:
        def __init__(self) -> None:
            self.docs, self.msgs = [], []

        async def send_document(self, chat_id, document, caption=None, **kw):
            self.docs.append((document, caption))

        async def send_message(self, chat_id, text, **kw):
            self.msgs.append(text)

    async def go():
        async with pg_schema() as (_, settings):
            env = Env()
            env.db = pg_db(settings)
            await env.db.connect()
            await on_web_app_data(make_message(BIG_USER, good())[0], **env.deps(FakeBot()), lang="uz")
            await on_web_app_data(make_message(2, good(ref="r2"))[0], **env.deps(FakeBot()), lang="ru")
            tables = await env.db.export_tables()
            bot = DocBot()
            ok = await backup.send_backup(bot, env.config, env.db, reason="sinov")
            changed = await backup.has_changes(env.db)
            # 49 MB cheklovi Postgres rejimida ham ishlaydi
            limit = config_module.BACKUP_MAX_BYTES
            config_module.BACKUP_MAX_BYTES = 10
            try:
                too_big = await backup.send_backup(bot, env.config, env.db, reason="katta")
            finally:
                config_module.BACKUP_MAX_BYTES = limit
            await env.db.close()
            return ok, changed, too_big, bot, tables

    ok, changed, too_big, bot, tables = run(go())
    assert ok is True and changed is False and too_big is False
    assert len(bot.docs) == 1 and "juda katta" in bot.msgs[-1] and "Neon" in bot.msgs[-1]
    document, caption = bot.docs[0]
    assert document.filename.endswith(".db") and "Buyurtmalar: 2 ta" in caption
    path = Path(tempfile.mkdtemp()) / "restore.db"
    path.write_bytes(document.data)

    async def restore():
        db = Database(path)  # nusxa lokal botda ochiladi
        await db.connect()
        t = await db.export_tables()
        res = await db.latest_result(BIG_USER)
        await db.close()
        return t, res

    restored, result = run(restore())
    assert restored["orders"] == tables["orders"] and restored["order_items"] == tables["order_items"]
    assert result["kind"] == "ok" and result["order_id"] == 1
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


# ------------------------------------------------------------ ko'chirish skripti

def test_migration_idempotent_and_ids_continue():
    if skip():
        return
    import migrate_sqlite_to_postgres as mig

    async def make_sqlite():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        await env.db.set_user_lang(BIG_USER, "ru", datetime.now(timezone.utc))
        await on_web_app_data(make_message(BIG_USER, good())[0], **env.deps(bot), lang="ru")
        await on_web_app_data(make_message(2, good(ref="b2"))[0], **env.deps(bot), lang="uz")
        env.limiter.release(2)
        await on_web_app_data(make_message(2, good(ref="rejref02", lat=41.2995, lon=69.2401))[0], **env.deps(bot))
        await env.db.set_meta("backup_last_sig", "2:2:0")
        expected = await env.db.export_tables()
        await env.db.close()
        return env, expected

    env, expected = run(make_sqlite())
    data = mig.read_sqlite(env.config.db_path)

    async def go():
        async with pg_schema() as (_, settings):
            first = await mig.migrate(DSN, data, server_settings=settings)
            second = await mig.migrate(DSN, data, server_settings=settings)
            pg = Env()
            pg.db = pg_db(settings)
            await pg.db.connect()
            tables = await pg.db.export_tables()
            result = await pg.db.latest_result(2)
            # yangi buyurtma ID si ko'chirilganlardan keyin davom etadi
            await on_web_app_data(make_message(5, good(ref="new5"))[0], **pg.deps(FakeBot()), lang="uz")
            new_order = await pg.db.get_order(3)
            await pg.db.close()
            # boshqa ma'lumotli SQLite (shu ID - boshqa buyurtma) -> to'xtaydi, hech narsa yozilmaydi
            conflict = [list(r) for r in data["orders"]]
            conflict[0][TABLES["orders"].index("created_at")] = "2020-01-01T00:00:00+00:00"
            bad = {**data, "orders": [tuple(r) for r in conflict]}
            try:
                await mig.migrate(DSN, bad, server_settings=settings)
                conflict_error = None
            except mig.MigrationConflict as exc:
                conflict_error = str(exc)
            return first, second, tables, result, new_order, conflict_error

    first, second, tables, result, new_order, conflict_error = run(go())
    assert first["orders"] == {"sqlite": 2, "added": 2, "skipped": 0, "postgres": 2}
    assert first["order_items"]["added"] == 4 and first["last_rejections"]["added"] == 1
    assert all(s["added"] == 0 and s["skipped"] == s["sqlite"] for s in second.values()), "qayta ishga tushirish ikkilantirmaydi"
    assert tables == expected, "ko'chirilgan ma'lumot SQLite bilan bir xil"
    assert result["kind"] == "rej" and result["ref"] == "rejref02"
    assert new_order is not None and new_order["user_id"] == 5
    assert conflict_error and "orders" in conflict_error and "+998" not in conflict_error


def test_resend_after_migration_sends_unnotified():
    """Admin'ga yetmagan buyurtma (notified=0) ko'chirilgandan keyin serverdagi bot uni yuboradi."""
    if skip():
        return
    import migrate_sqlite_to_postgres as mig

    async def make_sqlite():
        env = Env()
        await env.db.connect()
        await on_web_app_data(make_message(1, good())[0], **env.deps(FakeBot(fail_admin=True)), lang="uz")
        await env.db.close()
        return env

    env = run(make_sqlite())
    data = mig.read_sqlite(env.config.db_path)

    async def go():
        async with pg_schema() as (_, settings):
            await mig.migrate(DSN, data, server_settings=settings)
            pg = Env()
            pg.db = pg_db(settings)
            await pg.db.connect()
            bot = FakeBot()
            sent = await resend_unnotified(bot, pg.config, pg.db)
            row = await pg.db.get_order(1)
            await pg.db.close()
            return sent, row["notified"]

    assert run(go()) == (1, 1)


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    if not DSN:
        print("SKIP: TEST_DATABASE_URL berilmagan - Postgres testlari o'tkazib yuborildi")
        print(f"\n0/{len(tests)} test o'tdi ({len(tests)} SKIP)")
        sys.exit(0)
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK    {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} test o'tdi")
    sys.exit(1 if failed else 0)
