"""Mini App buyurtma holati API testlari: GET /api/order-status (orderapi.py).

- initData SHART EMAS (klaviatura tugmasidan ochilgan Mini App'da bo'sh): ref bo'yicha ishlaydi;
  to'g'ri initData kelsa - egasi ham tekshiriladi; noto'g'ri initData e'tiborsiz;
- javobda faqat status / n / reason - ism, telefon, manzil yo'q;
- so'rov cheklovi: har ref'ga 2 s da 1 ta; qisqa (eski) ref'lar uchun IP boshiga daqiqasiga 30 ta -
  taxmin qilib topish to'xtaydi (429); uzun ref'lar 128 bit;
- CORS: faqat GET, faqat Mini App origin'i, OPTIONS preflight;
- baza tayyor emas / ulanmagan - 503, /health baribir 200; logda initData, token, ref yo'q;
- buyurtma oqimi: saqlash -> mijozga tasdiq -> admin'ga chek; admin xatosi buyurtmani buzmaydi;
  webhook handleridagi istisno Telegram'ga 500 qaytarmaydi.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import orderapi  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher, Router  # noqa: E402
from aiogram.types import Message  # noqa: E402
from aiogram.utils.web_app import check_webapp_signature  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from handlers import on_cancel_order, on_web_app_data, resend_if_needed  # noqa: E402
from test_flow import Env, FakeBot, FakeCallback, good, make_message, NO_LOC  # noqa: E402
from test_server import FAKE_TOKEN, SECRET, update_json  # noqa: E402

business.is_open_now = lambda: True

ORIGIN = "https://chicken24.pages.dev"
OTHER_TOKEN = "987654321:" + "B" * 35
USER_A, USER_B = 5_123_456_789, 77
LONG_OK = secrets.token_hex(16)    # yangi Mini App ref'i: 32 hex = 128 bit
LONG_REJ = secrets.token_hex(16)
SHORT_CX = "mf3k2pq8x1a9"          # eski Mini App ref'i (12 belgi)


def run(coro):
    return asyncio.run(coro)


def make_init_data(user_id: int, *, token: str = FAKE_TOKEN, auth_date: int | None = None,
                   extra: dict | None = None, tamper: dict | None = None) -> str:
    """Telegram qoidasi bo'yicha imzolangan initData (tamper - imzodan KEYIN o'zgartiriladi)."""
    fields = {
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Ali", "language_code": "uz"}, separators=(",", ":")),
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "signature": "c2lnbmF0dXJl",  # Bot API 8.0+ maydoni - imzoga kiradi
        **(extra or {}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    fields.update(tamper or {})
    return urlencode(fields)


# ================================================================ initData tekshiruvi (ixtiyoriy egasi tekshiruvi uchun)

def expect_error(init_data: str, **kw) -> None:
    try:
        orderapi.validate_init_data(init_data, FAKE_TOKEN, **kw)
    except orderapi.InitDataError:
        return
    raise AssertionError("InitDataError kutilgan edi")


def test_init_data_valid_and_matches_aiogram():
    data = make_init_data(USER_A)
    assert orderapi.validate_init_data(data, FAKE_TOKEN) == USER_A
    assert check_webapp_signature(FAKE_TOKEN, data) is True, "aiogram bilan bir xil imzo qoidasi"


def test_init_data_rejected_cases():
    now = int(time.time())
    expect_error(make_init_data(USER_A, tamper={"user": json.dumps({"id": USER_B})}))  # boshqa user ID
    expect_error(make_init_data(USER_A, token=OTHER_TOKEN))                               # boshqa bot tokeni
    expect_error(make_init_data(USER_A, auth_date=now - 24 * 3600 - 5))                  # 24 soatdan eski
    expect_error(make_init_data(USER_A, auth_date=now + 3600))                           # kelajakdan
    expect_error(make_init_data(USER_A).replace("&hash=", "&x="))                         # hash yo'q
    expect_error(make_init_data(USER_A) + "&auth_date=1")                                 # takroriy maydon
    expect_error(make_init_data(USER_A, tamper={"hash": "é" * 64}))                       # ASCII emas
    for bad in ("", "x" * 5000, "%%%"):
        expect_error(bad)
    expect_error(make_init_data(USER_A, extra={"user": "not json"}))
    assert orderapi.validate_init_data(make_init_data(USER_A, auth_date=now - 24 * 3600 + 60), FAKE_TOKEN) == USER_A


# ================================================================ API (haqiqiy SQLite baza)

async def make_env():
    env = Env()
    await env.db.connect()
    bot = FakeBot()
    await on_web_app_data(make_message(USER_A, good(ref=LONG_OK))[0], **env.deps(bot), lang="uz")          # #1
    env.limiter.release(USER_A)
    await on_web_app_data(make_message(USER_A, good(ref=LONG_REJ, **NO_LOC))[0],
                          **env.deps(bot), lang="uz")                                                        # rad
    await on_web_app_data(make_message(USER_B, good(ref=SHORT_CX))[0], **env.deps(bot), lang="ru")         # #2
    await on_cancel_order(FakeCallback(USER_B, "cancel:2"), bot, env.config, env.db, lang="ru")              # bekor
    return env


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def with_client(env, fn, *, ready_set: bool = True, db=None, clock: Clock | None = None):
    ready = asyncio.Event()
    if ready_set:
        ready.set()
    clock = clock or Clock()
    api = orderapi.OrderStatusAPI(db=db or env.db, bot_token=FAKE_TOKEN, allowed_origin=ORIGIN + "/",
                                  ready=ready, monotonic=clock)
    bot = Bot(FAKE_TOKEN)
    app = webhook.build_app(Dispatcher(), bot, SECRET, ready=ready, api=api)
    try:
        async with TestClient(TestServer(app)) as client:
            return await fn(client, clock)
    finally:
        await bot.session.close()


async def get(client, ref, *, init_data: str | None = None, origin: str | None = ORIGIN, ip: str = "203.0.113.5"):
    headers = {"CF-Connecting-IP": ip}
    if origin:
        headers["Origin"] = origin
    if init_data is not None:
        headers["Authorization"] = f"tma {init_data}"
    resp = await client.get(f"{orderapi.STATUS_PATH}?ref={ref}", headers=headers)
    return resp, await resp.text()


def test_empty_init_data_works_by_ref_without_personal_data():
    """Klaviatura tugmasidan ochilgan Mini App: initData bo'sh - baribir ishlaydi (avval 401 edi)."""
    async def go():
        env = await make_env()

        async def fn(client, clock):
            out = {}
            for name, ref, init in (("ok", LONG_OK, None), ("rej", LONG_REJ, ""), ("cx", SHORT_CX, None),
                                    ("unknown", secrets.token_hex(16), None)):
                clock.t += 3
                resp, body = await get(client, ref, init_data=init)
                out[name] = (resp.status, json.loads(body), body, dict(resp.headers))
            return out

        out = await with_client(env, fn)
        await env.db.close()
        return out

    out = run(go())
    assert out["ok"][:2] == (200, {"status": "accepted", "n": 1, "reason": None})
    assert out["rej"][:2] == (200, {"status": "rejected", "n": None, "reason": "location"})
    assert out["cx"][:2] == (200, {"status": "cancelled", "n": 2, "reason": None})
    assert out["unknown"][:2] == (200, {"status": "unknown", "n": None, "reason": None})
    for status, data, body, headers in out.values():
        assert set(data) == {"status", "n", "reason"}
        for private in ("Aziz", "+998", "901234567", "Registon", "lat", "lon", "phone", "name", "address",
                        str(USER_A), str(USER_B)):
            assert private not in body, private
        assert headers["Cache-Control"] == "no-store" and headers["Access-Control-Allow-Origin"] == ORIGIN


def test_valid_init_data_also_checks_owner_invalid_is_ignored():
    async def go():
        env = await make_env()

        async def fn(client, clock):
            res = {}
            for name, init in (("owner", make_init_data(USER_A)), ("other", make_init_data(USER_B)),
                               ("bad_sig", make_init_data(USER_B, token=OTHER_TOKEN)),
                               ("expired", make_init_data(USER_B, auth_date=int(time.time()) - 90000))):
                clock.t += 3
                resp, body = await get(client, LONG_OK, init_data=init)
                res[name] = (resp.status, json.loads(body)["status"])
            clock.t += 3
            resp, body = await get(client, LONG_REJ, init_data=make_init_data(USER_B))
            res["other_rej"] = (resp.status, json.loads(body)["status"])
            return res

        out = await with_client(env, fn)
        await env.db.close()
        return out

    out = run(go())
    assert out["owner"] == (200, "accepted")
    assert out["other"] == (200, "unknown"), "to'g'ri initData boshqa foydalanuvchiniki - buyurtma ko'rsatilmaydi"
    assert out["other_rej"] == (200, "unknown")
    assert out["bad_sig"] == (200, "accepted") and out["expired"] == (200, "accepted"), \
        "noto'g'ri/eskirgan initData rad etilmaydi - e'tiborsiz (ref'ning o'zi sir)"


def test_bad_ref_and_long_ref_format():
    async def go():
        env = await make_env()

        async def fn(client, clock):
            out = {}
            for name, ref in (("sql", "AAA'--"), ("upper", "ABCDEF123456"), ("too_short", "abc12"),
                              ("too_long", "a" * 65), ("empty", "")):
                clock.t += 3
                out[name] = (await get(client, ref))[0].status
            return out

        out = await with_client(env, fn)
        await env.db.close()
        return out

    out = run(go())
    assert set(out.values()) == {400}, out
    assert len(LONG_OK) == 32 and int(LONG_OK, 16) >= 0, "yangi ref - 128 bit"


def test_rate_limit_per_ref():
    async def go():
        env = await make_env()

        async def fn(client, clock):
            first = (await get(client, LONG_OK))[0].status
            second, _ = await get(client, LONG_OK)
            other_ref = (await get(client, LONG_REJ))[0].status
            other_ip = (await get(client, LONG_OK, ip="198.51.100.9"))[0].status  # ref cheklovi IP'dan qat'i nazar
            clock.t += 2.1
            third = (await get(client, LONG_OK))[0].status
            return first, second.status, second.headers.get("Retry-After"), other_ref, other_ip, third

        out = await with_client(env, fn)
        await env.db.close()
        return out

    first, second, retry_after, other_ref, other_ip, third = run(go())
    assert first == 200 and second == 429 and retry_after in ("1", "2")
    assert other_ref == 200 and other_ip == 429 and third == 200


def test_short_refs_cannot_be_guessed_ip_limit():
    """Eski qisqa ref'larni taxmin qilish: bitta IP dan daqiqasiga 30 tadan keyin 429."""
    async def go():
        env = await make_env()

        async def fn(client, clock):
            codes = []
            for _ in range(40):  # har safar boshqa (tasodifiy) qisqa ref - taxmin qilishga urinish
                codes.append((await get(client, secrets.token_hex(6)))[0].status)
            other_ip = (await get(client, secrets.token_hex(6), ip="198.51.100.20"))[0].status
            long_ref_same_ip = (await get(client, LONG_OK))[0].status   # uzun ref'larga bu cheklov tegmaydi
            clock.t += 61
            after_minute = (await get(client, SHORT_CX))[0].status
            return codes, other_ip, long_ref_same_ip, after_minute

        out = await with_client(env, fn)
        await env.db.close()
        return out

    codes, other_ip, long_ref_same_ip, after_minute = run(go())
    assert codes[:30] == [200] * 30 and codes[30:] == [429] * 10, codes
    assert other_ip == 200 and long_ref_same_ip == 200 and after_minute == 200
    # 12 belgili eski ref ([a-z0-9]): 36^12 variant; daqiqasiga 30 ta urinish bilan topish - amalda imkonsiz
    assert 36 ** 12 / (30 * 60 * 24 * 365) > 1e8


def test_long_refs_have_looser_ip_limit():
    async def go():
        env = await make_env()

        async def fn(client, _clock):
            return [(await get(client, secrets.token_hex(16)))[0].status for _ in range(125)]

        out = await with_client(env, fn)
        await env.db.close()
        return out

    codes = run(go())
    assert codes[:120] == [200] * 120 and codes[120:] == [429] * 5


def test_cors_get_only_and_origin():
    async def go():
        env = await make_env()

        async def fn(client, clock):
            out = {}
            pre = await client.options(orderapi.STATUS_PATH, headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization"})
            out["pre"] = (pre.status, dict(pre.headers))
            out["pre_evil"] = (await client.options(orderapi.STATUS_PATH, headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})).status
            out["pre_post"] = (await client.options(orderapi.STATUS_PATH, headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "POST"})).status
            out["post"] = (await client.post(f"{orderapi.STATUS_PATH}?ref={LONG_OK}", headers={"Origin": ORIGIN})).status
            evil, _ = await get(client, LONG_OK, origin="https://evil.example")
            out["evil"] = (evil.status, evil.headers.get("Access-Control-Allow-Origin"))
            out["preview"] = (await get(client, LONG_OK, origin="https://abc.chicken24.pages.dev"))[0].status
            clock.t += 3
            no_origin, _ = await get(client, LONG_OK, origin=None)
            out["no_origin"] = (no_origin.status, no_origin.headers.get("Access-Control-Allow-Origin"))
            return out

        out = await with_client(env, fn)
        await env.db.close()
        return out

    out = run(go())
    status, headers = out["pre"]
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Methods"] == "GET"
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    assert out["pre_evil"] == 403 and out["pre_post"] == 403 and out["post"] == 405
    assert out["evil"] == (403, None) and out["preview"] == 403
    assert out["no_origin"] == (200, None)


def test_db_unavailable_gives_503_and_health_still_ok():
    class BrokenDB:
        async def order_status(self, ref, user_id=None):
            raise RuntimeError("Baza ulanmagan: postgresql://u:TopSecret@h/db " + FAKE_TOKEN)

    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    async def go():
        env = Env()

        async def check(client, _clock):
            resp, body = await get(client, LONG_OK, init_data=make_init_data(USER_A))
            health = await client.get("/health")
            return resp.status, body, health.status

        a = await with_client(env, check, ready_set=False)
        b = await with_client(env, check, db=BrokenDB())
        return a, b

    handler = Grab()
    lg = logging.getLogger("orderapi")
    lg.addHandler(handler)
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    try:
        a, b = run(go())
    finally:
        logging.disable(disabled)
        lg.removeHandler(handler)
    assert a == (503, '{"error": "unavailable"}', 200), "bot tayyor emas - 503, /health 200"
    assert b == (503, '{"error": "unavailable"}', 200)
    text = "\n".join(records)
    assert "RuntimeError" in text
    for leaked in ("TopSecret", FAKE_TOKEN, LONG_OK, "hash=", "auth_date"):
        assert leaked not in text, leaked


def test_order_status_db_method():
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        ref = secrets.token_hex(16)
        await on_web_app_data(make_message(9, good(ref=ref, **NO_LOC))[0], **env.deps(bot))
        env.limiter.release(9)
        before = await env.db.order_status(ref)
        # bir xil ref qayta keldi (takror so'rov) - yangi buyurtma yaratilmaydi, avvalgi natija qoladi
        msg, answers = make_message(9, good(ref=ref))
        await on_web_app_data(msg, **env.deps(bot))
        again = await env.db.order_status(ref)
        count = (await (await env.db.conn.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        # yangi ref - qabul; keyin shu ref'ga eskiroq rad yozuvi - qabul ustun (keyingisi hisoblanadi)
        env.limiter.release(9)
        ref2 = secrets.token_hex(16)
        await on_web_app_data(make_message(9, good(ref=ref2))[0], **env.deps(bot))
        owner = await env.db.order_status(ref2, user_id=9)
        foreign = await env.db.order_status(ref2, user_id=10)
        stored = (await (await env.db.conn.execute("SELECT client_ref FROM orders")).fetchone())[0]
        await env.db.close()
        return before, again, count, answers, owner, foreign, stored, ref2

    before, again, count, answers, owner, foreign, stored, ref2 = run(go())
    assert before == again == {"status": "rejected", "n": None, "reason": "location"}
    assert count == 0 and answers == [], "takror so'rovda yangi buyurtma ham, xabar ham yo'q"
    assert owner == {"status": "accepted", "n": 1, "reason": None}
    assert foreign["status"] == "unknown"
    assert stored == ref2 and len(ref2) == 32, "32 belgili ref bazaga to'liq yoziladi"


def test_origin_and_ip_helpers():
    assert orderapi.origin_of("https://Chicken24.pages.dev/?lang=uz#x") == "https://chicken24.pages.dev"
    req = SimpleNamespace(headers={"CF-Connecting-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9"}, remote="10.0.0.1")
    assert orderapi.client_ip(req) == "1.2.3.4"
    req = SimpleNamespace(headers={"X-Forwarded-For": "5.6.7.8, 10.0.0.2"}, remote="10.0.0.1")
    assert orderapi.client_ip(req) == "5.6.7.8"
    assert orderapi.client_ip(SimpleNamespace(headers={}, remote="10.0.0.1")) == "10.0.0.1"


# ================================================================ buyurtma oqimi: tasdiq -> admin; admin xatosi

class OrderedBot(FakeBot):
    """Mijoz va admin xabarlarini BITTA ro'yxatda tartib bilan yozadi; admin (999) ga yuborish xato beradi."""

    def __init__(self, log: list) -> None:
        super().__init__(fail_admin=True)
        self.log = log

    async def send_message(self, chat_id, text, **kw):
        self.log.append(("admin" if chat_id == 999 else "bot->user", text[:30]))
        return await super().send_message(chat_id, text, **kw)


def test_customer_confirmed_first_admin_failure_keeps_order():
    async def go():
        env = Env()
        await env.db.connect()
        log: list = []
        bot = OrderedBot(log)
        msg, answers = make_message(USER_A, good(ref=LONG_OK))
        original_answer = msg.answer

        async def answer(text, **kw):
            log.append(("user", text[:30]))
            return await original_answer(text, **kw)

        msg.answer = answer
        disabled = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            await on_web_app_data(msg, **env.deps(bot), lang="uz")
        finally:
            logging.disable(disabled)
        row = await (await env.db.conn.execute("SELECT status, notified FROM orders")).fetchone()
        status_now = await env.db.order_status(LONG_OK)
        # admin tuzaldi (to'g'ri ADMIN_ID / /start bosdi) - fon vazifasi yuboradi
        bot.fail_admin = False
        sent = await resend_if_needed(bot, env.config, env.db, min_age_seconds=0)
        notified = (await (await env.db.conn.execute("SELECT notified FROM orders")).fetchone())[0]
        await env.db.close()
        return log, answers, row, status_now, sent, notified

    log, answers, row, status_now, sent, notified = run(go())
    kinds = [k for k, _ in log]
    first_admin = kinds.index("admin")
    assert kinds[0] == "user" and log[0][1].startswith("✅"), "AVVAL mijozga tasdiq"
    assert first_admin > 0, "admin'ga chek tasdiqdan KEYIN"
    assert row == ("new", 0), "admin xatosi buyurtmani bekor qilmaydi, notified=0"
    assert status_now["status"] == "accepted", "Mini App 'qabul qilindi' ko'radi"
    assert any("Qayta buyurtma bermang" in a[0] for a in answers), "mijozga takror bermaslik haqida eslatma"
    assert sent == 1 and notified == 1, "keyinroq qayta yuborildi"


def test_webhook_handler_exception_does_not_return_500():
    async def go():
        bot = Bot(FAKE_TOKEN)
        dp = Dispatcher()
        r = Router()
        calls: list = []

        @r.message()
        async def boom(message: Message) -> None:
            calls.append(message.text)
            raise RuntimeError("handler ichida xato")

        dp.include_router(r)
        ready = asyncio.Event()
        ready.set()
        hdr = {"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET}
        async with TestClient(TestServer(webhook.build_app(dp, bot, SECRET, ready=ready))) as client:
            resp = await client.post(webhook.webhook_path(SECRET), data=json.dumps(update_json("x")), headers=hdr)
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
        await bot.session.close()
        return resp.status, calls

    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        status, calls = run(go())
    finally:
        logging.disable(disabled)
    assert calls == ["x"] and status == 200, "Telegram cheksiz qayta urinmasligi uchun 200"


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
