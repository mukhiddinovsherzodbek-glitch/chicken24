"""POST /api/orders (Mini App buyurtmasi HTTP orqali) va yangi interfeys (inline tugma, menyu tugmasi) testlari.

- initData: to'g'ri / buzilgan / eskirgan / bo'sh; user_id FAQAT initData dan;
- idempotentlik: bir xil (user_id, ref) - bitta buyurtma, bir xil javob, xabar bir marta; parallel ham;
- tana hajmi, taomlar soni va miqdor chegarasi, noto'g'ri JSON (400);
- ish vaqti va lokatsiya bo'yicha rad etish; so'rov cheklovi; CORS (POST + OPTIONS);
- umumiy funksiya: sendData va HTTP yo'li bir xil natija beradi;
- /start -> til: pastki klaviatura olib tashlanadi, menyu tugmasi, salomlashuv ostida inline tugma; /til.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import orderapi  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from handlers import on_lang_chosen, on_web_app_data  # noqa: E402
from test_flow import Env, FakeBot, good, make_message, order_button  # noqa: E402
from test_orderapi import OTHER_TOKEN, make_init_data  # noqa: E402
from test_server import FAKE_TOKEN, SECRET  # noqa: E402

business.is_open_now = lambda: True
USER = 5_123_456_789
OTHER = 77
ORIGIN = "https://example.pages.dev"  # test_flow.make_config dagi WEBAPP_URL origin'i
CFG_TOKEN = "1:x"                      # test_flow.make_config dagi bot_token


def init_data_for(user_id: int, **kw) -> str:
    """Env konfiguratsiyasidagi bot tokeni bilan imzolangan initData (token= berilsa - boshqa token)."""
    kw.setdefault("token", CFG_TOKEN)
    return make_init_data(user_id, **kw)


def run(coro):
    return asyncio.run(coro)


def payload(**over) -> dict:
    data = good(ref=secrets.token_hex(16), lang="uz")
    data.update(over)
    return data


async def with_api(fn, *, env: Env | None = None, ready: bool = True, bot=None):
    env = env or Env()
    if env.db._b is None:
        await env.db.connect()
    bot = bot or FakeBot()
    ev = asyncio.Event()
    if ready:
        ev.set()
    submit = orderapi.OrderSubmitAPI(bot=bot, config=env.config, menu_store=env.menu_store, db=env.db,
                                     limiter=env.limiter, ready=ev)
    status = orderapi.OrderStatusAPI(db=env.db, bot_token=env.config.bot_token, allowed_origin=ORIGIN, ready=ev)
    status.ref_limiter = orderapi.WindowLimiter(100, 2.0)  # testda bir ref'ni ketma-ket so'raymiz
    real_bot = Bot(FAKE_TOKEN)
    app = webhook.build_app(Dispatcher(), real_bot, SECRET, ready=ev, api=[status, submit])
    try:
        async with TestClient(TestServer(app)) as client:
            return await fn(client, env, bot, submit)
    finally:
        await real_bot.session.close()
        await env.db.close()


async def post(client, body, *, init_data: str | None = None, origin: str | None = ORIGIN,
               content_type: str = "application/json"):
    headers = {"Content-Type": content_type}
    if init_data is not None:
        headers["Authorization"] = f"tma {init_data}"
    if origin:
        headers["Origin"] = origin
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    resp = await client.post(orderapi.ORDERS_PATH, data=data, headers=headers)
    text = await resp.text()
    return resp.status, (json.loads(text) if text.startswith("{") else text), resp


async def count_orders(env) -> int:
    return (await (await env.db.conn.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]


# ================================================================ autentifikatsiya

def test_init_data_required_valid_and_fresh():
    async def fn(client, env, bot, _api):
        p = payload()
        out = {
            "empty": (await post(client, p, init_data=""))[:2],
            "missing": (await post(client, p))[:2],
            "broken": (await post(client, p, init_data=init_data_for(USER, tamper={"user": json.dumps({"id": 1})})))[:2],
            "other_bot": (await post(client, p, init_data=init_data_for(USER, token=OTHER_TOKEN)))[:2],
            "expired": (await post(client, p, init_data=init_data_for(USER, auth_date=int(time.time()) - 90000)))[:2],
        }
        out["count_after_bad"] = await count_orders(env)
        out["ok"] = (await post(client, p, init_data=init_data_for(USER)))[:2]
        return out

    out = run(with_api(fn))
    for key in ("empty", "missing", "broken", "other_bot", "expired"):
        assert out[key] == (401, {"error": "unauthorized"}), key
    assert out["count_after_bad"] == 0
    assert out["ok"] == (200, {"status": "accepted", "n": 1, "reason": None})


def test_user_id_only_from_init_data():
    async def fn(client, env, bot, _api):
        p = payload(user_id=OTHER, from_user={"id": OTHER})  # tanadagi user_id e'tiborsiz
        status, body, _ = await post(client, p, init_data=init_data_for(USER))
        row = await (await env.db.conn.execute("SELECT user_id FROM orders")).fetchone()
        to_user = [s for s in bot.sent if s[0] == "message" and s[1] == USER]
        to_other = [s for s in bot.sent if s[0] == "message" and s[1] == OTHER]
        return status, body, row[0], len(to_user), len(to_other)

    status, body, saved_user, to_user, to_other = run(with_api(fn))
    assert status == 200 and body["status"] == "accepted"
    assert saved_user == USER and to_user >= 2 and to_other == 0


# ================================================================ idempotentlik

def test_same_ref_twice_and_parallel_one_order():
    async def fn(client, env, bot, _api):
        p = payload()
        init = init_data_for(USER)
        first = (await post(client, p, init_data=init))[:2]
        sent_after_first = len(bot.sent)
        second = (await post(client, p, init_data=init))[:2]
        sent_after_second = len(bot.sent)
        # parallel ikkita bir xil so'rov (yangi ref)
        q = payload()
        env.limiter.release(USER)
        a, b = await asyncio.gather(post(client, q, init_data=init), post(client, q, init_data=init))
        rows = await (await env.db.conn.execute(
            "SELECT client_ref, COUNT(*) FROM orders GROUP BY client_ref")).fetchall()
        idx = await (await env.db.conn.execute("PRAGMA index_list(orders)")).fetchall()
        return first, second, sent_after_first, sent_after_second, a[:2], b[:2], rows, idx

    first, second, s1, s2, a, b, rows, idx = run(with_api(fn))
    assert first == second == (200, {"status": "accepted", "n": 1, "reason": None})
    assert s1 == s2, "takror so'rovda mijozga ham, admin'ga ham qayta xabar yo'q"
    assert a == b and a[1]["status"] == "accepted" and a[1]["n"] == 2, (a, b)
    assert sorted(c for _, c in rows) == [1, 1], "har ref - bitta buyurtma"
    assert any(r[1] == "uq_orders_user_ref" and r[2] == 1 for r in idx), "unique indeks (user_id, client_ref)"


def test_unique_index_skipped_for_old_duplicates_without_crash():
    import sqlite3
    import tempfile

    path = Path(tempfile.mkdtemp()) / "dup.db"

    async def make():
        from db import Database
        db = Database(path)
        await db.connect()
        await db.conn.execute("DROP INDEX uq_orders_user_ref")
        for _ in range(2):
            await db.conn.execute(
                "INSERT INTO orders (user_id, customer_name, phone, address_type, total, created_at, client_ref) "
                "VALUES (1, 'A', '+998901112233', 'text', 1, '2026-01-01T00:00:00+00:00', 'dupref01')")
        await db.conn.commit()
        await db.close()
        db2 = Database(path)  # qayta ishga tushish - yiqilmasligi kerak
        await db2.connect()
        await db2.close()

    logging.disable(logging.CRITICAL)
    try:
        run(make())
    finally:
        logging.disable(logging.NOTSET)
    con = sqlite3.connect(path)
    names = [r[1] for r in con.execute("PRAGMA index_list(orders)")]
    con.close()
    assert "uq_orders_user_ref" not in names and "idx_orders_ref" in names


# ================================================================ chegaralar va noto'g'ri so'rovlar

def test_size_items_qty_and_bad_json():
    async def fn(client, env, bot, api):
        init = init_data_for(USER)
        out = {}
        out["too_big_body"] = (await post(client, '{"ref":"' + "a" * 25000 + '"}', init_data=init))[:2]
        out["bad_json"] = (await post(client, "{not json", init_data=init))[:2]
        out["array"] = (await post(client, "[1,2]", init_data=init))[:2]
        out["no_ref"] = (await post(client, {k: v for k, v in payload().items() if k != "ref"}, init_data=init))[:2]
        out["form"] = (await post(client, "a=1", init_data=init, content_type="application/x-www-form-urlencoded"))[:2]
        api.user_limiter = orderapi.WindowLimiter(100, 60.0)
        many = payload(items=[{"id": "burger-1", "qty": 1}] * 31)
        out["lines"] = (await post(client, many, init_data=init))[:2]
        out["qty"] = (await post(client, payload(items=[{"id": "burger-1", "qty": 51}]), init_data=init))[:2]
        out["payload_4k"] = (await post(client, payload(comment="x" * 5000), init_data=init))[:2]
        out["count"] = await count_orders(env)
        return out

    out = run(with_api(fn))
    assert out["too_big_body"] == (413, {"error": "too_large"})
    for key in ("bad_json", "array", "no_ref", "form"):
        assert out[key] == (400, {"error": "bad_request"}), key
    for key in ("lines", "qty", "payload_4k"):
        assert out[key][0] == 200 and out[key][1]["status"] == "rejected" and out[key][1]["reason"] == "data", key
    assert out["count"] == 0


def test_closed_hours_and_location_rejected_with_inline_button():
    async def fn(client, env, bot, _api):
        init = init_data_for(USER)
        business.is_open_now = lambda: False
        try:
            closed = (await post(client, payload(), init_data=init))[:2]
        finally:
            business.is_open_now = lambda: True
        area = (await post(client, payload(lat=41.2995, lon=69.2401), init_data=init))[:2]
        msgs = [s for s in bot.sent if s[0] == "message" and s[1] == USER]
        return closed, area, msgs, await count_orders(env)

    logging.disable(logging.CRITICAL)
    try:
        closed, area, msgs, count = run(with_api(fn))
    finally:
        logging.disable(logging.NOTSET)
    assert closed == (200, {"status": "rejected", "n": None, "reason": "closed"})
    assert area == (200, {"status": "rejected", "n": None, "reason": "area"})
    assert count == 0
    assert msgs[0][2].startswith("⛔") and order_button(msgs[0][3]["reply_markup"]).text == "Buyurtma berish"
    assert "Samarqand" in msgs[1][2]


def test_rate_limit_and_not_ready():
    async def fn(client, env, bot, _api):
        init = init_data_for(USER)
        codes = [(await post(client, "{bad", init_data=init))[0] for _ in range(8)]
        return codes

    codes = run(with_api(fn))
    assert codes[:6] == [400] * 6 and codes[6:] == [429, 429], codes

    async def fn2(client, env, bot, _api):
        return (await post(client, payload(), init_data=init_data_for(USER)))[:2]

    assert run(with_api(fn2, ready=False)) == (503, {"error": "unavailable"})


def test_cors_post_and_options():
    async def fn(client, env, bot, _api):
        out = {}
        pre = await client.options(orderapi.ORDERS_PATH, headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        out["pre"] = (pre.status, dict(pre.headers))
        out["pre_get"] = (await client.options(orderapi.ORDERS_PATH, headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "GET"})).status
        out["pre_evil"] = (await client.options(orderapi.ORDERS_PATH, headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})).status
        status, body, resp = await post(client, payload(), init_data=init_data_for(USER))
        out["post"] = (status, resp.headers.get("Access-Control-Allow-Origin"))
        out["evil"] = (await post(client, payload(), init_data=init_data_for(USER), origin="https://evil.example"))[:2]
        out["get"] = (await client.get(orderapi.ORDERS_PATH)).status
        return out

    out = run(with_api(fn))
    status, headers = out["pre"]
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Methods"] == "POST"
    assert headers["Access-Control-Allow-Headers"] == "Authorization, Content-Type"
    assert out["pre_get"] == 403 and out["pre_evil"] == 403
    assert out["post"] == (200, ORIGIN) and out["evil"] == (403, {"error": "forbidden"}) and out["get"] == 405


def test_status_endpoint_works_with_init_data_after_post():
    async def fn(client, env, bot, _api):
        p = payload()
        await post(client, p, init_data=init_data_for(USER))
        own = await client.get(f"{orderapi.STATUS_PATH}?ref={p['ref']}",
                               headers={"Origin": ORIGIN, "Authorization": f"tma {init_data_for(USER)}"})
        foreign = await client.get(f"{orderapi.STATUS_PATH}?ref={p['ref']}",
                                   headers={"Origin": ORIGIN, "CF-Connecting-IP": "1.1.1.1",
                                            "Authorization": f"tma {init_data_for(OTHER)}"})
        return await own.json(), await foreign.json()

    own, foreign = run(with_api(fn))
    assert own == {"status": "accepted", "n": 1, "reason": None} and foreign["status"] == "unknown"


def test_logs_have_no_init_data_token_phone_address():
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    async def fn(client, env, bot, _api):
        init = init_data_for(USER)
        await post(client, payload(address_type="text", address_text="Registon ko'chasi 7", lat=None, lon=None),
                   init_data=init)
        await post(client, "{bad", init_data=init)
        await post(client, payload(phone="+99890"), init_data=init)
        return init

    root = logging.getLogger()
    handler = Grab(logging.DEBUG)
    root.addHandler(handler)
    level, disabled = root.level, logging.root.manager.disable
    root.setLevel(logging.INFO)  # prod darajasi (main.setup_logging)
    logging.disable(logging.NOTSET)
    try:
        init = run(with_api(fn))
    finally:
        logging.disable(disabled)
        root.setLevel(level)
        root.removeHandler(handler)
    text = "\n".join(records)
    assert "qabul qilindi" in text
    for leaked in (init[:40], "hash=", FAKE_TOKEN, "901234567", "Registon", "Aziz"):
        assert leaked not in text, leaked


# ================================================================ umumiy funksiya: ikki yo'l bir xil

def test_send_data_and_http_paths_give_same_result():
    def strip(rows):
        # username: sendData'da Telegram xabaridan, HTTP'da initData'dan (sinovdagi initData'da yo'q) - farq kutilgan
        return [tuple(v for k, v in r.items() if k not in ("created_at", "admin_msg_id", "username")) for r in rows]

    async def via_send_data(p):
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, answers = make_message(USER, p)
        await on_web_app_data(msg, **env.deps(bot), lang="uz")
        rows = [dict(o) for o, _ in await env.db.orders_with_items()]
        result = await env.db.order_status(p["ref"], user_id=USER)
        await env.db.close()
        return result, strip(rows), [a[0] for a in answers], [s[2] for s in bot.sent if s[0] == "message" and s[1] == 999]

    async def via_http(p):
        async def fn(client, env, bot, _api):
            status, body, _ = await post(client, p, init_data=init_data_for(USER))
            rows = [dict(o) for o, _ in await env.db.orders_with_items()]
            texts = [s[2] for s in bot.sent if s[0] == "message" and s[1] == USER]
            return body, strip(rows), texts, [s[2] for s in bot.sent if s[0] == "message" and s[1] == 999]
        return await with_api(fn)

    for p in (payload(), payload(lat=41.2995, lon=69.2401), payload(items=[{"id": "yo'q", "qty": 1}])):
        r1, rows1, texts1, admin1 = run(via_send_data(p))
        r2, rows2, texts2, admin2 = run(via_http(p))
        assert r1["status"] == r2["status"] and r1["reason"] == r2["reason"] and r1["n"] == r2["n"], (r1, r2)
        assert rows1 == rows2, "bazadagi buyurtma bir xil"
        assert texts1 == texts2, (texts1, texts2)
        assert [a[:30] for a in admin1] == [a[:30] for a in admin2]


# ================================================================ interfeys: /start, /til

class UiBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.photos: list = []

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, caption, reply_markup))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="F")])


def callback(user_id: int, data: str):
    async def edit_text(text, **kw):
        return None

    async def answer(*a, **kw):
        return None

    return SimpleNamespace(from_user=SimpleNamespace(id=user_id, first_name="Ali"), data=data,
                           message=SimpleNamespace(edit_text=edit_text), answer=answer)


def test_start_and_til_inline_button_menu_button_and_keyboard_removed():
    async def go():
        env = Env()
        await env.db.connect()
        bot = UiBot()
        await on_lang_chosen(callback(USER, "lang:ru:w"), bot, env.config, env.db, lang=None)    # /start
        after_start = (list(bot.kb_removed), list(bot.menu_buttons), list(bot.photos), list(bot.deleted))
        await on_lang_chosen(callback(USER, "lang:uz"), bot, env.config, env.db, lang="ru")      # /til
        til_msg = [s for s in bot.sent if s[0] == "message" and s[1] == USER][-1]
        await env.db.close()
        return after_start, bot.menu_buttons, til_msg, bot.kb_removed

    (removed, menus, photos, deleted), all_menus, til_msg, all_removed = run(go())
    assert removed == [USER] and deleted, "pastki klaviatura olib tashlandi (yordamchi xabar o'chirildi)"
    chat_id, menu = menus[0]
    assert chat_id == USER and menu.type == "web_app" and menu.text == "Заказ"
    assert parse_qs(urlparse(menu.web_app.url).query) == {"lang": ["ru"]}
    _, caption, markup = photos[0]
    assert caption.startswith("Здравствуйте")
    button = order_button(markup)
    assert button.text == "Сделать заказ" and parse_qs(urlparse(button.web_app.url).query) == {"lang": ["ru"]}
    # /til: menyu tugmasi yangi tilda, tasdiq ostida inline tugma
    assert all_menus[-1][1].text == "Buyurtma" and parse_qs(urlparse(all_menus[-1][1].web_app.url).query) == {"lang": ["uz"]}
    assert til_msg[2].startswith("✅ Til o'zgartirildi") and order_button(til_msg[3]["reply_markup"]).text == "Buyurtma berish"
    assert all_removed == [USER, USER]


def test_old_keyboard_order_still_works_and_migrates_customer():
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, answers = make_message(USER, good())
        await on_web_app_data(msg, **env.deps(bot), lang="uz")
        await env.db.close()
        return answers, bot

    answers, bot = run(go())
    assert answers[0][0].startswith("✅") and order_button(answers[0][1]).text == "Buyurtma berish"
    assert answers[1][1].inline_keyboard[0][0].callback_data == "cancel:1", "bekor qilish tugmasi saqlandi"
    assert bot.kb_removed == [USER] and bot.menu_buttons and bot.menu_buttons[0][1].text == "Buyurtma"


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
