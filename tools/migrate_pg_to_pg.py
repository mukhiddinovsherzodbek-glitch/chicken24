"""PostgreSQL -> PostgreSQL bir martalik ko'chirish (masalan Neon -> Supabase).

Ishga tushirish (loyiha papkasida, bot venv'i bilan). Manzillar FAQAT muhit o'zgaruvchilarida:
    SOURCE_DATABASE_URL - qayerdan (masalan Neon)      - faqat O'QILADI
    TARGET_DATABASE_URL - qayerga  (masalan Supabase)  - bo'sh baza bo'lishi shart
Yo'riqnoma (qiymatni yashirin kiritish bilan): docs/kurer/MIGRATSIYA.md

    bot\\venv\\Scripts\\python.exe tools\\migrate_pg_to_pg.py            # = --dry-run: faqat sonlar
    bot\\venv\\Scripts\\python.exe tools\\migrate_pg_to_pg.py --apply    # ko'chirish (KOCHIR tasdig'i bilan)

- Manba bitta READ ONLY + REPEATABLE READ tranzaksiyada o'qiladi (izchil holat, manbaga hech narsa yozilmaydi;
  Database.connect() manbada ishlatilmaydi - u jadval yaratib/o'zgartirib qo'yardi).
- --dry-run (standart): manba va maqsaddagi qator sonlari, orders.total yig'indisi. Hech narsa yozilmaydi.
- --apply: yo'nalish va sonlarni ko'rsatadi, maqsad bo'sh bo'lmasa rad etadi, KOCHIR deb yozilgandan keyingina:
  maqsadda sxema bot/db.py orqali yaratiladi (RLS bilan), db.TABLES tartibida BITTA tranzaksiyada ko'chiriladi,
  identity hisoblagichlari MAX(id) dan davom etadi, qator sonlari va orders.total yig'indisi commit'dan OLDIN
  solishtiriladi - farq bo'lsa hammasi bekor (ROLLBACK).
- Ekranga faqat sonlar va provayder nomi (neon.tech, supabase.com) chiqadi: login, parol, xost, shaxsiy ma'lumot yo'q.

Chiqish kodlari: 0 - tayyor; 1 - sozlama xatosi; 2 - rad etildi / bekor qilindi; 3 - baza xatosi; 4 - tekshiruv farqi.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TextIO
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))

from db import (  # noqa: E402
    IDENTITY_TABLES,
    PRIMARY_KEYS,
    TABLES,
    Database,
    DatabaseError,
    clean_pg_error,
    dsn_problem,
    is_postgres_url,
    pg_connect,
)
from logsafe import scrub  # noqa: E402

SOURCE_ENV = "SOURCE_DATABASE_URL"
TARGET_ENV = "TARGET_DATABASE_URL"
CONFIRM_WORD = "KOCHIR"

EXIT_OK, EXIT_CONFIG, EXIT_REFUSED, EXIT_DB, EXIT_MISMATCH = 0, 1, 2, 3, 4


class ConfigProblem(Exception):
    """Muhit o'zgaruvchilari yoki argumentlar noto'g'ri (manzil xabarga qo'shilmaydi)."""


class Refused(Exception):
    """Ko'chirish xavfsiz emas (maqsad bo'sh emas, manba sxemasi to'liq emas) - hech narsa yozilmadi."""


class VerificationError(Exception):
    """Ko'chirilgandan keyingi sonlar manbadagidan farq qildi - tranzaksiya bekor qilindi."""


@dataclass(frozen=True)
class Snapshot:
    data: dict[str, list[tuple]]
    total_sum: int

    @property
    def counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.data.items()}


# ================================================================ manzillar (faqat env)

def provider(dsn: str) -> str:
    """Ekranda ko'rsatish uchun provayder nomi: xost to'liq ko'rsatilmaydi."""
    host = (urlsplit(dsn).hostname or "localhost").lower()
    for suffix in ("neon.tech", "supabase.com", "supabase.co"):
        if host == suffix or host.endswith("." + suffix):
            return "supabase.com" if suffix.startswith("supabase") else suffix
    if host in ("localhost", "127.0.0.1", "::1"):
        return "localhost"
    return "boshqa server"


def db_identity(dsn: str) -> tuple[str, int, str, str]:
    """(xost, port, baza, foydalanuvchi). Supabase pooler'da bir xost ko'p loyihaga xizmat qiladi -
    loyiha foydalanuvchi nomida (postgres.<ref>), shuning uchun u ham solishtiriladi."""
    parts = urlsplit(dsn)
    return ((parts.hostname or "localhost").lower(), parts.port or 5432,
            unquote(parts.path.lstrip("/")), unquote(parts.username or ""))


def read_dsns(environ: Mapping[str, str]) -> tuple[str, str]:
    source = environ.get(SOURCE_ENV, "").strip()
    target = environ.get(TARGET_ENV, "").strip()
    missing = [name for name, value in ((SOURCE_ENV, source), (TARGET_ENV, target)) if not value]
    if missing:
        raise ConfigProblem(f"Muhit o'zgaruvchisi berilmagan: {', '.join(missing)} (docs/kurer/MIGRATSIYA.md)")
    for name, value in ((SOURCE_ENV, source), (TARGET_ENV, target)):
        if not is_postgres_url(value):
            raise ConfigProblem(f"{name} postgresql:// bilan boshlanishi kerak")
        problem = dsn_problem(value)
        if problem:
            raise ConfigProblem(f"{name}: {problem}")
    if db_identity(source) == db_identity(target):
        raise ConfigProblem(f"{SOURCE_ENV} va {TARGET_ENV} bitta bazani ko'rsatyapti - ko'chirish ma'nosiz")
    return source, target


def redact(text: str, dsns: tuple[str, ...]) -> str:
    """Xato matnidan manzil bo'laklarini (xost, foydalanuvchi, parol, baza nomi) olib tashlaydi."""
    text = scrub(text)
    for dsn in dsns:
        parts = urlsplit(dsn)
        pieces = [parts.hostname, parts.username, parts.password, parts.path.lstrip("/")]
        for piece in sorted({unquote(p) for p in pieces if p}, key=len, reverse=True):
            if len(piece) >= 3:
                text = text.replace(piece, "***")
    return text


# ================================================================ manba (faqat o'qish)

async def read_source(dsn: str, server_settings: dict | None = None) -> Snapshot:
    """Hamma jadval bitta READ ONLY snapshot'dan. Ustun yetishmasa - Refused (manbaga tegilmaydi)."""
    con = await pg_connect(dsn, server_settings)
    try:
        async with con.transaction(isolation="repeatable_read", readonly=True):
            missing = []
            for table, columns in TABLES.items():
                rows = await con.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = $1", table)
                have = {r["column_name"] for r in rows}
                missing += [f"{table}.{c}" for c in columns if c not in have] if have else [table]
            if missing:
                raise Refused("manbada jadval/ustun yo'q: " + ", ".join(missing[:5]))
            data = {}
            for table, columns in TABLES.items():
                # Nomlar faqat db.TABLES doimiy ro'yxatidan
                rows = await con.fetch(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {PRIMARY_KEYS[table]}")
                data[table] = [tuple(r.values()) for r in rows]
            total_sum = await con.fetchval("SELECT COALESCE(SUM(total), 0) FROM orders")
    finally:
        await con.close()
    return Snapshot(data, int(total_sum))


async def source_signature(dsn: str, server_settings: dict | None = None) -> tuple[dict[str, int], int]:
    """Ko'chirishdan keyin manba o'zgarmaganini tekshirish uchun (faqat o'qish)."""
    con = await pg_connect(dsn, server_settings)
    try:
        async with con.transaction(isolation="repeatable_read", readonly=True):
            counts = {t: int(await con.fetchval(f"SELECT COUNT(*) FROM {t}")) for t in TABLES}
            total_sum = int(await con.fetchval("SELECT COALESCE(SUM(total), 0) FROM orders"))
    finally:
        await con.close()
    return counts, total_sum


# ================================================================ maqsad

async def target_status(dsn: str, server_settings: dict | None = None) -> dict[str, int | None]:
    """{jadval: qator soni yoki None (jadval yo'q)} - faqat o'qiydi."""
    con = await pg_connect(dsn, server_settings)
    try:
        async with con.transaction(readonly=True):
            out: dict[str, int | None] = {}
            for table in TABLES:
                exists = await con.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
                out[table] = int(await con.fetchval(f"SELECT COUNT(*) FROM {table}")) if exists else None
    finally:
        await con.close()
    return out


async def apply(dsn: str, snapshot: Snapshot, server_settings: dict | None = None) -> tuple[dict[str, int], int]:
    """Sxema (bot/db.py) + bitta tranzaksiyada ko'chirish + commit'dan oldin tekshiruv."""
    db = Database(dsn=dsn, server_settings=server_settings)
    await db.connect()  # jadvallar, indekslar, RLS - bot bilan bir xil
    try:
        async with db.pg_connection() as con:
            async with con.transaction():
                await con.execute("LOCK TABLE " + ", ".join(TABLES) + " IN ACCESS EXCLUSIVE MODE")
                busy = [t for t in TABLES if await con.fetchval(f"SELECT EXISTS (SELECT 1 FROM {t})")]
                if busy:
                    raise Refused("maqsad bo'sh emas: " + ", ".join(busy))
                for table, columns in TABLES.items():
                    rows = snapshot.data.get(table, [])
                    if rows:
                        placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                        await con.executemany(
                            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows)
                for table in IDENTITY_TABLES:
                    await con.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
                counts = {t: int(await con.fetchval(f"SELECT COUNT(*) FROM {t}")) for t in TABLES}
                total_sum = int(await con.fetchval("SELECT COALESCE(SUM(total), 0) FROM orders"))
                if counts != snapshot.counts or total_sum != snapshot.total_sum:
                    raise VerificationError(_mismatch(snapshot, counts, total_sum))
    finally:
        await db.close()
    return counts, total_sum


def _mismatch(snapshot: Snapshot, counts: dict[str, int], total_sum: int) -> str:
    diffs = [f"{t}: manba {snapshot.counts[t]}, maqsad {counts.get(t)}" for t in TABLES
             if counts.get(t) != snapshot.counts[t]]
    if total_sum != snapshot.total_sum:
        diffs.append(f"orders.total: manba {snapshot.total_sum}, maqsad {total_sum}")
    return "; ".join(diffs)


# ================================================================ chiqish (faqat sonlar)

def _status_text(value: int | None) -> str:
    return "jadval yo'q" if value is None else str(value)


def print_plan(out: TextIO, source: str, target: str, snapshot: Snapshot, status: dict[str, int | None]) -> None:
    print(f"Yo'nalish: {provider(source)} -> {provider(target)}", file=out)
    print(f"{'jadval':<18}{'manba':>8}{'maqsad (hozir)':>16}", file=out)
    for table in TABLES:
        print(f"{table:<18}{snapshot.counts[table]:>8}{_status_text(status[table]):>16}", file=out)
    print(f"orders.total yig'indisi (manba): {snapshot.total_sum}", file=out)


def print_result(out: TextIO, snapshot: Snapshot, counts: dict[str, int], total_sum: int) -> None:
    print(f"{'jadval':<18}{'manba':>8}{'maqsad':>8}", file=out)
    for table in TABLES:
        mark = "OK" if counts[table] == snapshot.counts[table] else "FARQ"
        print(f"{table:<18}{snapshot.counts[table]:>8}{counts[table]:>8}  {mark}", file=out)
    mark = "OK" if total_sum == snapshot.total_sum else "FARQ"
    print(f"orders.total yig'indisi: manba {snapshot.total_sum}, maqsad {total_sum}  {mark}", file=out)


# ================================================================ buyruq qatori

class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: D401 - argparse xatosi argument qiymatini qaytarmasin
        raise ConfigProblem("Noto'g'ri argument. Faqat --dry-run yoki --apply. Manzillar argument emas - "
                            f"{SOURCE_ENV} va {TARGET_ENV} muhit o'zgaruvchilarida bo'lsin.")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(description="PostgreSQL -> PostgreSQL ko'chirish (manzillar faqat muhit o'zgaruvchilarida).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="standart: faqat sonlar, hech narsa yozilmaydi")
    mode.add_argument("--apply", action="store_true", help=f"ko'chirish ({CONFIRM_WORD} tasdig'i bilan)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None,
         input_fn: Callable[[str], str] = input, out: TextIO | None = None, err: TextIO | None = None,
         source_settings: dict | None = None, target_settings: dict | None = None) -> int:
    """source_settings / target_settings - faqat testlar uchun (vaqtinchalik sxema: search_path)."""
    out = out or sys.stdout
    err = err or sys.stderr
    dsns: tuple[str, ...] = ()
    try:
        args = parse_args(argv)
        source, target = read_dsns(os.environ if environ is None else environ)
        dsns = (source, target)
        snapshot = asyncio.run(read_source(source, source_settings))
        status = asyncio.run(target_status(target, target_settings))
        print_plan(out, source, target, snapshot, status)
        busy = [t for t, n in status.items() if n]
        if not args.apply:
            if busy:
                print("Diqqat: maqsad bo'sh emas (" + ", ".join(busy) + ") - --apply rad etiladi.", file=out)
            print("Sinov (dry-run): hech narsa yozilmadi. Ko'chirish uchun: --apply", file=out)
            return EXIT_OK
        if busy:
            raise Refused("maqsad bo'sh emas: " + ", ".join(busy))
        try:
            answer = input_fn(f"Ko'chirish uchun {CONFIRM_WORD} deb yozing (boshqa narsa - bekor): ")
        except EOFError:
            answer = ""
        if answer.strip() != CONFIRM_WORD:
            print("Bekor qilindi. Hech narsa yozilmadi.", file=out)
            return EXIT_REFUSED
        counts, total_sum = asyncio.run(apply(target, snapshot, target_settings))
        print_result(out, snapshot, counts, total_sum)
        after = asyncio.run(source_signature(source, source_settings))
        if after != (snapshot.counts, snapshot.total_sum):
            print("DIQQAT: manba nusxa olingandan keyin o'zgargan (bot to'xtatilmaganmi?). Yangi yozuvlar "
                  "ko'chmagan - MIGRATSIYA.md dagi 'Muammo bo'lsa' bo'limiga qarang.", file=out)
        print("Tayyor: ko'chirildi va tekshirildi.", file=out)
        return EXIT_OK
    except ConfigProblem as exc:
        print(f"Xato: {exc}", file=err)
        return EXIT_CONFIG
    except Refused as exc:
        print(f"RAD ETILDI, hech narsa yozilmadi: {redact(str(exc), dsns)}", file=err)
        return EXIT_REFUSED
    except VerificationError as exc:
        print(f"TEKSHIRUV FARQI - tranzaksiya bekor qilindi, maqsadga ma'lumot yozilmadi: {exc}", file=err)
        return EXIT_MISMATCH
    except DatabaseError as exc:
        print(f"Baza xatosi: {redact(str(exc), dsns)}", file=err)
        return EXIT_DB
    except Exception as exc:  # noqa: BLE001 - tarmoq/SSL: faqat turi (manzil chiqmasin)
        import asyncpg

        if isinstance(exc, asyncpg.PostgresError):
            print(f"Baza xatosi: {redact(str(clean_pg_error(exc)), dsns)}", file=err)
            return EXIT_DB
        hint = " (Supabase uchun manzilga sslrootcert=... kerak - MIGRATSIYA.md)" if "SSL" in type(exc).__name__ \
            or "CERTIFICATE" in str(exc).upper() else ""
        print(f"Ulanib bo'lmadi: {type(exc).__name__}{hint}", file=err)
        return EXIT_DB


if __name__ == "__main__":
    sys.exit(main())
