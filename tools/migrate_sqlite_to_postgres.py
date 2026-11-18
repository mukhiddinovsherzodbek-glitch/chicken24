"""SQLite (bot/chicken24.db) -> PostgreSQL (DATABASE_URL) bir martalik ko'chirish.

Ishga tushirish (loyiha papkasida, bot venv'i bilan):
    bot\\venv\\Scripts\\python.exe tools\\migrate_sqlite_to_postgres.py
    bot\\venv\\Scripts\\python.exe tools\\migrate_sqlite_to_postgres.py --sqlite yo'l\\zaxira.db

- Postgres manzili DATABASE_URL muhit o'zgaruvchisidan olinadi; bo'lmasa yashirin so'raladi (ekranda ko'rinmaydi).
  bot/.env o'qilmaydi - lokal bot SQLite'da qolishi uchun.
- Asl .db faylga tegilmaydi: vaqtinchalik nusxa olinib, o'sha nusxa o'qiladi.
- Qayta ishga tushirsa ham ma'lumot ikkilanmaydi: mavjud qatorlar (bir xil ID / kalit) o'tkazib yuboriladi.
- Postgres'da shu ID bilan BOSHQA buyurtma bo'lsa - hech narsa yozilmaydi, xato bilan to'xtaydi.
- Hammasi bitta tranzaksiyada: xato bo'lsa Postgres o'zgarmaydi.
- Konsolga faqat qatorlar soni chiqadi (telefon, ism va boshqa shaxsiy ma'lumot chiqmaydi).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))

from db import IDENTITY_TABLES, PRIMARY_KEYS, TABLES, Database, DatabaseError, describe_dsn, is_postgres_url  # noqa: E402
from logsafe import scrub  # noqa: E402

DEFAULT_SQLITE = BOT_DIR / "chicken24.db"


class MigrationConflict(Exception):
    """Postgres'da shu ID bilan boshqa yozuv bor."""


def read_sqlite(path: Path) -> dict[str, list[tuple]]:
    """Asl faylning izchil nusxasini olib (backup API), eski sxema bo'lsa yangilab, hamma jadvalni o'qiydi."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.db"
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

        async def load() -> dict[str, list[tuple]]:
            db = Database(copy)  # eski ustunlar bo'lsa shu nusxada qo'shiladi (asl faylda emas)
            await db.connect()
            try:
                return await db.export_tables()
            finally:
                await db.close()

        return asyncio.run(load())


async def migrate(dsn: str, data: dict[str, list[tuple]], *, server_settings: dict | None = None) -> dict[str, dict]:
    """data -> Postgres. Qaytadi: {jadval: {"sqlite": n, "added": n, "skipped": n, "postgres": n}}."""
    db = Database(dsn=dsn, server_settings=server_settings)
    await db.connect()  # jadvallar yo'q bo'lsa yaratiladi
    stats: dict[str, dict] = {}
    try:
        async with db.pg_connection() as con:
            async with con.transaction():
                # Ko'chirish paytida bot yozib qo'ymasligi uchun
                await con.execute("LOCK TABLE " + ", ".join(TABLES) + " IN SHARE ROW EXCLUSIVE MODE")
                await check_conflicts(con, data)
                for table, columns in TABLES.items():
                    rows = data.get(table, [])
                    before = await con.fetchval(f"SELECT COUNT(*) FROM {table}")
                    if rows:
                        placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                        await con.executemany(
                            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                            f"ON CONFLICT ({PRIMARY_KEYS[table]}) DO NOTHING",
                            rows,
                        )
                    after = await con.fetchval(f"SELECT COUNT(*) FROM {table}")
                    added = int(after) - int(before)
                    stats[table] = {"sqlite": len(rows), "added": added, "skipped": len(rows) - added,
                                    "postgres": int(after)}
                # Avtomatik ID hisoblagichi ko'chirilgan eng katta ID dan davom etsin
                for table in IDENTITY_TABLES:
                    await con.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                    )
    finally:
        await db.close()
    return stats


async def check_conflicts(con, data: dict[str, list[tuple]]) -> None:
    """Bir xil ID li buyurtma/qator Postgres'da bor-u, lekin boshqa yozuv bo'lsa - to'xtaydi."""
    orders_cols = TABLES["orders"]
    i_id, i_user, i_created = (orders_cols.index(c) for c in ("id", "user_id", "created_at"))
    ours = {r[i_id]: (int(r[i_user]), r[i_created]) for r in data.get("orders", [])}
    if ours:
        rows = await con.fetch("SELECT id, user_id, created_at FROM orders WHERE id = ANY($1::bigint[])", list(ours))
        bad = [r["id"] for r in rows if (int(r["user_id"]), r["created_at"]) != ours[r["id"]]]
        if bad:
            raise MigrationConflict(f"orders: {len(bad)} ta ID Postgres'da boshqa buyurtmaga tegishli (masalan #{bad[0]})")
    item_cols = TABLES["order_items"]
    j_id, j_order = item_cols.index("id"), item_cols.index("order_id")
    items = {r[j_id]: int(r[j_order]) for r in data.get("order_items", [])}
    if items:
        rows = await con.fetch("SELECT id, order_id FROM order_items WHERE id = ANY($1::bigint[])", list(items))
        bad = [r["id"] for r in rows if int(r["order_id"]) != items[r["id"]]]
        if bad:
            raise MigrationConflict(f"order_items: {len(bad)} ta ID Postgres'da boshqa buyurtmaga tegishli")


def print_stats(stats: dict[str, dict]) -> None:
    print(f"{'jadval':<16}{'sqlite':>8}{'qo`shildi':>11}{'bor edi':>9}{'postgres':>10}")
    for table, s in stats.items():
        print(f"{table:<16}{s['sqlite']:>8}{s['added']:>11}{s['skipped']:>9}{s['postgres']:>10}")


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        if not sys.stdin.isatty():
            raise SystemExit("DATABASE_URL berilmagan.")
        dsn = getpass.getpass("Neon DATABASE_URL (yozganingiz ko'rinmaydi): ").strip()
    if not is_postgres_url(dsn):
        raise SystemExit("DATABASE_URL postgresql:// bilan boshlanishi kerak.")
    return dsn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite -> PostgreSQL ko'chirish (bir martalik).")
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE, help="SQLite fayl (standart: bot/chicken24.db)")
    args = parser.parse_args(argv)
    if not args.sqlite.is_file():
        print(f"SQLite fayl topilmadi: {args.sqlite}", file=sys.stderr)
        return 1
    dsn = get_dsn()
    print(f"Manba: {args.sqlite}")
    print(f"Manzil: PostgreSQL {describe_dsn(dsn)}")
    data = read_sqlite(args.sqlite)
    try:
        stats = asyncio.run(migrate(dsn, data))
    except MigrationConflict as exc:
        print(f"TO'XTATILDI, hech narsa yozilmadi: {exc}", file=sys.stderr)
        return 2
    except DatabaseError as exc:
        print(f"Postgres xatosi, hech narsa yozilmadi: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - xabar tozalanadi (parol/telefon chiqmasin)
        print(f"Xato, hech narsa yozilmadi: {type(exc).__name__}: {scrub(str(exc).splitlines()[0] if str(exc) else '')}",
              file=sys.stderr)
        return 3
    print_stats(stats)
    print("Tayyor. Qayta ishga tushirsangiz, mavjud qatorlar o'tkazib yuboriladi (ikkilanmaydi).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
