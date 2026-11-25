"""Postgres testlari uchun umumiy yordamchi (o'zi test emas).

TEST_DATABASE_URL faqat bot/.env.test faylidan o'qiladi (fayl git'ga tushmaydi: .gitignore -> .env.*).
Fayl yo'q yoki qator bo'sh bo'lsa - Postgres testlari o'tkazib yuboriladi (SKIP). Fayl namunasi:

    # bot/.env.test - FAQAT TEST bazasi (chicken24-test), ishchi baza emas!
    TEST_DATABASE_URL=postgresql://postgres.<ref>:<parol>@aws-1-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=verify-full&sslrootcert=certs/supabase-ca.crt

Xavfsizlik:
- har test o'z vaqtinchalik sxemasida (c24test_<UTC vaqt>_<tasodifiy>) ishlaydi va oxirida u sxema o'chiriladi;
  xato bo'lsa ham (yangi ulanish bilan, DROP ... IF EXISTS), qisman yaratilganlari ham;
- jarayon o'ldirilsa (finally ishlamaydi) - keyingi run_tests() 2 soatdan eski c24test_ sxemalarni o'chiradi
  (nomidagi vaqt bo'yicha; yangi sxemalarga - parallel ishlayotgan boshqa testlarga - tegilmaydi);
- search_path har ulanishda o'rnatiladi va TEKSHIRILADI (db.apply_search_path) - o'rnatilmasa, hech narsa
  yozilmasdan xato; public sxemaga tegilmaydi;
- run_tests() testlardan oldin va keyin public sxema tarkibini solishtiradi.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

from db import dsn_problem, pg_connect  # noqa: E402

ENV_FILE = BOT_DIR / ".env.test"
SCHEMA_PREFIX = "c24test_"
STALE_AFTER = timedelta(hours=2)
_SCHEMA_RE = re.compile(r"^c24test_(\d{12})_[0-9a-f]{8}$")


def load_test_dsn(path: Path = ENV_FILE) -> str:
    """bot/.env.test dagi TEST_DATABASE_URL (KEY=VALUE qatorlari, # izoh). Bo'lmasa - bo'sh satr."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TEST_DATABASE_URL":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value.strip()
    return ""


DSN = load_test_dsn()

SKIP_TEXT = "SKIP: bot/.env.test da TEST_DATABASE_URL yo'q - Postgres testlari o'tkazib yuborildi"


# ================================================================ vaqtinchalik sxemalar

def new_schema_name(now: datetime | None = None) -> str:
    """c24test_<YYYYmmddHHMM UTC>_<8 hex>: nomidan yaratilgan vaqtni bilib, eski qoldiqlarni tozalash uchun."""
    now = now or datetime.now(timezone.utc)
    name = f"{SCHEMA_PREFIX}{now:%Y%m%d%H%M}_{secrets.token_hex(4)}"
    assert _SCHEMA_RE.match(name) and name.isidentifier()
    return name


def schema_created_at(name: str) -> datetime | None:
    """Sxema nomidagi vaqt (UTC) yoki None (boshqa ko'rinishdagi nom - tozalashda tegilmaydi)."""
    m = _SCHEMA_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def _checked(names: list[str]) -> list[str]:
    for name in names:  # nom SQL'ga qo'yiladi - faqat o'zimiz yaratgan ko'rinishdagi nomlar
        if not _SCHEMA_RE.match(name):
            raise ValueError(f"vaqtinchalik sxema nomi emas: {name!r}")
    return names


async def create_schemas(names: list[str]) -> None:
    con = await pg_connect(DSN)
    try:
        for name in _checked(names):
            await con.execute(f'CREATE SCHEMA "{name}"')
    finally:
        await con.close()


async def drop_schemas(names: list[str]) -> None:
    """Yangi (qisqa) ulanish bilan: test davomida eski ulanish uzilgan bo'lsa ham ishlaydi."""
    con = await pg_connect(DSN)
    try:
        for name in _checked(names):
            await con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    finally:
        await con.close()


def _warn_not_dropped(names: list[str], exc: BaseException) -> None:
    print(f"OGOHLANTIRISH: {len(names)} ta vaqtinchalik sxema o'chirilmadi ({type(exc).__name__}) - "
          f"keyingi ishga tushirishda (2 soatdan keyin) o'chiriladi", file=sys.stderr)


@asynccontextmanager
async def pg_schema() -> AsyncIterator[tuple[str, dict[str, str]]]:
    """Vaqtinchalik sxema: (nomi, Database/pg_connect uchun server_settings); oxirida - xato bo'lsa ham - o'chiriladi."""
    names = [new_schema_name()]
    try:
        await create_schemas(names)
        yield names[0], {"search_path": names[0]}
    except BaseException:
        try:
            await drop_schemas(names)
        except Exception as drop_exc:  # noqa: BLE001 - asl xato yashirilmasin
            _warn_not_dropped(names, drop_exc)
        raise
    await drop_schemas(names)  # xatosiz tugagan test: o'chirilmasa - bu ham xato


@contextmanager
def temp_schemas(count: int) -> Iterator[list[str]]:
    """Bir nechta vaqtinchalik sxema, sinxron (main() har bosqichda o'z event loop'ini ochadi)."""
    names = [new_schema_name() for _ in range(count)]
    try:
        asyncio.run(create_schemas(names))  # qisman yaratilsa ham pastdagi tozalash ishlaydi
        yield names
    except BaseException:
        try:
            asyncio.run(drop_schemas(names))
        except Exception as drop_exc:  # noqa: BLE001
            _warn_not_dropped(names, drop_exc)
        raise
    asyncio.run(drop_schemas(names))


async def existing_test_schemas() -> list[str]:
    con = await pg_connect(DSN)
    try:
        rows = await con.fetch("SELECT nspname FROM pg_namespace WHERE starts_with(nspname, $1) ORDER BY 1",
                               SCHEMA_PREFIX)
    finally:
        await con.close()
    return [r["nspname"] for r in rows]


async def schema_tables(schema: str) -> list[str]:
    con = await pg_connect(DSN)
    try:
        rows = await con.fetch("SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY 1", schema)
    finally:
        await con.close()
    return [r["tablename"] for r in rows]


async def _public_fingerprint(con) -> list[tuple]:
    rows = await con.fetch(
        "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' ORDER BY 1, 2")
    return [tuple(r.values()) for r in rows]


async def public_fingerprint() -> list[tuple]:
    """public sxemadagi obyektlar (nom, tur) - testlar unga tegmaganini tekshirish uchun."""
    con = await pg_connect(DSN)
    try:
        return await _public_fingerprint(con)
    finally:
        await con.close()


async def prepare_run(now: datetime | None = None) -> tuple[list[tuple], int]:
    """Bitta ulanish: public holati + 2 soatdan eski (o'ldirilgan jarayondan qolgan) c24test_ sxemalarni o'chirish."""
    now = now or datetime.now(timezone.utc)
    con = await pg_connect(DSN)
    try:
        names = [r["nspname"] for r in await con.fetch(
            "SELECT nspname FROM pg_namespace WHERE starts_with(nspname, $1)", SCHEMA_PREFIX)]
        stale = [n for n in names if (t := schema_created_at(n)) is not None and now - t > STALE_AFTER]
        for name in stale:
            await con.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        return await _public_fingerprint(con), len(stale)
    finally:
        await con.close()


def run_tests(tests: list[tuple[str, Callable[[], None]]], *, needs_db: Callable[[str], bool]) -> int:
    """Testlarni bajaradi; Postgres testlari faqat DSN bo'lsa. Qaytadi: chiqish kodi."""
    db_tests = [(n, f) for n, f in tests if needs_db(n)]
    problem = dsn_problem(DSN) if DSN else None
    if problem:
        print(f"FAIL  bot/.env.test: TEST_DATABASE_URL {problem}")
        print(f"\n0/{len(tests)} test o'tdi")
        return 1
    if not DSN:
        print(SKIP_TEXT)
        tests = [(n, f) for n, f in tests if not needs_db(n)]
    before = None
    if DSN:
        before, stale = asyncio.run(prepare_run())
        if stale:
            print(f"Eski vaqtinchalik sxemalar o'chirildi: {stale}")
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK    {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
    if DSN and asyncio.run(public_fingerprint()) != before:
        failed += 1
        print("FAIL  public sxema o'zgardi - testlar unga tegmasligi kerak edi")
    skipped = 0 if DSN else len(db_tests)
    print(f"\n{len(tests) - failed}/{len(tests)} test o'tdi" + (f" ({skipped} SKIP)" if skipped else ""))
    return 1 if failed else 0
