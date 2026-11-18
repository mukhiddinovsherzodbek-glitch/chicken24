"""Zaxira (backup) va admin hisoboti (/hisobot) testlari - Telegram'siz."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import backup  # noqa: E402
import config  # noqa: E402
import reports  # noqa: E402
from db import Database  # noqa: E402
from handlers import cmd_backup, cmd_report  # noqa: E402
from menu import parse_menu  # noqa: E402
from validators import validate_order  # noqa: E402

CFG = config.Config(bot_token="1:x", admin_id=999, webapp_url="https://example.pages.dev",
                    menu_path=Path("menu.json"), db_path=Path("t.db"))
MENU = parse_menu({
    "categories": [{"id": "c", "name": "C"}],
    "items": [
        {"id": "a", "category": "c", "name": "Burger", "price": 30000},
        {"id": "b", "category": "c", "name": "Pitsa", "price": 50000},
    ],
})
SKD = config.LOCAL_TZ


class FakeBot:
    def __init__(self) -> None:
        self.docs: list = []
        self.msgs: list = []

    async def send_document(self, chat_id, document, caption=None, **kw):
        self.docs.append((chat_id, document, caption))

    async def send_message(self, chat_id, text, **kw):
        self.msgs.append((chat_id, text))


def order(items, address="Samarqand"):
    data = {"name": "Aziz", "phone": "+998901234567", "address_type": "text", "address_text": address,
            "items": [{"id": i, "qty": q} for i, q in items]}
    return validate_order(data, MENU, delivery_fee=10000)


async def new_db() -> Database:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    await db.connect()
    return db


def local(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=SKD).astimezone(timezone.utc)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ zaxira

def test_backup_is_valid_copy_and_skips_when_unchanged():
    async def scenario():
        db = await new_db()
        bot = FakeBot()
        await db.save_order(user_id=1, username=None, order=order([("a", 2)]), created_at=datetime.now(timezone.utc))
        first = await backup.send_backup(bot, CFG, db, reason="sinov")
        changed_after = await backup.has_changes(db)
        await db.save_order(user_id=2, username=None, order=order([("b", 1)]), created_at=datetime.now(timezone.utc))
        changed_new = await backup.has_changes(db)
        await db.close()
        return first, changed_after, changed_new, bot.docs

    first, changed_after, changed_new, docs = run(scenario())
    assert first is True and len(docs) == 1
    chat_id, document, caption = docs[0]
    assert chat_id == 999 and document.filename.startswith("chicken24_") and document.filename.endswith(".db")
    assert "Buyurtmalar: 1 ta" in caption
    # Yuborilgan fayl - haqiqiy SQLite baza, buyurtma ichida
    path = Path(tempfile.mkdtemp()) / "copy.db"
    path.write_bytes(document.data)
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*), SUM(total) FROM orders").fetchone() == (1, 70000)
    con.close()
    assert changed_after is False, "zaxiradan keyin o'zgarish yo'q"
    assert changed_new is True, "yangi buyurtmadan keyin o'zgarish bor"


def test_startup_backup_only_if_due():
    async def scenario():
        db = await new_db()
        bot = FakeBot()
        empty = await backup.startup_backup_if_due(bot, CFG, db)  # buyurtma yo'q
        await db.save_order(user_id=1, username=None, order=order([("a", 1)]), created_at=datetime.now(timezone.utc))
        first = await backup.startup_backup_if_due(bot, CFG, db)  # hech qachon zaxira bo'lmagan
        await db.save_order(user_id=1, username=None, order=order([("a", 1)]), created_at=datetime.now(timezone.utc))
        recent = await backup.startup_backup_if_due(bot, CFG, db)  # 24 soat o'tmagan
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        await db.set_meta(backup.META_LAST_AT, old)
        overdue = await backup.startup_backup_if_due(bot, CFG, db)  # 25 soat o'tgan
        await db.close()
        return empty, first, recent, overdue, len(bot.docs)

    assert run(scenario()) == (False, True, False, True, 2)


def test_seconds_until_backup_time():
    t4 = time(4, 0)
    assert backup.seconds_until(t4, datetime(2026, 11, 17, 3, 30, tzinfo=SKD)) == 30 * 60
    assert backup.seconds_until(t4, datetime(2026, 11, 17, 4, 0, tzinfo=SKD)) == 24 * 3600
    assert backup.seconds_until(t4, datetime(2026, 11, 17, 23, 0, tzinfo=SKD)) == 5 * 3600


# ------------------------------------------------------------ hisobot

def test_business_day_boundary():
    # 01:30 dagi buyurtma oldingi ish kuniga tegishli (kun 03:00 da almashadi)
    assert reports.business_day_start(datetime(2026, 11, 17, 1, 30, tzinfo=SKD)) == datetime(2026, 11, 16, 3, 0, tzinfo=SKD)
    assert reports.business_day_start(datetime(2026, 11, 17, 3, 0, tzinfo=SKD)) == datetime(2026, 11, 17, 3, 0, tzinfo=SKD)
    names = [p[0] for p in reports.periods(datetime(2026, 11, 18, 12, 0, tzinfo=SKD))]
    assert names == ["Bugun", "Shu hafta", "Shu oy"]
    week_start = reports.periods(datetime(2026, 11, 18, 12, 0, tzinfo=SKD))[1][1]
    assert week_start == datetime(2026, 11, 16, 3, 0, tzinfo=SKD)  # dushanba 03:00


def test_report_numbers_exclude_cancelled():
    async def scenario():
        db = await new_db()
        # "Hozir" = 18.11.2026 12:00 (chorshanba)
        now = local(2026, 11, 18, 12)
        await db.save_order(user_id=1, username=None, order=order([("a", 2)]), created_at=local(2026, 11, 18, 11))   # bugun 70k
        await db.save_order(user_id=1, username=None, order=order([("b", 1)]), created_at=local(2026, 11, 18, 1))    # kecha (ish kuni) 60k
        await db.save_order(user_id=1, username=None, order=order([("a", 1)]), created_at=local(2026, 11, 3, 15))    # shu oy 40k
        await db.save_order(user_id=1, username=None, order=order([("a", 1)]), created_at=local(2026, 10, 30, 15))   # o'tgan oy
        oid = await db.save_order(user_id=1, username=None, order=order([("b", 3)]), created_at=local(2026, 11, 18, 11, 30))
        await db.cancel_order(oid, 1, local(2026, 11, 18, 11, 31), 300)                                          # bekor
        text = await reports.build_report(db, now=now)
        await db.close()
        return text

    text = run(scenario())
    assert "<b>Bugun:</b> 1 ta · <b>70 000 so'm</b>" in text, text
    assert "bekor qilingan: 1 ta" in text
    assert "<b>Shu hafta:</b> 2 ta · <b>130 000 so'm</b>" in text, text
    assert "<b>Shu oy:</b> 3 ta · <b>170 000 so'm</b>" in text, text
    assert "1. Burger — 3 ta, 90 000 so'm" in text, text


def test_admin_commands_only_for_admin():
    async def scenario():
        db = await new_db()
        await db.save_order(user_id=1, username=None, order=order([("a", 1)]), created_at=datetime.now(timezone.utc))
        bot = FakeBot()
        out: dict[str, list] = {"admin": [], "user": []}

        def msg(uid, key):
            async def answer(text, **kw):
                out[key].append(text)
            return SimpleNamespace(from_user=SimpleNamespace(id=uid), answer=answer)

        await cmd_report(msg(999, "admin"), CFG, db)
        await cmd_report(msg(5, "user"), CFG, db)
        await cmd_backup(msg(5, "user"), bot, CFG, db)
        user_docs = len(bot.docs)
        await cmd_backup(msg(999, "admin"), bot, CFG, db)
        await db.close()
        return out, user_docs, len(bot.docs)

    out, user_docs, admin_docs = run(scenario())
    assert out["admin"][0].startswith("📊")
    assert out["user"] == [], "admin bo'lmaganga bot jim qolishi kerak"
    assert user_docs == 0 and admin_docs == 1


if __name__ == "__main__":
    import logging

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
