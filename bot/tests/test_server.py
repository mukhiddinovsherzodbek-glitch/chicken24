"""Server (Render) rejimi testlari - Postgres serversiz ham ishlaydi.

- sozlamalar: DATABASE_URL / WEBHOOK_URL / WEBHOOK_SECRET / PORT tekshiruvi, maxfiy qiymatlar repr'da yo'q;
- webhook: secret_token tekshiruvi (401), sir yo'lak, /health (bazaga tegmaydi);
- polling'ga qaytishda webhook o'chiriladi;
- loglarda telefon / token / parol yashirinadi;
- fon qayta yuborish bo'sh bazaga so'rov yubormaydi;
- Postgres manzili (SSL, Neon parametrlari), xato matnidan DETAIL olib tashlanadi;
- zaxira nusxa yozuvchisi va ko'chirish skriptining SQLite qismi;
- render.yaml, keep-alive, .env.example, .gitignore va repo'da token yo'qligi.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import sqlite3
import ssl
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import business  # noqa: E402
import config as config_module  # noqa: E402
import db as db_module  # noqa: E402
import main as main_module  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher, Router  # noqa: E402
from aiogram.types import Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from db import Database, clean_pg_error, describe_dsn, pg_connect_params, to_pg_sql, write_sqlite_copy  # noqa: E402
from handlers import resend_if_needed, resend_unnotified, on_web_app_data  # noqa: E402
from logsafe import SafeFormatter, mask_phone, scrub  # noqa: E402
from test_flow import Env, FakeBot, good, make_message  # noqa: E402
from validators import ValidationError, parse_payload, validate_order  # noqa: E402

business.is_open_now = lambda: True

SECRET = "Test_secret-0123456789abcdef"
FAKE_TOKEN = "123456789:" + "A" * 35  # haqiqiy emas


def run(coro):
    return asyncio.run(coro)


# ================================================================ sozlamalar

@contextmanager
def env(**values):
    """Muhit o'zgaruvchilarini vaqtincha o'rnatadi; bot/.env o'qilmaydi (load_dotenv o'chirilgan)."""
    keys = ("BOT_TOKEN", "ADMIN_ID", "WEBAPP_URL", "DATABASE_URL", "WEBHOOK_URL", "WEBHOOK_SECRET", "PORT",
            "MENU_URL", "MENU_PATH", "DB_PATH", "PUBLIC_URL", "RENDER_EXTERNAL_URL")
    saved = {k: os.environ.get(k) for k in keys}
    original_dotenv = config_module.load_dotenv
    config_module.load_dotenv = lambda *a, **kw: False
    try:
        for k in keys:
            os.environ.pop(k, None)
        base = {"BOT_TOKEN": FAKE_TOKEN, "ADMIN_ID": "999", "WEBAPP_URL": "https://example.pages.dev"}
        for k, v in {**base, **values}.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        config_module.load_dotenv = original_dotenv
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def config_error(**values) -> str:
    with env(**values):
        try:
            config_module.load_config()
        except config_module.ConfigError as exc:
            return str(exc)
    raise AssertionError("ConfigError kutilgan edi")


def test_config_defaults_local_mode():
    with env():
        cfg = config_module.load_config()
    assert cfg.database_url is None and cfg.webhook_url is None and cfg.use_webhook is False
    assert cfg.port == config_module.DEFAULT_PORT
    assert Database.from_config(cfg).kind == "sqlite"


def test_config_server_mode():
    dsn = "postgresql://neondb_owner:S3cretPass@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    with env(DATABASE_URL=dsn, WEBHOOK_URL="https://chicken24-bot.onrender.com/", WEBHOOK_SECRET=SECRET, PORT="10000"):
        cfg = config_module.load_config()
    assert cfg.use_webhook and cfg.webhook_url == "https://chicken24-bot.onrender.com" and cfg.port == 10000
    assert cfg.database_url == dsn and Database.from_config(cfg).kind == "postgres"
    text = repr(cfg)
    for secret in (FAKE_TOKEN, SECRET, "S3cretPass"):
        assert secret not in text, "maxfiy qiymat repr'da ko'rinmasin"


def test_config_rejects_bad_server_values():
    assert "WEBHOOK_SECRET yo'q" in config_error(WEBHOOK_URL="https://a.onrender.com")
    assert "https://" in config_error(WEBHOOK_URL="http://a.onrender.com", WEBHOOK_SECRET=SECRET)
    assert "? va #" in config_error(WEBHOOK_URL="https://a.onrender.com/?x=1", WEBHOOK_SECRET=SECRET)
    # base64 belgilari (+ / =) Telegram'da ruxsat etilmagan; qisqa sir ham
    assert "16-256" in config_error(WEBHOOK_URL="https://a.onrender.com", WEBHOOK_SECRET="abc+def/ghi=jkl0123")
    assert "16-256" in config_error(WEBHOOK_URL="https://a.onrender.com", WEBHOOK_SECRET="short")
    msg = config_error(DATABASE_URL="mysql://u:TopSecret@h/db")
    assert "postgresql://" in msg and "TopSecret" not in msg
    assert "PORT" in config_error(PORT="abc")
    # Sirning o'zi bo'lsa, lekin manzil bo'lmasa - polling (xato emas)
    with env(WEBHOOK_SECRET=SECRET):
        assert config_module.load_config().use_webhook is False


# ================================================================ webhook va /health

class ExplodingDB:
    """Har qanday murojaatda xato - /health bazaga tegmasligini isbotlash uchun."""

    def __getattr__(self, name):
        raise AssertionError(f"/health bazaga murojaat qildi: {name}")


def update_json(text: str) -> dict:
    return {"update_id": 1, "message": {
        "message_id": 1, "date": 0, "text": text,
        "chat": {"id": 5, "type": "private"}, "from": {"id": 5, "is_bot": False, "first_name": "A"},
    }}


def test_webhook_secret_and_health():
    async def scenario():
        bot = Bot(FAKE_TOKEN)
        dp = Dispatcher(db=ExplodingDB())
        router = Router()
        got: list[str] = []

        @router.message()
        async def capture(message: Message) -> None:
            got.append(message.text)

        dp.include_router(router)
        path = webhook.webhook_path(SECRET)
        out = {}
        async with TestClient(TestServer(webhook.build_app(dp, bot, SECRET))) as client:
            resp = await client.get("/health")
            out["health"] = (resp.status, await resp.json())
            head = await client.head("/health")
            out["head"] = head.status
            body = json.dumps(update_json("salom"))
            hdr = {"Content-Type": "application/json"}
            out["no_secret"] = (await client.post(path, data=body, headers=hdr)).status
            bad = {**hdr, "X-Telegram-Bot-Api-Secret-Token": SECRET + "x"}
            out["bad_secret"] = (await client.post(path, data=body, headers=bad)).status
            out["handled_after_bad"] = list(got)
            ok = {**hdr, "X-Telegram-Bot-Api-Secret-Token": SECRET}
            out["ok"] = (await client.post(path, data=body, headers=ok)).status
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.02)
            out["handled"] = list(got)
            out["get_path"] = (await client.get(path)).status
            out["secret_in_url"] = (await client.post(f"/tg/{SECRET}", data=body, headers=ok)).status
            out["root"] = (await client.get("/")).status
        await bot.session.close()
        return out, path

    out, path = run(scenario())
    assert out["health"] == (200, {"ok": True})
    assert out["head"] == 200
    assert out["no_secret"] == 401 and out["bad_secret"] == 401 and out["handled_after_bad"] == []
    assert out["ok"] == 200 and out["handled"] == ["salom"]
    assert out["get_path"] == 405
    assert out["secret_in_url"] == 404 and out["root"] == 404
    assert SECRET not in path and re.fullmatch(r"/tg/[0-9a-f]{40}", path)


def test_webhook_path_is_stable_and_secret_specific():
    assert webhook.webhook_path(SECRET) == webhook.webhook_path(SECRET)
    assert webhook.webhook_path(SECRET) != webhook.webhook_path(SECRET + "2")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_sets_webhook_with_secret_and_stops():
    class HookBot:
        def __init__(self) -> None:
            self.calls: list = []
            self.closed = 0

            async def close() -> None:
                self.closed += 1

            self.session = SimpleNamespace(close=close)  # server to'xtaganda aiogram sessiyani yopadi

        async def set_webhook(self, url, **kw):
            self.calls.append((url, kw))
            stop.set()  # o'rnatildi - serverni to'xtatamiz

    stop: asyncio.Event

    async def scenario():
        nonlocal stop
        stop = asyncio.Event()
        bot = HookBot()
        dp = Dispatcher()
        port = free_port()
        await asyncio.wait_for(
            webhook.serve(dp, bot, base_url="https://x.onrender.com/", secret=SECRET, port=port,
                          host="127.0.0.1", stop=stop),
            timeout=10,
        )
        # server to'xtagan - port yana bo'sh
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
        return bot.calls

    calls = run(scenario())
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == "https://x.onrender.com" + webhook.webhook_path(SECRET)
    assert kw["secret_token"] == SECRET and kw["drop_pending_updates"] is False


def test_polling_removes_webhook_only_when_set():
    class PollBot:
        def __init__(self, url: str) -> None:
            self.url = url
            self.deleted: list = []

        async def get_webhook_info(self):
            return SimpleNamespace(url=self.url)

        async def delete_webhook(self, **kw):
            self.deleted.append(kw)

    with_hook, without = PollBot("https://x.onrender.com/tg/abc"), PollBot("")
    main_module.logger.disabled = True
    try:
        run(main_module.remove_webhook_for_polling(with_hook))
        run(main_module.remove_webhook_for_polling(without))
    finally:
        main_module.logger.disabled = False
    assert with_hook.deleted == [{"drop_pending_updates": False}], "kutib turgan xabarlar o'chmasin"
    assert without.deleted == []


# ================================================================ loglar

def test_scrub_hides_phone_token_password():
    text = ("tel +998901234567, 998 90 123-45-67, +998-91-555-44-33; token " + FAKE_TOKEN
            + "; postgresql://owner:P4ss.w0rd@ep-x.neon.tech/db")
    out = scrub(text)
    for leaked in ("901234567", "123-45", "555-44", "A" * 35, "P4ss.w0rd"):
        assert leaked not in out, leaked
    assert "+998 ** *** ** 67" in out and "+998 ** *** ** 33" in out
    assert "<BOT_TOKEN>" in out and "postgresql://owner:***@ep-x.neon.tech/db" in out
    # Oddiy raqamlar (user_id, buyurtma raqami, summa) o'zgarmaydi
    assert scrub("user_id=5012345678 order #42 jami=70000") == "user_id=5012345678 order #42 jami=70000"


def test_safe_formatter_covers_tracebacks():
    fmt = SafeFormatter("%(message)s")
    try:
        raise RuntimeError("telefon +998901234567 bilan xato")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "Buyurtma %s", ("+998935556677",),
                                   sys.exc_info())
    out = fmt.format(record)
    assert "901234567" not in out and "935556677" not in out and "Traceback" in out


def test_invalid_phone_is_masked_in_validation_detail():
    assert mask_phone("+998901234567") == "+99890*****67"
    assert mask_phone(12345) == "<int>" and mask_phone("12") == "**"
    data = parse_payload(json.dumps({"name": "Ali", "phone": "+99890123456789", "address_type": "text",
                                     "address_text": "x", "items": [{"id": "a", "qty": 1}]}))
    try:
        validate_order(data, None)
    except ValidationError as exc:
        assert "90123456789" not in exc.detail and exc.key == "val.phone"
    else:
        raise AssertionError("ValidationError kutilgan edi")


# ================================================================ fon qayta yuborish: bo'sh so'rov yo'q

def count_calls(db: Database) -> list:
    calls: list = []
    original = db.get_unnotified

    async def spy(*a, **kw):
        calls.append(a)
        return await original(*a, **kw)

    db.get_unnotified = spy
    return calls


def test_background_resend_skips_db_when_nothing_pending():
    async def scenario():
        env_ = Env()
        await env_.db.connect()
        calls = count_calls(env_.db)
        bot = FakeBot()
        out = {}
        out["startup_needed"] = env_.db.resend_needed()
        await resend_unnotified(bot, env_.config, env_.db, min_age_seconds=0)  # ishga tushishdagi tekshiruv
        for _ in range(3):
            await resend_if_needed(bot, env_.config, env_.db, min_age_seconds=60)
        out["idle_queries"] = len(calls)  # faqat bitta - ishga tushganda
        # yangi buyurtma -> keyingi fon tekshiruvi bazaga qaraydi
        await on_web_app_data(make_message(1, good())[0], **env_.deps(bot))
        out["after_order_needed"] = env_.db.resend_needed()
        await resend_if_needed(bot, env_.config, env_.db, min_age_seconds=60)
        out["after_order_queries"] = len(calls)
        # buyurtma hali 60 soniyadan yosh - belgi saqlanadi; vaqt o'tgach tekshiriladi va tozalanadi
        out["still_needed"] = env_.db.resend_needed()
        await resend_if_needed(bot, env_.config, env_.db, min_age_seconds=0)
        out["cleared"] = not env_.db.resend_needed()
        await env_.db.close()
        return out

    out = run(scenario())
    assert out["startup_needed"] is True
    assert out["idle_queries"] == 1
    assert out["after_order_needed"] is True and out["after_order_queries"] == 2
    assert out["still_needed"] is True and out["cleared"] is True


def test_admin_failure_keeps_resend_flag_until_delivered():
    async def scenario():
        env_ = Env()
        await env_.db.connect()
        await resend_unnotified(FakeBot(), env_.config, env_.db)
        down = FakeBot(fail_admin=True)
        await on_web_app_data(make_message(2, good())[0], **env_.deps(down))
        # admin hali yo'q - qayta yuborish ham muvaffaqiyatsiz, belgi qoladi
        await resend_if_needed(down, env_.config, env_.db, min_age_seconds=0)
        needed_while_down = env_.db.resend_needed()
        up = FakeBot()
        sent = await resend_if_needed(up, env_.config, env_.db, min_age_seconds=0)
        await env_.db.close()
        return needed_while_down, sent, env_.db.resend_needed(), [s for s in up.sent if s[1] == 999]

    needed_while_down, sent, needed_after, admin_msgs = run(scenario())
    assert needed_while_down is True and sent == 1 and needed_after is False and admin_msgs


# ================================================================ Postgres yordamchilari (serversiz)

def test_pg_connect_params_ssl_and_neon_params():
    neon = "postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    dsn, ctx = pg_connect_params(neon)
    assert "sslmode" not in dsn and "channel_binding" not in dsn and dsn.startswith("postgresql://u:p@ep-x")
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    _, ctx2 = pg_connect_params("postgresql://u:p@db.example.com/x")  # sslmode yo'q - baribir SSL
    assert isinstance(ctx2, ssl.SSLContext)
    assert pg_connect_params("postgresql://u:p@localhost/x")[1] is False
    assert pg_connect_params("postgresql://u:p@127.0.0.1:5432/x?sslmode=prefer")[1] is False
    assert pg_connect_params("postgresql://u:p@db.example.com/x?sslmode=disable")[1] is False
    assert isinstance(pg_connect_params("postgresql://u:p@localhost/x?sslmode=require")[1], ssl.SSLContext)
    assert describe_dsn(neon) == "ep-x-pooler.eu-central-1.aws.neon.tech/neondb"


def test_pg_sql_conversion():
    assert to_pg_sql("a = ? AND b IN (?,?) LIMIT ?") == "a = $1 AND b IN ($2,$3) LIMIT $4"
    assert db_module._rowcount("UPDATE 1") == 1 and db_module._rowcount("INSERT 0 3") == 3
    assert db_module._rowcount("") == 0


def test_pg_error_detail_removed():
    import asyncpg

    exc = asyncpg.exceptions.CheckViolationError('new row for relation "orders" violates check constraint')
    exc.detail = "Failing row contains (1, 5, Aziz, +998901234567, ...)"
    exc.sqlstate = "23514"
    assert "+998901234567" in str(exc)  # asyncpg o'zi DETAIL'ni qo'shadi
    clean = str(clean_pg_error(exc))
    assert clean.startswith("CheckViolationError [23514]: new row") and "Failing row" not in clean
    assert "998901234567" not in clean and "Aziz" not in clean


def test_database_rejects_bad_dsn():
    for bad in ("mysql://x", "sqlite:///x.db"):
        try:
            Database(dsn=bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        Database().conn  # noqa: B018
    except ValueError:
        pass
    else:
        raise AssertionError("yo'l ham, DSN ham yo'q - xato bo'lishi kerak")


# ================================================================ zaxira nusxa yozuvchisi (Postgres rejimida ishlatiladi)

async def filled_sqlite_env() -> Env:
    env_ = Env()
    await env_.db.connect()
    bot = FakeBot()
    await env_.db.set_user_lang(1, "ru", datetime.now(timezone.utc))
    await env_.db.save_verified_phone(1, "+998901234567", datetime.now(timezone.utc))
    await on_web_app_data(make_message(1, good())[0], **env_.deps(bot))
    env_.limiter.release(1)
    await on_web_app_data(make_message(1, good(ref="rej00001", lat=41.2995, lon=69.2401))[0], **env_.deps(bot))
    await on_web_app_data(make_message(2, good(ref="second02", address_type="text", address_text="Registon 1",
                                                lat=None, lon=None))[0], **env_.deps(bot))
    await env_.db.set_meta("backup_last_sig", "1:1:0")
    return env_


def test_sqlite_copy_writer_roundtrip():
    async def scenario():
        env_ = await filled_sqlite_env()
        tables = await env_.db.export_tables()
        result = await env_.db.latest_result(1)
        await env_.db.close()
        copy = env_.tmp / "copy.db"
        write_sqlite_copy(copy, tables)
        db2 = Database(copy)
        await db2.connect()
        tables2 = await db2.export_tables()
        result2 = await db2.latest_result(1)
        next_id = await db2.save_order(user_id=3, username=None, order=_order(), created_at=datetime.now(timezone.utc))
        await db2.close()
        return tables, tables2, result, result2, next_id

    tables, tables2, result, result2, next_id = run(scenario())
    assert tables == tables2 and result == result2
    assert len(tables["orders"]) == 2 and len(tables["last_rejections"]) == 1 and len(tables["meta"]) == 1
    assert next_id == 3, "AUTOINCREMENT nusxada ham davom etadi"


def _order():
    from test_flow import MENU
    from menu import parse_menu

    data = {"name": "Ali", "phone": "+998901112233", "address_type": "text", "address_text": "x",
            "items": [{"id": "burger-1", "qty": 1}]}
    return validate_order(data, parse_menu(MENU), delivery_fee=10000)


# ================================================================ ko'chirish skripti: SQLite qismi

def test_migration_reads_old_sqlite_without_touching_it():
    import migrate_sqlite_to_postgres as mig

    tmp = Path(tempfile.mkdtemp()) / "old.db"
    con = sqlite3.connect(tmp)
    con.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, "
        "customer_name TEXT NOT NULL, phone TEXT NOT NULL, address_type TEXT NOT NULL, address_text TEXT, "
        "lat REAL, lon REAL, total INTEGER NOT NULL, created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute("INSERT INTO orders (user_id, customer_name, phone, address_type, address_text, total, created_at) "
                "VALUES (7, 'Ali', '+998901234567', 'text', 'x', 50000, '2026-01-01T10:00:00+00:00')")
    con.commit()
    con.close()
    before = tmp.read_bytes()
    data = mig.read_sqlite(tmp)
    assert tmp.read_bytes() == before, "asl fayl o'zgarmasligi kerak"
    cols = db_module.TABLES["orders"]
    row = dict(zip(cols, data["orders"][0]))
    assert row["items_total"] == 50000 and row["delivery_fee"] == 0 and row["status"] == "new"
    assert set(data) == set(db_module.TABLES)


def test_migration_cli_output_has_no_personal_data():
    import io
    from contextlib import redirect_stderr, redirect_stdout

    import migrate_sqlite_to_postgres as mig

    async def fake_migrate(dsn, data, **kw):
        return {t: {"sqlite": len(r), "added": len(r), "skipped": 0, "postgres": len(r)} for t, r in data.items()}

    async def make():
        env_ = await filled_sqlite_env()
        await env_.db.close()
        return env_.config.db_path

    path = run(make())
    original = mig.migrate
    mig.migrate = fake_migrate
    os.environ["DATABASE_URL"] = "postgresql://owner:Pw0rd@ep-x.neon.tech/neondb?sslmode=require"
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = mig.main(["--sqlite", str(path)])
    finally:
        mig.migrate = original
        os.environ.pop("DATABASE_URL", None)
    text = out.getvalue() + err.getvalue()
    assert code == 0, text
    for leaked in ("+998", "901234567", "Aziz", "Registon", "Pw0rd", "owner"):
        assert leaked not in text, leaked
    assert re.search(r"orders\s+2\s+2\s+0\s+2", text) and "ep-x.neon.tech/neondb" in text


# ================================================================ deploy fayllari

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_render_yaml():
    text = read("render.yaml")
    for needle in ("type: web", "runtime: python", "plan: free", "region: frankfurt", "healthCheckPath: /health",
                   "buildCommand: pip install -r requirements.txt", "startCommand: python bot/main.py",
                   "key: PYTHON_VERSION", 'value: "3.12.10"'):
        assert needle in text, needle
    for key in ("BOT_TOKEN", "ADMIN_ID", "DATABASE_URL", "WEBHOOK_URL", "WEBHOOK_SECRET"):
        assert re.search(rf"- key: {key}\n\s+sync: false", text), f"{key} qiymati yaml'da bo'lmasin"
    assert "generateValue" not in text, "Render generateValue base64 beradi - Telegram secret_token'ga mos emas"
    assert read(".python-version").strip() == "3.12.10"
    assert "-r bot/requirements.txt" in read("requirements.txt")
    reqs = read("bot/requirements.txt")
    assert re.search(r"^asyncpg==\d", reqs, re.M) and re.search(r"^aiohttp==\d", reqs, re.M)


def test_keep_alive_workflow():
    text = read(".github/workflows/keep-alive.yml")
    assert 'cron: "*/5 * * * *"' in text and "workflow_dispatch:" in text and "vars.RENDER_HEALTH_URL" in text
    for flag in ("--fail", "--retry 3", "--retry-all-errors", "--max-time 60"):
        assert flag in text, flag
    assert "exit 1" in text, "manzil berilmasa ham workflow qizil bo'lsin"
    assert "onrender.com/" not in text.split("jobs:", 1)[1], "manzil qattiq yozilmasin"
    assert "permissions: {}" in text


def test_env_example_and_gitignore():
    example = read("bot/.env.example")
    assert "BOT_TOKEN=your_token_here" in example
    for key in ("DATABASE_URL=", "WEBHOOK_URL=", "WEBHOOK_SECRET=", "ADMIN_ID="):
        assert key in example, key
    # Server qatorlari izohda: nusxa olingan .env lokal botni Postgres/webhook'ga o'tkazib yubormasin
    for key in ("DATABASE_URL", "WEBHOOK_URL", "WEBHOOK_SECRET"):
        assert not re.search(rf"^{key}=", example, re.M), key
    ignore = read(".gitignore").splitlines()
    for line in (".env", "*.db", "venv/", "__pycache__/", "originals/", "deploy/*.zip"):
        assert line in ignore, line


def test_no_real_tokens_or_passwords_in_repo_files():
    token = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
    dsn_pw = re.compile(r"postgres(?:ql)?://[^:/@\s]+:([^@\s]+)@")
    allowed_pw = {"password", "p", "S3cretPass", "TopSecret", "P4ss.w0rd", "Pw0rd", "***", "PAROL"}
    skip_dirs = {"venv", ".venv", "__pycache__", "node_modules", ".wrangler", "originals", ".git"}
    bad = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip_dirs & set(path.parts):
            continue
        if path.name == ".env" or path.suffix not in {".py", ".md", ".yml", ".yaml", ".txt", ".example", ".js",
                                                      ".html", ".json", ".ps1", ".toml", ".cfg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.name != "test_server.py" and token.search(text):
            bad.append(f"{path.relative_to(ROOT)}: token")
        for m in dsn_pw.finditer(text):
            if m.group(1) not in allowed_pw and not m.group(1).startswith("<"):
                bad.append(f"{path.relative_to(ROOT)}: DSN paroli")
    assert not bad, bad


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
