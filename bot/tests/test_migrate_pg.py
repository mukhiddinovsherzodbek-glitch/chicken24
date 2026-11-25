"""tools/migrate_pg_to_pg.py testlari (Postgres -> Postgres ko'chirish).

- Bazasiz testlar (har doim): manzil faqat env'dan, argument sifatida rad va qaytarib yozilmaydi, xato matnida
  login/parol/xost yo'q, bitta baza rad, sslrootcert (Supabase CA), bot/.env.test o'qilishi.
- Postgres testlari (test_pg_*; bot/.env.test da TEST_DATABASE_URL bo'lsa): ikki vaqtinchalik sxema (manba va
  maqsad) - dry-run hech narsa yozmaydi, KOCHIR siz yozilmaydi, to'liq ko'chirish + ID davomi + RLS, to'la
  maqsad rad, tekshiruv farqida ROLLBACK, manba faqat READ ONLY tranzaksiyada o'qiladi.
"""
from __future__ import annotations

import asyncio
import io
import ssl
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import business  # noqa: E402
import migrate_pg_to_pg as mig  # noqa: E402
import pgtest  # noqa: E402
from db import TABLES, Database, DatabaseError, pg_connect_params, split_server_settings  # noqa: E402
from handlers import on_web_app_data  # noqa: E402
from test_flow import NO_LOC, Env, FakeBot, good, make_message  # noqa: E402

business.is_open_now = lambda: True

DSN = pgtest.DSN
BIG_USER = 7_123_456_789
FAKE_SRC = "postgresql://u_secret:S3cretPass@127.0.0.1:1/srcdb"
FAKE_DST = "postgresql://u_secret:S3cretPass@127.0.0.1:2/dstdb"


def run(coro):
    return asyncio.run(coro)


def call(argv, environ, *, answer=None, **kw):
    """main() ni chaqiradi. answer: str - tasdiq javobi; EOFError - kirish yo'q; None - so'ralmasligi shart."""
    out, err, prompts = io.StringIO(), io.StringIO(), []

    def input_fn(prompt):
        prompts.append((prompt, out.getvalue()))
        if answer is None:
            raise AssertionError("tasdiq so'ralmasligi kerak edi")
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer()
        return answer(prompt) if callable(answer) else answer

    code = mig.main(argv, environ=environ, input_fn=input_fn, out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue(), prompts


# ================================================================ bazasiz testlar

def test_dry_run_is_default_and_modes_exclusive():
    assert mig.parse_args([]).apply is False
    assert mig.parse_args(["--dry-run"]).apply is False and mig.parse_args(["--apply"]).apply is True
    code, _, err, _ = call(["--dry-run", "--apply"], {})
    assert code == mig.EXIT_CONFIG and "--apply" in err


def test_url_argument_rejected_and_not_echoed():
    code, out, err, _ = call([FAKE_SRC], {})
    assert code == mig.EXIT_CONFIG
    text = out + err
    for secret in ("S3cretPass", "u_secret", "127.0.0.1", "srcdb", "postgresql://"):
        assert secret not in text, secret
    assert mig.SOURCE_ENV in err and mig.TARGET_ENV in err


def test_env_variables_required():
    code, _, err, _ = call([], {})
    assert code == mig.EXIT_CONFIG and mig.SOURCE_ENV in err and mig.TARGET_ENV in err
    code, _, err, _ = call([], {mig.SOURCE_ENV: FAKE_SRC})
    assert code == mig.EXIT_CONFIG and mig.TARGET_ENV in err and "S3cretPass" not in err
    code, _, err, _ = call([], {mig.SOURCE_ENV: "mysql://x", mig.TARGET_ENV: FAKE_DST})
    assert code == mig.EXIT_CONFIG and "postgresql://" in err
    # Supabase namunasidagi [ ] qavslar qolib ketgan - aniq xabar, parol va xost chiqmaydi
    bracketed = "postgresql://postgres.ref:" + "[Pw0rd]" + "@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
    code, out, err, _ = call([], {mig.SOURCE_ENV: FAKE_SRC, mig.TARGET_ENV: bracketed})
    assert code == mig.EXIT_CONFIG and mig.TARGET_ENV in err and "[ ]" in err, err
    for secret in ("Pw0rd", "pooler.supabase.com", "S3cretPass"):
        assert secret not in out + err, secret


def test_same_database_refused():
    code, _, err, _ = call(["--apply"], {mig.SOURCE_ENV: FAKE_SRC, mig.TARGET_ENV: FAKE_SRC + "?sslmode=require"})
    assert code == mig.EXIT_CONFIG and "bitta bazani" in err and "S3cretPass" not in err
    # Supabase pooler: bir xost, bir baza nomi, lekin boshqa loyiha (foydalanuvchi postgres.<ref>) - boshqa baza
    a = "postgresql://postgres.aaaa:p@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    b = "postgresql://postgres.bbbb:p@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    assert mig.db_identity(a) != mig.db_identity(b)
    assert mig.db_identity(a) == mig.db_identity(a.replace(":5432", ""))


def test_provider_labels_hide_host():
    assert mig.provider("postgresql://u:p@ep-cool-1.eu-central-1.aws.neon.tech/neondb") == "neon.tech"
    assert mig.provider("postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb") == "neon.tech"
    assert mig.provider("postgresql://postgres.ref:p@aws-0-eu-central-1.pooler.supabase.com:5432/postgres") \
        == "supabase.com"
    assert mig.provider("postgresql://u:p@db.abcdef.supabase.co:5432/postgres") == "supabase.com"
    assert mig.provider("postgresql://u:p@localhost/x") == "localhost"
    assert mig.provider("postgresql://u:p@evilneon.tech/x") == "boshqa server"


def test_connection_error_shows_no_address():
    code, out, err, _ = call(["--dry-run"], {mig.SOURCE_ENV: FAKE_SRC, mig.TARGET_ENV: FAKE_DST})
    assert code == mig.EXIT_DB and "Ulanib bo'lmadi" in err, err
    for secret in ("S3cretPass", "u_secret", "srcdb", "dstdb", "127.0.0.1"):
        assert secret not in out + err, secret


def test_redact_removes_address_parts():
    dsn = "postgresql://postgres.myref:Pw0rd@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    text = ('password authentication failed for user "postgres.myref" at '
            "aws-0-eu-central-1.pooler.supabase.com (Pw0rd) postgresql://postgres.myref:Pw0rd@x")
    clean = mig.redact(text, (dsn,))
    for secret in ("myref", "Pw0rd", "pooler.supabase.com"):
        assert secret not in clean, (secret, clean)


def test_sslrootcert_for_supabase_ca():
    import certifi

    ca = certifi.where()  # haqiqiy PEM fayl - Supabase CA o'rnida
    clean, ctx = pg_connect_params(f"postgresql://u:p@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
                                   f"?sslmode=verify-full&sslrootcert={ca}")
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert "sslrootcert" not in clean and "sslmode" not in clean
    # sslrootcert'siz - tizim CA'lari bilan, baribir tekshiriladi
    _, ctx = pg_connect_params("postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    # lokal server: SSL'siz, lekin sslrootcert berilsa - SSL bilan
    assert pg_connect_params("postgresql://u:p@localhost/x")[1] is False
    assert isinstance(pg_connect_params(f"postgresql://u:p@localhost/x?sslrootcert={ca}")[1], ssl.SSLContext)
    assert pg_connect_params(f"postgresql://u:p@h/x?sslmode=disable&sslrootcert={ca}")[1] is False
    # fayl yo'q - aniq xato (nisbiy yo'l bot/ papkasidan), papka yo'li va parol chiqmaydi
    try:
        pg_connect_params("postgresql://u:Pw0rd@h/x?sslrootcert=certs/yoq-fayl.crt")
    except DatabaseError as exc:
        assert "yoq-fayl.crt" in str(exc) and "Pw0rd" not in str(exc) and "certs" not in str(exc)
    else:
        raise AssertionError("DatabaseError kutilgan edi")


def test_search_path_not_sent_in_startup_packet():
    assert split_server_settings({"search_path": "c24test_x", "application_name": "a"}) == \
        ({"application_name": "a"}, "c24test_x")
    assert split_server_settings(None) == ({}, None)


def test_database_pool_sets_search_path_on_every_acquire():
    """search_path (faqat testlar) pool'da har acquire'da o'rnatiladi; ishchi botda setup yo'q."""
    import asyncpg

    captured: dict = {}

    async def fake_create_pool(dsn, **kw):
        captured.clear()
        captured.update(kw)
        raise RuntimeError("to'xta")

    original = asyncpg.create_pool
    asyncpg.create_pool = fake_create_pool
    try:
        for server_settings in ({"search_path": "c24test_x"}, None):
            try:
                run(Database(dsn="postgresql://u:p@localhost/x", server_settings=server_settings).connect())
            except RuntimeError:
                pass
            if server_settings:
                assert captured["setup"] is not None and "search_path" not in captured["server_settings"]
            else:
                assert captured["setup"] is None
                assert captured["server_settings"] == {"application_name": "chicken24-bot"}
    finally:
        asyncpg.create_pool = original


def test_search_path_guard_stops_before_writes():
    from db import apply_search_path

    class FakeCon:
        def __init__(self, current):
            self.current, self.sql = current, []

        async def execute(self, sql, *args):
            self.sql.append(sql)

        async def fetchval(self, sql, *args):
            return self.current

    ok = FakeCon("c24test_x")
    run(apply_search_path(ok, "c24test_x"))
    assert "set_config" in ok.sql[0]
    bad = FakeCon("public")  # pooler search_path'ni o'tkazmadi
    try:
        run(apply_search_path(bad, "c24test_x"))
    except DatabaseError as exc:
        assert "public" in str(exc)
    else:
        raise AssertionError("DatabaseError kutilgan edi")
    run(apply_search_path(FakeCon(None), None))  # search_path yo'q - hech narsa qilinmaydi


def test_env_test_file_loader():
    tmp = Path(tempfile.mkdtemp()) / ".env.test"
    tmp.write_text("﻿# izoh\n\nOTHER=1\nTEST_DATABASE_URL = 'postgresql://u:p@h/db?a=1&b=2'\n", encoding="utf-8")
    assert pgtest.load_test_dsn(tmp) == "postgresql://u:p@h/db?a=1&b=2"
    tmp.write_text("TEST_DATABASE_URL=\n", encoding="utf-8")
    assert pgtest.load_test_dsn(tmp) == ""
    assert pgtest.load_test_dsn(tmp.parent / "yoq") == ""
    assert pgtest.ENV_FILE == BOT_DIR / ".env.test"
    from db import dsn_problem

    assert dsn_problem("postgresql://u:" + "[p]" + "@h.example.com:5432/db")
    assert dsn_problem("postgresql://u:p@h:99999/db")
    assert dsn_problem("postgresql://u:p@aws-1-eu-central-1.pooler.supabase.com:5432/postgres") is None
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.*" in ignore and "!.env.test" not in ignore, ".env.test git'ga tushmasin"


def test_temp_schema_names_carry_creation_time():
    now = datetime(2026, 11, 26, 3, 7, tzinfo=timezone.utc)
    name = pgtest.new_schema_name(now)
    assert name.startswith("c24test_202611260307_") and name.isidentifier()
    assert pgtest.schema_created_at(name) == now
    assert pgtest.new_schema_name(now) != name, "tasodifiy qism"
    # boshqa ko'rinishdagi nomlar (eski format, begona sxema) - eski qoldiq deb o'chirilmaydi
    for other in ("c24test_ab12cd34ef56", "public", "c24test_202611260307_xyz", "c24test_2026112603_ab12cd34"):
        assert pgtest.schema_created_at(other) is None, other
    try:
        pgtest._checked(['c24test_x"; DROP SCHEMA public; --'])
    except ValueError:
        pass
    else:
        raise AssertionError("noto'g'ri nom SQL'ga tushmasligi kerak")


# ================================================================ Postgres testlari

temp_schemas = pgtest.temp_schemas


def test_pg_temp_schemas_dropped_even_on_error():
    """Test xato bilan tugasa ham uning vaqtinchalik sxemalari o'chiriladi (sinxron va async yordamchi)."""
    seen: list[str] = []
    try:
        with temp_schemas(2) as names:
            seen += names
            assert set(names) <= set(run(pgtest.existing_test_schemas())), "sxemalar yaratilgan"
            raise RuntimeError("sinov xatosi")
    except RuntimeError:
        pass

    async def async_variant():
        try:
            async with pgtest.pg_schema() as (schema, _):
                seen.append(schema)
                raise RuntimeError("sinov xatosi")
        except RuntimeError:
            pass

    run(async_variant())
    left = set(run(pgtest.existing_test_schemas())) & set(seen)
    assert len(seen) == 3 and not left, f"xatodan keyin qolgan sxemalar: {sorted(left)}"


def test_pg_stale_schemas_cleaned_but_fresh_kept():
    """O'ldirilgan jarayondan qolgan (2 soatdan eski) sxema keyingi ishga tushishda o'chiriladi, yangisi qoladi."""
    old = pgtest.new_schema_name(datetime.now(timezone.utc) - timedelta(hours=3))
    fresh = pgtest.new_schema_name()
    run(pgtest.create_schemas([old, fresh]))
    try:
        _, dropped = run(pgtest.prepare_run())
        existing = set(run(pgtest.existing_test_schemas()))
        assert dropped >= 1 and old not in existing and fresh in existing
    finally:
        run(pgtest.drop_schemas([old, fresh]))


def settings(schema: str) -> dict:
    return {"search_path": schema}


def environ() -> dict:
    # Testda manba va maqsad - bitta TEST bazasidagi ikki sxema (search_path bilan ajratiladi)
    return {mig.SOURCE_ENV: DSN, mig.TARGET_ENV: DSN}


@contextmanager
def allow_same_database():
    """Bitta-baza tekshiruvi (db_identity) testda vaqtincha o'chiriladi: sxemalar har xil."""
    original = mig.db_identity
    calls = iter(range(10_000))
    mig.db_identity = lambda dsn: (original(dsn), next(calls))
    try:
        yield
    finally:
        mig.db_identity = original


async def fill_source(schema: str) -> dict:
    env = Env()
    env.db = Database(dsn=DSN, server_settings=settings(schema))
    await env.db.connect()
    now = datetime.now(timezone.utc)
    await env.db.set_user_lang(BIG_USER, "ru", now)
    await env.db.save_verified_phone(BIG_USER, "+998901234567", now)
    await on_web_app_data(make_message(BIG_USER, good())[0], **env.deps(FakeBot()), lang="ru")
    await on_web_app_data(make_message(2, good(ref="b2"))[0], **env.deps(FakeBot()), lang="uz")
    env.limiter.release(2)
    await on_web_app_data(make_message(2, good(ref="rejref02", **NO_LOC))[0], **env.deps(FakeBot()))
    await env.db.cancel_order(2, 2, datetime.now(timezone.utc), 300)
    await env.db.set_meta("backup_last_sig", "2:2:1")
    tables = await env.db.export_tables()
    await env.db.close()
    return tables


async def export(schema: str) -> dict:
    db = Database(dsn=DSN, server_settings=settings(schema))
    await db.connect()
    try:
        return await db.export_tables()
    finally:
        await db.close()


def test_pg_dry_run_writes_nothing():
    with temp_schemas(2) as (src, dst), allow_same_database():
        run(fill_source(src))
        code, out, err, prompts = call([], environ(), source_settings=settings(src), target_settings=settings(dst))
        assert code == mig.EXIT_OK, err
        assert "Yo'nalish:" in out and "dry-run" in out and prompts == []
        assert run(pgtest.schema_tables(dst)) == [], "dry-run maqsadga hech narsa yozmaydi"


def test_pg_apply_requires_kochir():
    with temp_schemas(2) as (src, dst), allow_same_database():
        run(fill_source(src))
        for answer in ("kochir", "", " KOCHIR!", EOFError):
            code, out, err, prompts = call(["--apply"], environ(), answer=answer,
                                           source_settings=settings(src), target_settings=settings(dst))
            assert code == mig.EXIT_REFUSED and "Bekor qilindi" in out, (answer, out, err)
            # tasdiq so'ralishidan OLDIN yo'nalish va sonlar ko'rsatilgan
            shown = prompts[0][1]
            assert "Yo'nalish:" in shown and "orders" in shown and "orders.total" in shown
            assert "KOCHIR" in prompts[0][0]
            assert run(pgtest.schema_tables(dst)) == [], "tasdiqsiz hech narsa yozilmaydi"


def test_pg_apply_copies_everything_and_ids_continue():
    recorded = []
    original_connect = mig.pg_connect

    class Spy:
        def __init__(self, con):
            self._con = con

        def transaction(self, **kw):
            recorded.append(kw)
            return self._con.transaction(**kw)

        def __getattr__(self, name):
            return getattr(self._con, name)

    async def spy_connect(dsn, server_settings=None):
        return Spy(await original_connect(dsn, server_settings))

    with temp_schemas(2) as (src, dst), allow_same_database():
        source_before = run(fill_source(src))
        mig.pg_connect = spy_connect
        try:
            code, out, err, _ = call(["--apply"], environ(), answer="KOCHIR",
                                     source_settings=settings(src), target_settings=settings(dst))
        finally:
            mig.pg_connect = original_connect
        assert code == mig.EXIT_OK, (out, err)
        assert "Tayyor" in out and "FARQ" not in out and "DIQQAT" not in out
        assert recorded and all(kw.get("readonly") is True for kw in recorded), "manba/maqsad o'qish - READ ONLY"
        assert any(kw.get("isolation") == "repeatable_read" for kw in recorded), "manba - bitta snapshot"
        assert run(export(dst)) == source_before, "hamma jadval aynan ko'chdi"
        assert run(export(src)) == source_before, "manba o'zgarmadi"

        async def after():
            env = Env()
            env.db = Database(dsn=DSN, server_settings=settings(dst))
            await env.db.connect()
            await on_web_app_data(make_message(5, good(ref="new5"))[0], **env.deps(FakeBot()), lang="uz")
            order = await env.db.get_order(3)
            async with env.db.pg_connection() as con:
                item_ids = [r["id"] for r in await con.fetch("SELECT id FROM order_items ORDER BY id")]
                rls = await con.fetch("SELECT relname, relrowsecurity FROM pg_class "
                                      "WHERE relnamespace = $1::text::regnamespace AND relkind = 'r'", dst)
                total = await con.fetchval("SELECT SUM(total) FROM orders WHERE id <= 2")
            await env.db.close()
            return order, item_ids, rls, total

        order, item_ids, rls, total = run(after())
        assert order is not None and order["user_id"] == 5, "yangi buyurtma ID si MAX(id)+1 dan davom etadi"
        assert item_ids == sorted(set(item_ids)) and len(item_ids) == len(source_before["order_items"]) + 2
        assert {r["relname"] for r in rls} == set(TABLES) and all(r["relrowsecurity"] for r in rls)
        orders_total = sum(r[TABLES["orders"].index("total")] for r in source_before["orders"])
        assert total == orders_total and f"manba {orders_total}, maqsad {orders_total}" in out
        # chiqishda manzil bo'laklari yo'q
        parts = urlsplit(DSN)
        for secret in (parts.password, parts.username, parts.hostname):
            if secret and len(secret) >= 3:
                assert secret not in out + err, "manzil bo'lagi chiqishda"

        # maqsad endi bo'sh emas - tasdiq so'ralmasdan rad, hech narsa o'zgarmaydi
        dst_before = run(export(dst))
        code, out, err, _ = call(["--apply"], environ(), answer=None,
                                 source_settings=settings(src), target_settings=settings(dst))
        assert code == mig.EXIT_REFUSED and "bo'sh emas" in err
        assert run(export(dst)) == dst_before


def test_pg_verification_mismatch_rolls_back():
    with temp_schemas(2) as (src, dst):
        run(fill_source(src))
        snap = run(mig.read_source(DSN, settings(src)))
        bad = mig.Snapshot(snap.data, snap.total_sum + 1)
        try:
            run(mig.apply(DSN, bad, settings(dst)))
        except mig.VerificationError as exc:
            message = str(exc)
        else:
            raise AssertionError("VerificationError kutilgan edi")
        assert "orders.total" in message and "+998" not in message
        tables = run(export(dst))
        assert all(rows == [] for rows in tables.values()), "ROLLBACK: maqsadda ma'lumot qolmadi"


def test_pg_source_without_tables_refused_and_untouched():
    with temp_schemas(2) as (src, dst), allow_same_database():
        code, out, err, _ = call(["--dry-run"], environ(), source_settings=settings(src),
                                 target_settings=settings(dst))
        assert code == mig.EXIT_REFUSED and "manbada jadval" in err
        assert run(pgtest.schema_tables(src)) == [], "manbada jadval yaratilmadi"


def test_pg_warns_if_source_changes_during_copy():
    with temp_schemas(2) as (src, dst), allow_same_database():
        run(fill_source(src))

        async def touch_source():
            db = Database(dsn=DSN, server_settings=settings(src))
            await db.connect()
            await db.set_meta("yangi", "1")
            await db.close()

        def answer(_prompt):
            run(touch_source())  # tasdiq kutilayotganda bot manbaga yozdi (Suspend qilinmagan)
            return "KOCHIR"

        code, out, err, _ = call(["--apply"], environ(), answer=answer,
                                 source_settings=settings(src), target_settings=settings(dst))
        assert code == mig.EXIT_OK and "DIQQAT" in out, (out, err)


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    sys.exit(pgtest.run_tests(tests, needs_db=lambda name: name.startswith("test_pg_")))
