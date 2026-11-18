"""Buyurtma oqimi testlari (handler soxta bot/xabar bilan, Telegram'siz).

Ishga tushirish (bot/ papkasida):
    python tests/test_flow.py        yoki   python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import business  # noqa: E402
import config as config_module  # noqa: E402

# Testlar kun vaqtiga bog'liq bo'lmasin: odatda "ochiq", yopiq holat alohida testda
business.is_open_now = lambda: True
from db import Database  # noqa: E402
from handlers import (  # noqa: E402
    RateLimiter,
    normalize_uz_phone,
    on_cancel_order,
    on_contact,
    on_web_app_data,
    resend_unnotified,
    webapp_url_with_result,
)
from menu import MenuStore  # noqa: E402

MENU = {
    "categories": [{"id": "burger", "name": "Burger"}],
    "items": [
        {"id": "burger-1", "category": "burger", "name": "Klassik burger", "price": 25000},
        {"id": "burger-2", "category": "burger", "name": "Cheese <b>", "price": 30000},
    ],
}
REGISTAN = (39.6548, 66.9757)


class FakeBot:
    def __init__(self, fail_admin: bool = False) -> None:
        self.sent: list[tuple] = []
        self.fail_admin = fail_admin

    async def send_message(self, chat_id, text, **kw):
        if self.fail_admin and chat_id == 999:
            raise RuntimeError("admin bloklagan (sinov)")
        self.sent.append(("message", chat_id, text, kw))
        return SimpleNamespace(message_id=1000 + len(self.sent))

    async def send_location(self, chat_id, latitude, longitude, **kw):
        if self.fail_admin and chat_id == 999:
            raise RuntimeError("admin bloklagan (sinov)")
        self.sent.append(("location", chat_id, latitude, longitude))


def make_message(user_id: int, payload) -> tuple[SimpleNamespace, list]:
    answers: list = []

    async def answer(text, **kw):
        answers.append((text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=500 + len(answers))

    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, username="mijoz", first_name="Test"),
        web_app_data=SimpleNamespace(data=payload if isinstance(payload, str) else json.dumps(payload)),
        chat=SimpleNamespace(id=user_id),
        answer=answer,
    )
    return msg, answers


def confirmation(answers):
    """Muvaffaqiyatli buyurtmada mijoz 2 xabar oladi: tasdiq (reply-klaviatura bilan) va bekor qilish taklifi.
    Rad etilganda - bitta xabar. Har ikki holatda tasdiq/rad - birinchi xabar."""
    return answers[0]


def button_query(markup) -> dict[str, list[str]]:
    url = markup.keyboard[0][0].web_app.url
    return parse_qs(urlparse(url).query)


def good(**over):
    data = {
        "ref": "abc123xyz",
        "name": "Aziz",
        "phone": "+998901234567",
        "address_type": "location",
        "address_text": "",
        "lat": REGISTAN[0],
        "lon": REGISTAN[1],
        "items": [{"id": "burger-1", "qty": 2}, {"id": "burger-2", "qty": 1}],
    }
    data.update(over)
    return data


class Env:
    """Vaqtinchalik menyu + baza + sozlamalar."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        menu_path = self.tmp / "menu.json"
        menu_path.write_text(json.dumps(MENU), encoding="utf-8")
        self.config = make_config(menu_path, self.tmp / "t.db")
        self.db = Database(self.config.db_path)
        self.menu_store = MenuStore(menu_path)
        self.limiter = RateLimiter(30)

    def deps(self, bot):
        return dict(bot=bot, config=self.config, menu_store=self.menu_store, db=self.db, limiter=self.limiter)


def make_config(menu_path: Path, db_path: Path):
    fields = config_module.Config.__dataclass_fields__
    values = dict(
        bot_token="1:x",
        admin_id=999,
        webapp_url="https://example.pages.dev",
        menu_path=menu_path,
        db_path=db_path,
    )
    return config_module.Config(**{k: v for k, v in values.items() if k in fields})


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ 1. bot javobi -> tugma havolasi

def test_url_builder():
    assert webapp_url_with_result("https://a.dev", None) == "https://a.dev"
    assert webapp_url_with_result("https://a.dev", {"ok": "r1", "n": "5"}) == "https://a.dev?ok=r1&n=5"
    assert webapp_url_with_result("https://a.dev/?x=1#h", {"rej": "r1", "r": "limit"}) == "https://a.dev/?x=1&rej=r1&r=limit#h"


def test_success_returns_ok_link():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, answers = make_message(1, good())
        await on_web_app_data(msg, **env.deps(bot))
        await env.db.close()
        return answers

    answers = run(scenario())
    text, markup = confirmation(answers)
    assert text.startswith("✅")
    q = button_query(markup)
    assert q["ok"] == ["abc123xyz"] and q["n"] == ["1"], q


# ------------------------------------------------------------ 2. mijozga tasdiq matni

def test_customer_text_has_number_items_total():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(5, good())
        await on_web_app_data(msg, **env.deps(FakeBot()))
        await env.db.close()
        return answers

    text, _ = confirmation(run(scenario()))
    assert "#1" in text
    assert "Klassik burger × 2 = 50 000 so'm" in text
    assert "Cheese &lt;b&gt; × 1 = 30 000 so'm" in text  # HTML tozalangan
    assert "Taomlar: 80 000 so'm" in text
    assert "Yetkazish: 10 000 so'm" in text
    assert "Jami: 90 000 so'm" in text
    assert "To'lov: naqd (kuryerga)" in text


# ------------------------------------------------------------ yetkazish narxi, to'lov, ish vaqti

def test_delivery_fee_in_receipt_and_db_client_total_ignored():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, _ = make_message(11, good(total=1, delivery_fee=0, items_total=1))  # soxta summalar
        await on_web_app_data(msg, **env.deps(bot))
        cur = await env.db.conn.execute("SELECT items_total, delivery_fee, total FROM orders")
        row = await cur.fetchone()
        await env.db.close()
        return bot.sent[0][2], row

    receipt, row = run(scenario())
    assert row == (80000, 10000, 90000)
    for part in ("Taomlar: 80 000 so'm", "Yetkazish: 10 000 so'm", "Jami: 90 000 so'm", "To'lov: naqd (kuryerga)"):
        assert part in receipt, part


def test_closed_hours_rejected_and_not_saved():
    async def scenario():
        env = Env()
        await env.db.connect()
        business.is_open_now = lambda: False
        try:
            msg, answers = make_message(12, good())
            bot = FakeBot()
            await on_web_app_data(msg, **env.deps(bot))
        finally:
            business.is_open_now = lambda: True
        cur = await env.db.conn.execute("SELECT COUNT(*) FROM orders")
        count = (await cur.fetchone())[0]
        await env.db.close()
        return answers, count, bot.sent

    answers, count, sent = run(scenario())
    text, markup = confirmation(answers)
    assert "Hozir yopiqmiz. Ish vaqti 10:00 – 03:00" in text
    assert button_query(markup) == {"lang": ["uz"], "rej": ["abc123xyz"], "r": ["closed"]}
    assert count == 0 and sent == []


# ------------------------------------------------------------ 3. uy/qavat/xonadon va izoh

def test_details_and_comment_reach_admin_and_db():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, _ = make_message(6, good(address_details="12-uy, 3-qavat, 45-xonadon", comment="Domofon <45>\nqo'ng'iroq qiling"))
        await on_web_app_data(msg, **env.deps(bot))
        cur = await env.db.conn.execute("SELECT address_details, comment FROM orders")
        row = await cur.fetchone()
        await env.db.close()
        return bot.sent, row

    sent, row = run(scenario())
    receipt = sent[0][2]
    assert "Uy / qavat / xonadon: 12-uy, 3-qavat, 45-xonadon" in receipt
    # html.escape apostrofni &#x27; qiladi - Telegram uni ' qilib ko'rsatadi
    assert "Izoh: Domofon &lt;45&gt; qo&#x27;ng&#x27;iroq qiling" in receipt
    assert sent[1][0] == "location"  # lokatsiya ham yuborildi
    assert row == ("12-uy, 3-qavat, 45-xonadon", "Domofon <45> qo'ng'iroq qiling")


def test_details_too_long_rejected():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(7, good(address_details="x" * 101))
        await on_web_app_data(msg, **env.deps(FakeBot()))
        await env.db.close()
        return answers

    text, markup = confirmation(run(scenario()))
    assert "100 belgidan" in text and button_query(markup)["r"] == ["data"]


def test_old_database_is_migrated():
    import sqlite3

    tmp = Path(tempfile.mkdtemp()) / "old.db"
    con = sqlite3.connect(tmp)
    con.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, "
        "customer_name TEXT NOT NULL, phone TEXT NOT NULL, address_type TEXT NOT NULL, address_text TEXT, "
        "lat REAL, lon REAL, total INTEGER NOT NULL, created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0)"
    )
    con.execute("INSERT INTO orders (user_id, customer_name, phone, address_type, total, created_at) "
                "VALUES (1, 'Eski', '+998901234567', 'text', 1000, '2026-01-01T00:00:00+00:00')")
    con.commit()
    con.close()

    async def scenario():
        db = Database(tmp)
        await db.connect()
        cur = await db.conn.execute(
            "SELECT customer_name, address_details, comment, items_total, delivery_fee, total FROM orders"
        )
        rows = await cur.fetchall()
        await db.close()
        return rows

    # Eski buyurtma saqlangan; yetkazishdan oldingi summa = taomlar, yetkazish 0
    assert run(scenario()) == [("Eski", None, None, 1000, 0, 1000)]


# ------------------------------------------------------------ 5. admin'ga yetmagan buyurtmalar

def test_unnotified_order_is_resent_once():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot(fail_admin=True)
        msg, answers = make_message(8, good())
        await on_web_app_data(msg, **env.deps(bot))
        customer_text, markup = confirmation(answers)
        cur = await env.db.conn.execute("SELECT notified FROM orders")
        notified_before = (await cur.fetchone())[0]

        bot.fail_admin = False  # admin yana ochiq (bot qayta ishga tushdi)
        first, second = await asyncio.gather(
            resend_unnotified(bot, env.config, env.db),
            resend_unnotified(bot, env.config, env.db),
        )
        third = await resend_unnotified(bot, env.config, env.db)
        cur = await env.db.conn.execute("SELECT notified FROM orders")
        notified_after = (await cur.fetchone())[0]
        await env.db.close()
        return customer_text, markup, notified_before, (first, second, third), bot.sent, notified_after

    text, markup, before, counts, sent, after = run(scenario())
    # Mijoz: buyurtma qabul qilindi, kechikish haqida ogohlantirish, savatni tozalash havolasi
    assert text.startswith("✅") and "Qayta buyurtma bermang" in text
    assert button_query(markup)["ok"] == ["abc123xyz"]
    assert before == 0 and after == 1
    assert sorted(counts) == [0, 0, 1], counts  # parallel chaqiruvda ham faqat 1 marta
    receipts = [s for s in sent if s[0] == "message"]
    assert len(receipts) == 1 and "Qayta yuborildi" in receipts[0][2] and "#1" in receipts[0][2]
    assert [s[0] for s in sent] == ["message", "location"]


def test_location_outside_samarkand_rejected_by_bot():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, answers = make_message(10, good(lat=41.2995, lon=69.2401))  # Toshkent
        await on_web_app_data(msg, **env.deps(bot))
        cur = await env.db.conn.execute("SELECT COUNT(*) FROM orders")
        count = (await cur.fetchone())[0]
        await env.db.close()
        return answers, count, bot.sent

    answers, count, sent = run(scenario())
    text, markup = confirmation(answers)
    assert "qabul qilinmadi" in text and "Samarqand shahri ichiga yetkazamiz" in text
    assert button_query(markup)["r"] == ["area"]
    assert count == 0  # bazaga yozilmadi
    assert not any("yangi buyurtma" in s[2] for s in sent if s[0] == "message")  # admin'ga chek ketmadi


# ------------------------------------------------------------ 11. mijoz bekor qilishi (5 daqiqa)

class FakeCallback:
    def __init__(self, user_id: int, data: str) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.alerts: list = []
        self.edits: list = []
        outer = self

        async def edit_text(text, **kw):
            outer.edits.append(text)

        self.message = SimpleNamespace(edit_text=edit_text)

    async def answer(self, text=None, show_alert=False, **kw):
        self.alerts.append((text, show_alert))


async def _place_order(env, bot, user_id=20):
    msg, answers = make_message(user_id, good())
    await on_web_app_data(msg, **env.deps(bot))
    return answers


def test_success_sends_cancel_offer_keeps_ok_link():
    async def scenario():
        env = Env()
        await env.db.connect()
        answers = await _place_order(env, FakeBot())
        await env.db.close()
        return answers

    answers = run(scenario())
    assert len(answers) == 2
    first_text, first_markup = answers[0]
    assert first_text.startswith("✅") and button_query(first_markup)["ok"] == ["abc123xyz"]
    offer_text, offer_markup = answers[1]
    assert "5 daqiqa" in offer_text
    button = offer_markup.inline_keyboard[0][0]
    assert button.callback_data == "cancel:1"


def test_customer_cancels_within_window():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        await _place_order(env, bot)
        cb = FakeCallback(20, "cancel:1")
        await on_cancel_order(cb, bot, env.config, env.db)
        cur = await env.db.conn.execute("SELECT status, cancelled_at IS NOT NULL FROM orders WHERE id = 1")
        row = await cur.fetchone()
        again = FakeCallback(20, "cancel:1")
        await on_cancel_order(again, bot, env.config, env.db)
        report_resend = await resend_unnotified(bot, env.config, env.db)
        await env.db.close()
        return cb, row, again, bot.sent, report_resend

    cb, row, again, sent, resent = run(scenario())
    assert row == ("cancelled", 1)
    assert cb.alerts[0][0] == "Buyurtma bekor qilindi" and "bekor qilindi" in cb.edits[0]
    # Mijozga: natijali havola ?cx=<ref> (Mini App tarixida "Bekor qilindi")
    customer = [s for s in sent if s[0] == "message" and s[1] == 20][-1]
    assert parse_qs(urlparse(customer[3]["reply_markup"].keyboard[0][0].web_app.url).query) == {"lang": ["uz"], "cx": ["abc123xyz"]}
    # Admin'ga: chekka javob sifatida
    admin_msgs = [s for s in sent if s[0] == "message" and s[1] == 999]
    assert "bekor qildi" in admin_msgs[-1][2]
    assert admin_msgs[-1][3]["reply_parameters"].message_id == 1001  # birinchi chek xabari
    assert "allaqachon" in again.alerts[0][0]
    assert resent == 0


def test_cancel_rejected_after_window_or_by_other_user():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        await _place_order(env, bot)
        stranger = FakeCallback(77, "cancel:1")
        await on_cancel_order(stranger, bot, env.config, env.db)
        # Buyurtma 6 daqiqa oldin berilgan bo'lsin
        old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        await env.db.conn.execute("UPDATE orders SET created_at = ? WHERE id = 1", (old,))
        await env.db.conn.commit()
        late = FakeCallback(20, "cancel:1")
        await on_cancel_order(late, bot, env.config, env.db)
        cur = await env.db.conn.execute("SELECT status FROM orders WHERE id = 1")
        status = (await cur.fetchone())[0]
        await env.db.close()
        return stranger, late, status

    stranger, late, status = run(scenario())
    assert stranger.alerts[0][0] == "Buyurtma topilmadi"
    assert "muddati" in late.alerts[0][0] and late.edits
    assert status == "new"


# ------------------------------------------------------------ 8. Telegram orqali telefon

def test_normalize_uz_phone():
    assert normalize_uz_phone("998901234567") == "+998901234567"
    assert normalize_uz_phone("+998 90 123-45-67") == "+998901234567"
    assert normalize_uz_phone("+79161234567") is None
    assert normalize_uz_phone("") is None and normalize_uz_phone(None) is None


def test_shared_contact_marks_phone_verified():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()

        def contact_msg(sender, owner, phone):
            return SimpleNamespace(from_user=SimpleNamespace(id=sender),
                                   contact=SimpleNamespace(user_id=owner, phone_number=phone))

        await on_contact(contact_msg(30, 99, "998905555555"), env.db)   # boshqaning kontakti - e'tiborsiz
        await on_contact(contact_msg(30, 30, "998901234567"), env.db)   # o'zi
        await on_web_app_data(make_message(30, good())[0], **env.deps(bot))           # bir xil raqam
        await on_web_app_data(make_message(31, good(ref="other12"))[0], **env.deps(bot))  # kontakt yo'q
        cur = await env.db.conn.execute("SELECT user_id, phone_verified FROM orders ORDER BY id")
        rows = await cur.fetchall()
        receipts = [s[2] for s in bot.sent if s[0] == "message" and s[1] == 999]
        await env.db.close()
        return rows, receipts

    rows, receipts = run(scenario())
    assert rows == [(30, 1), (31, 0)]
    assert "(Telegram raqami)" in receipts[0] and "(Telegram raqami)" not in receipts[1]


def test_fresh_order_not_resent_before_min_age():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot(fail_admin=True)
        await on_web_app_data(make_message(9, good())[0], **env.deps(bot))
        bot.fail_admin = False
        count = await resend_unnotified(bot, env.config, env.db, min_age_seconds=60)
        await env.db.close()
        return count

    assert run(scenario()) == 0


def test_rate_limit_returns_rejected_link():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        await on_web_app_data(make_message(1, good())[0], **env.deps(bot))
        msg, answers = make_message(1, good(ref="second1"))
        await on_web_app_data(msg, **env.deps(bot))
        await env.db.close()
        return answers

    text, markup = confirmation(run(scenario()))
    assert "30 soniya" in text or "soniyadan" in text
    q = button_query(markup)
    assert q["rej"] == ["second1"] and q["r"] == ["limit"], q


def test_unknown_item_returns_menu_reason():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(2, good(items=[{"id": "burger-99", "qty": 1}]))
        await on_web_app_data(msg, **env.deps(FakeBot()))
        await env.db.close()
        return answers

    text, markup = confirmation(run(scenario()))
    assert "qabul qilinmadi" in text and "qayta urinib" in text
    assert button_query(markup)["r"] == ["menu"]


def test_broken_json_has_no_ref():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(3, "{buzuq")
        await on_web_app_data(msg, **env.deps(FakeBot()))
        await env.db.close()
        return answers

    text, markup = confirmation(run(scenario()))
    assert "qabul qilinmadi" in text
    assert button_query(markup) == {"lang": ["uz"]}  # natijasiz, faqat til


def test_invalid_ref_is_ignored():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(4, good(ref="<script>"))
        await on_web_app_data(msg, **env.deps(FakeBot()))
        await env.db.close()
        return answers

    text, markup = confirmation(run(scenario()))
    assert text.startswith("✅")
    assert button_query(markup) == {"lang": ["uz"]}  # natijasiz, faqat til


if __name__ == "__main__":
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
