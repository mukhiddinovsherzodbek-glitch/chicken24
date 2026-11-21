"""Xavfsizlik testlari (Telegram'siz): tekshiruvda topilgan zaifliklar qayta paydo bo'lmasligi uchun.

- Excel eksport: mijoz yozgan matn ('=...' bilan boshlansa ham) formula bo'lib yozilmaydi;
- ko'rinmas belgilar (matn yo'nalishini o'zgartiruvchi va h.k.) indamasdan olib tashlanadi; log'ga soxta qator yo'q;
- buyurtma holati: bir xil ref'li ikki mijoz - har biri faqat o'zinikini ko'radi;
- POST /api/orders: minglab ichma-ich JSON - 400 (500 emas); server versiyasi oshkor qilinmaydi;
- bot faqat shaxsiy chatda ishlaydi (guruhda jim);
- flood: bitta foydalanuvchidan 10 soniyada 15 tadan ortiq xabar tashlab yuboriladi (admin va buyurtma mustasno);
- admin'ga ogohlantirishlar cheklangan (bitta mijoz admin chatini to'ldira olmaydi);
- pastki klaviaturani olib tashlash: bir marta, muvaffaqiyatsiz urinish esa qayta uriniladi.
"""
from __future__ import annotations

import asyncio
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardRemove, User, WebAppData
from openpyxl import load_workbook

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import excel_export  # noqa: E402
import handlers  # noqa: E402
import orderapi  # noqa: E402
from handlers import LanguageMiddleware, on_web_app_data  # noqa: E402
from test_flow import Env, FakeBot, good, make_message  # noqa: E402
from test_orders_http import init_data_for, post, with_api  # noqa: E402
from validators import ValidationError, validate_order  # noqa: E402

business.is_open_now = lambda: True
ADMIN = 999  # test_flow.make_config
NOW = datetime.now(timezone.utc)


def run(coro):
    return asyncio.run(coro)


# ================================================================ Excel: formula in'eksiyasi

EVIL = {
    "name": '=HYPERLINK("https://evil.example","Bosing")',
    "address_text": "=cmd|' /C calc'!A0",
    "address_details": "@SUM(1,2)",
    "comment": "+1+1",
}


def test_excel_customer_text_is_never_a_formula():
    env = Env()
    order = validate_order(good(address_type="text", lat=None, lon=None, **EVIL), env.menu_store.get(),
                           delivery_fee=10000)
    row = {
        "id": 1, "created_at": NOW.isoformat(), "status": "new", "customer_name": order.name, "phone": order.phone,
        "username": "mijoz", "address_type": "text", "address_text": order.address_text, "lat": None, "lon": None,
        "address_details": order.address_details, "comment": order.comment,
        "items_total": order.items_total, "delivery_fee": order.delivery_fee, "total": order.total,
    }
    items = [{"name": "=1+1", "qty": 1, "price": 25000, "line_total": 25000}]
    period = excel_export.export_range("hammasi", NOW)
    wb = load_workbook(io.BytesIO(excel_export.build_workbook([(row, items)], period, NOW)))

    ws = wb["Buyurtmalar"]
    expected = {4: EVIL["name"], 5: "+998901234567", 6: "@mijoz", 7: EVIL["address_text"],
                8: EVIL["address_details"], 9: EVIL["comment"], 11: "=1+1 × 1"}
    for col, value in expected.items():
        cell = ws.cell(2, col)
        assert cell.data_type == "s", f"{col}-ustun formula bo'lib yozilgan: {cell.data_type!r}"
        assert cell.value == value, (col, cell.value)
    # Boshqa varaqlar: taom nomi ham oddiy matn
    assert wb["Taomlar"].cell(2, 4).data_type == "s" and wb["Taomlar"].cell(2, 4).value == "=1+1"
    names = [c for c in wb["Xulosa"]["A"] if c.value == "=1+1"]
    assert names and all(c.data_type == "s" for c in names)
    # Hech bir varaqda formula turidagi katak yo'q
    for sheet in wb.worksheets:
        for cells in sheet.iter_rows():
            for cell in cells:
                assert cell.data_type != "f", f"{sheet.title}!{cell.coordinate} formula"


# ================================================================ ko'rinmas belgilar va log

def test_invisible_direction_chars_are_stripped():
    menu = Env().menu_store.get()
    rlo, zwsp, bom = "‮", "​", "﻿"
    order = validate_order(good(
        name=f"Az{rlo}iz{zwsp}", address_type="text", lat=None, lon=None,
        address_text=f"{bom}Registon{rlo} 5", address_details=f"3{zwsp}-uy", comment=f"domofon{rlo} 45",
    ), menu)
    assert order.name == "Aziz" and order.address_text == "Registon 5"
    assert order.address_details == "3-uy" and order.comment == "domofon 45"
    # faqat ko'rinmas belgilardan iborat "ism" - bo'sh hisoblanadi
    try:
        validate_order(good(name=rlo + zwsp + rlo), menu)
    except ValidationError as exc:
        assert exc.key == "val.name_len"
    else:
        raise AssertionError("ko'rinmas belgilardan iborat ism qabul qilindi")
    # murakkab emoji (ZWJ bilan) buzilmaydi
    family = "\U0001F468‍\U0001F469‍\U0001F467"
    assert validate_order(good(comment=family), menu).comment == family


def test_item_id_cannot_forge_log_lines():
    menu = Env().menu_store.get()
    forged = "x\n2026-11-21 | INFO | Buyurtma #999 qabul qilindi"
    for items in ([{"id": forged, "qty": 1}], [{"id": forged, "qty": 999}],
                  [{"id": forged, "qty": 1}, {"id": forged, "qty": 1}]):
        try:
            validate_order(good(items=items), menu)
        except ValidationError as exc:
            assert "\n" not in exc.detail and "\r" not in exc.detail, exc.detail
        else:
            raise AssertionError("noto'g'ri savat qabul qilindi")


# ================================================================ holat: bir xil ref, ikki mijoz

def test_order_status_owner_filter_with_same_ref():
    async def go():
        env = Env()
        await env.db.connect()
        order = validate_order(good(), env.menu_store.get(), delivery_fee=10000)
        ref = "sameref0000000001"
        first = await env.db.save_order(user_id=1, username=None, order=order, created_at=NOW, client_ref=ref)
        second = await env.db.save_order(user_id=2, username=None, order=order, created_at=NOW, client_ref=ref)
        await env.db.cancel_order(second, 2, NOW, 300)
        out = [await env.db.order_status(ref, user_id=u) for u in (1, 2, 3)]
        await env.db.close()
        return first, second, out

    first, second, (one, two, three) = run(go())
    assert one == {"status": "accepted", "n": first, "reason": None}, "birinchi mijoz o'z buyurtmasini ko'radi"
    assert two == {"status": "cancelled", "n": second, "reason": None}
    assert three == {"status": "unknown", "n": None, "reason": None}, "begona mijozga hech narsa qaytmaydi"


# ================================================================ HTTP

def test_deeply_nested_json_is_400_not_500():
    async def fn(client, env, bot, _api):
        init = init_data_for(5)
        nested = (await post(client, "[" * 15000, init_data=init))[:2]
        nested_obj = (await post(client, '{"ref":"abcdef123456","items":' + "[" * 15000, init_data=init))[:2]
        count = (await (await env.db.conn.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        return nested, nested_obj, count

    nested, nested_obj, count = run(with_api(fn))
    assert nested == (400, {"error": "bad_request"}) and nested_obj == (400, {"error": "bad_request"})
    assert count == 0


def test_server_version_is_not_disclosed():
    async def fn(client, env, bot, _api):
        out = {}
        for name, resp in (
            ("health", await client.get("/health")),
            ("status", await client.get(orderapi.STATUS_PATH, params={"ref": "0" * 32})),
            ("orders", await client.post(orderapi.ORDERS_PATH, data="{}")),
            ("missing", await client.get("/no-such-page")),
        ):
            out[name] = (resp.headers.get("Server"), resp.headers.get("X-Content-Type-Options"))
        return out

    for name, (server, nosniff) in run(with_api(fn)).items():
        assert server == "chicken24", f"{name}: Server={server!r}"
        assert "aiohttp" not in (server or "") and "Python" not in (server or "")
        assert nosniff == "nosniff", name


# ================================================================ faqat shaxsiy chat

def tg_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Ali")


def message_in(chat_type: str, user_id: int, text: str = "/start", **kw) -> Message:
    chat_id = user_id if chat_type == "private" else -1001234567890
    return Message(message_id=1, date=NOW, chat=Chat(id=chat_id, type=chat_type), from_user=tg_user(user_id),
                   text=text, **kw)


async def through(middleware, env: Env, bot, event, called: list):
    async def handler(ev, data):
        called.append(data.get("lang"))
    data = {"db": env.db, "config": env.config, "bot": bot, "event_from_user": event.from_user}
    return await middleware(handler, event, data)


def test_bot_is_silent_outside_private_chats():
    async def go():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(ADMIN, "uz", NOW)
        await env.db.set_user_lang(7, "uz", NOW)
        bot = FakeBot()
        mw = LanguageMiddleware()
        called: list = []
        for chat_type in ("group", "supergroup", "channel"):
            await through(mw, env, bot, message_in(chat_type, 7, "salom"), called)
            await through(mw, env, bot, message_in(chat_type, 8, "/start"), called)       # tili yo'q mijoz
            await through(mw, env, bot, message_in(chat_type, ADMIN, "/excel"), called)   # admin guruhda
            group_button = CallbackQuery(id="1", from_user=tg_user(7), chat_instance="x", data="lang:uz",
                                         message=message_in(chat_type, 7))
            await through(mw, env, bot, group_button, called)
        in_group = (list(called), list(bot.sent))
        await through(mw, env, bot, message_in("private", 7, "salom"), called)
        await env.db.close()
        return in_group, called

    (group_called, group_sent), called = run(go())
    assert group_called == [] and group_sent == [], "guruh/kanalda handler ham, javob ham yo'q"
    assert called == ["uz"], "shaxsiy chatda avvalgidek ishlaydi"


# ================================================================ flood

def test_flood_is_dropped_but_orders_and_admin_are_not():
    async def go():
        env = Env()
        await env.db.connect()
        for uid in (7, 8, ADMIN):
            await env.db.set_user_lang(uid, "uz", NOW)
        bot = FakeBot()
        mw = LanguageMiddleware()
        spam, other, admin, orders = [], [], [], []
        for _ in range(40):
            await through(mw, env, bot, message_in("private", 7, "spam"), spam)
        # tugma bosishlar ham shu hisobga kiradi
        button = CallbackQuery(id="1", from_user=tg_user(7), chat_instance="x", data="cancel:1")
        await through(mw, env, bot, button, spam)
        # boshqa mijozga ta'sir qilmaydi
        await through(mw, env, bot, message_in("private", 8, "salom"), other)
        # admin cheklanmaydi
        for _ in range(40):
            await through(mw, env, bot, message_in("private", ADMIN, "/hisobot"), admin)
        # Mini App buyurtmasi (web_app_data) flood paytida ham tashlab yuborilmaydi
        order = message_in("private", 7, None, web_app_data=WebAppData(data="{}", button_text="x"))
        await through(mw, env, bot, order, orders)
        sent = list(bot.sent)
        await env.db.close()
        return spam, other, admin, orders, sent

    spam, other, admin, orders, sent = run(go())
    assert len(spam) == handlers.FLOOD_LIMIT == 15, len(spam)
    assert other == ["uz"] and len(admin) == 40 and orders == ["uz"]
    assert sent == [], "tashlab yuborilgan xabarlarga hech narsa yuborilmaydi"


# ================================================================ admin ogohlantirishlari

def test_admin_notices_are_throttled():
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        answers_all = []
        for i in range(5):  # bitta mijoz ketma-ket 5 ta noto'g'ri buyurtma yuboradi (har biri yangi ref bilan)
            msg, answers = make_message(1, good(phone="12345", ref=f"badorder{i:04d}"))
            await on_web_app_data(msg, **env.deps(bot))
            answers_all.append(answers)
        same_user = [s for s in bot.sent if s[1] == ADMIN]
        msg, _ = make_message(2, good(phone="12345", ref="badorder9999"))  # boshqa mijoz - alohida ogohlantirish
        await on_web_app_data(msg, **env.deps(bot))
        two_users = [s for s in bot.sent if s[1] == ADMIN]
        # umumiy chegara: juda ko'p turli mijoz
        for uid in range(100, 140):
            await handlers.notify_admin(bot, env.config, "Sinov", SimpleNamespace(id=uid, username=None), "x")
        total = [s for s in bot.sent if s[1] == ADMIN]
        await env.db.close()
        return answers_all, same_user, two_users, total

    answers_all, same_user, two_users, total = run(go())
    assert all(a and a[0][0].startswith("❌") for a in answers_all), "mijoz har safar sababini ko'radi"
    assert len(same_user) == 1 and "rad etildi" in same_user[0][2], "admin'ga bitta mijozdan 1 ta ogohlantirish"
    assert len(two_users) == 2
    assert len(total) == handlers.ADMIN_NOTICE_BURST[0] == 20, len(total)


def test_admin_receipts_are_never_throttled():
    """Cheklov faqat ogohlantirishlarga: buyurtma cheklari har doim yetadi."""
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        for uid in range(1, 31):
            msg, _ = make_message(uid, good(ref=f"receipt{uid:06d}"))
            await on_web_app_data(msg, **env.deps(bot))
        receipts = [s for s in bot.sent if s[0] == "message" and s[1] == ADMIN and "yangi buyurtma" in s[2]]
        await env.db.close()
        return receipts

    assert len(run(go())) == 30


# ================================================================ pastki klaviatura: bir marta

class FlakyBot(FakeBot):
    """Birinchi ReplyKeyboardRemove xabari yuborilmaydi (tarmoq xatosi)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_removal = True

    async def send_message(self, chat_id, text, **kw):
        if isinstance(kw.get("reply_markup"), ReplyKeyboardRemove) and self.fail_next_removal:
            self.fail_next_removal = False
            raise RuntimeError("tarmoq (sinov)")
        return await super().send_message(chat_id, text, **kw)


def test_keyboard_removed_once_and_failed_attempt_is_retried():
    async def go():
        env = Env()
        await env.db.connect()
        bot = FlakyBot()
        steps = []
        for lang in ("uz", "uz", "uz", "ru", "ru"):
            await handlers.switch_to_inline(bot, env.config, env.db, 5, lang)
            steps.append((list(bot.kb_removed), [b.type for _, b in bot.menu_buttons]))
        # boshqa bot nusxasi (qayta ishga tushish) - holat nollanadi
        fresh = FakeBot()
        await handlers.switch_to_inline(fresh, env.config, env.db, 5, "ru")
        await env.db.close()
        return steps, fresh

    steps, fresh = run(go())
    assert steps[0] == ([], ["commands"]), "birinchi urinish yiqildi - eslab qolinmaydi"
    assert steps[1] == ([5], ["commands"]), "ikkinchi urinishda olib tashlandi; menyu tugmasi qayta o'rnatilmadi"
    assert steps[2] == steps[1], "uchinchi safar hech narsa yuborilmaydi"
    assert steps[3] == steps[1], "til o'zgarsa ham menyu tugmasi qayta o'rnatilmaydi (buyruqlar - tilsiz)"
    assert steps[4] == steps[3]
    assert fresh.kb_removed == [5] and len(fresh.menu_buttons) == 1


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
