"""Mini App buyurtma holati API testlari: GET /api/order-status (orderapi.py).

- initData: to'g'ri / buzilgan / boshqa token / eskirgan / kelajakdagi / takroriy maydon / hash'siz;
- foydalanuvchi faqat O'Z buyurtmasini ko'radi (user_id initData dan, so'rovdan emas);
- javobda faqat status / n / reason - ism, telefon, manzil yo'q;
- CORS: faqat Mini App origin'i, OPTIONS preflight;
- so'rov cheklovi (2 soniyada 1 ta, 429 + Retry-After);
- baza tayyor emas / ulanmagan - 503, /health baribir 200; logda initData va token yo'q.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import orderapi  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.utils.web_app import check_webapp_signature  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from handlers import on_cancel_order, on_web_app_data  # noqa: E402
from test_flow import Env, FakeBot, FakeCallback, good, make_message  # noqa: E402
from test_server import FAKE_TOKEN, SECRET  # noqa: E402

business.is_open_now = lambda: True

ORIGIN = "https://chicken24.pages.dev"
OTHER_TOKEN = "987654321:" + "B" * 35
USER_A, USER_B = 5_123_456_789, 77


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


def auth(init_data: str, origin: str | None = ORIGIN) -> dict:
    headers = {"Authorization": f"tma {init_data}"}
    if origin:
        headers["Origin"] = origin
    return headers


# ================================================================ initData tekshiruvi

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
    expect_error("")
    expect_error("x" * 5000)
    expect_error("%%%")
    expect_error(make_init_data(USER_A, extra={"user": "not json"}))
    # chegarada: 23 soat 59 daqiqa - hali amal qiladi
    assert orderapi.validate_init_data(make_init_data(USER_A, auth_date=now - 24 * 3600 + 60), FAKE_TOKEN) == USER_A


# ================================================================ API (haqiqiy SQLite baza)

async def make_env():
    env = Env()
    await env.db.connect()
    bot = FakeBot()
    await on_web_app_data(make_message(USER_A, good(ref="aaaaok01"))[0], **env.deps(bot), lang="uz")      # #1 qabul
    env.limiter.release(USER_A)
    await on_web_app_data(make_message(USER_A, good(ref="aaarej02", lat=41.2995, lon=69.2401))[0],
                          **env.deps(bot), lang="uz")                                                       # rad (hudud)
    await on_web_app_data(make_message(USER_B, good(ref="bbbbcx03"))[0], **env.deps(bot), lang="ru")      # #2
    await on_cancel_order(FakeCallback(USER_B, "cancel:2"), bot, env.config, env.db, lang="ru")             # bekor
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
    limiter = orderapi.RateLimiter(clock=clock or Clock())
    api = orderapi.OrderStatusAPI(db=db or env.db, bot_token=FAKE_TOKEN, allowed_origin=ORIGIN + "/",
                                  ready=ready, limiter=limiter)
    bot = Bot(FAKE_TOKEN)
    app = webhook.build_app(Dispatcher(), bot, SECRET, ready=ready, api=api)
    try:
        async with TestClient(TestServer(app)) as client:
            return await fn(client, limiter)
    finally:
        await bot.session.close()


async def get(client, ref, init_data, origin=ORIGIN):
    resp = await client.get(f"{orderapi.STATUS_PATH}?ref={ref}", headers=auth(init_data, origin))
    body = await resp.text()
    return resp, body


def test_owner_sees_own_statuses_without_personal_data():
    async def go():
        env = await make_env()
        clock = Clock()

        async def fn(client, _limiter):
            out = {}
            for ref, user in (("aaaaok01", USER_A), ("aaarej02", USER_A), ("bbbbcx03", USER_B), ("zzzzzz99", USER_A)):
                clock.t += 3
                resp, body = await get(client, ref, make_init_data(user))
                out[ref] = (resp.status, json.loads(body), body, dict(resp.headers))
            return out

        out = await with_client(env, fn, clock=clock)
        await env.db.close()
        return out

    out = run(go())
    assert out["aaaaok01"][:2] == (200, {"status": "accepted", "n": 1, "reason": None})
    assert out["aaarej02"][:2] == (200, {"status": "rejected", "n": None, "reason": "area"})
    assert out["bbbbcx03"][:2] == (200, {"status": "cancelled", "n": 2, "reason": None})
    assert out["zzzzzz99"][:2] == (200, {"status": "unknown", "n": None, "reason": None})
    for status, data, body, headers in out.values():
        assert set(data) == {"status", "n", "reason"}
        for private in ("Aziz", "+998", "901234567", "Registon", "lat", "lon", "phone", "name", "address"):
            assert private not in body, private
        assert headers["Cache-Control"] == "no-store" and headers["Access-Control-Allow-Origin"] == ORIGIN


def test_other_user_cannot_see_foreign_order():
    async def go():
        env = await make_env()
        clock = Clock()

        async def fn(client, _limiter):
            res = []
            # B foydalanuvchi A ning ref'larini so'raydi (ref ma'lum bo'lsa ham)
            for ref in ("aaaaok01", "aaarej02"):
                clock.t += 3
                resp, body = await get(client, ref, make_init_data(USER_B))
                res.append((resp.status, json.loads(body)))
            # so'rovda boshqa user_id berib aldash - e'tiborga olinmaydi
            clock.t += 3
            resp = await client.get(f"{orderapi.STATUS_PATH}?ref=aaaaok01&user_id={USER_A}",
                                    headers=auth(make_init_data(USER_B)))
            res.append((resp.status, await resp.json()))
            return res

        out = await with_client(env, fn, clock=clock)
        await env.db.close()
        return out

    for status, data in run(go()):
        assert status == 200 and data == {"status": "unknown", "n": None, "reason": None}


def test_auth_and_input_errors_are_generic():
    async def go():
        env = await make_env()
        clock = Clock()

        async def fn(client, _limiter):
            cases = {
                "no_auth": await client.get(f"{orderapi.STATUS_PATH}?ref=aaaaok01", headers={"Origin": ORIGIN}),
                "tampered": (await get(client, "aaaaok01",
                                       make_init_data(USER_B, tamper={"user": json.dumps({"id": USER_A})})))[0],
                "expired": (await get(client, "aaaaok01", make_init_data(USER_A, auth_date=int(time.time()) - 90000)))[0],
            }
            clock.t += 3
            cases["bad_ref"] = (await get(client, "AAA'--", make_init_data(USER_A)))[0]
            clock.t += 3
            cases["x_header"] = await client.get(f"{orderapi.STATUS_PATH}?ref=aaaaok01",
                                                 headers={"X-Telegram-Init-Data": make_init_data(USER_A)})
            return {k: (r.status, await r.text()) for k, r in cases.items()}

        out = await with_client(env, fn, clock=clock)
        await env.db.close()
        return out

    out = run(go())
    assert out["no_auth"] == (401, '{"error": "unauthorized"}')
    assert out["tampered"][0] == 401 and out["expired"][0] == 401
    assert out["bad_ref"] == (400, '{"error": "bad_request"}')
    assert out["x_header"][0] == 200, "X-Telegram-Init-Data sarlavhasi ham qabul qilinadi"


def test_rate_limit_per_user():
    async def go():
        env = await make_env()
        clock = Clock()

        async def fn(client, _limiter):
            a = make_init_data(USER_A)
            first = (await get(client, "aaaaok01", a))[0].status
            second, _ = await get(client, "aaaaok01", a)
            other_user = (await get(client, "bbbbcx03", make_init_data(USER_B)))[0].status
            clock.t += 2.1
            third = (await get(client, "aaaaok01", a))[0].status
            return first, second.status, second.headers.get("Retry-After"), other_user, third

        out = await with_client(env, fn, clock=clock)
        await env.db.close()
        return out

    first, second, retry_after, other_user, third = run(go())
    assert first == 200 and second == 429 and retry_after in ("1", "2")
    assert other_user == 200, "cheklov foydalanuvchi boshiga"
    assert third == 200


def test_cors_preflight_and_origin():
    async def go():
        env = await make_env()
        clock = Clock()

        async def fn(client, _limiter):
            out = {}
            pre = await client.options(orderapi.STATUS_PATH, headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization"})
            out["pre"] = (pre.status, dict(pre.headers))
            out["pre_evil"] = (await client.options(orderapi.STATUS_PATH, headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})).status
            out["pre_post"] = (await client.options(orderapi.STATUS_PATH, headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "POST"})).status
            evil, _ = await get(client, "aaaaok01", make_init_data(USER_A), origin="https://evil.example")
            out["evil"] = (evil.status, evil.headers.get("Access-Control-Allow-Origin"))
            preview, _ = await get(client, "aaaaok01", make_init_data(USER_A), origin="https://abc.chicken24.pages.dev")
            out["preview"] = preview.status
            clock.t += 3
            no_origin, _ = await get(client, "aaaaok01", make_init_data(USER_A), origin=None)
            out["no_origin"] = (no_origin.status, no_origin.headers.get("Access-Control-Allow-Origin"))
            return out

        out = await with_client(env, fn, clock=clock)
        await env.db.close()
        return out

    out = run(go())
    status, headers = out["pre"]
    assert status == 204 and headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "GET" in headers["Access-Control-Allow-Methods"]
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    assert out["pre_evil"] == 403 and out["pre_post"] == 403
    assert out["evil"] == (403, None) and out["preview"] == 403
    assert out["no_origin"] == (200, None), "Origin'siz (masalan curl) - CORS sarlavhasi yo'q, lekin initData kerak"


def test_db_unavailable_gives_503_and_health_still_ok():
    class BrokenDB:
        async def order_status(self, user_id, ref):
            raise RuntimeError("Baza ulanmagan: postgresql://u:TopSecret@h/db " + FAKE_TOKEN)

    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    async def go():
        env = Env()
        clock = Clock()

        async def not_ready(client, _limiter):
            resp, body = await get(client, "aaaaok01", make_init_data(USER_A))
            health = await client.get("/health")
            return resp.status, body, health.status

        async def broken(client, _limiter):
            init = make_init_data(USER_A)
            resp, body = await get(client, "aaaaok01", init)
            health = await client.get("/health")
            return resp.status, body, health.status, init

        a = await with_client(env, not_ready, ready_set=False, clock=clock)
        b = await with_client(env, broken, db=BrokenDB(), clock=Clock())
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
    assert b[:3] == (503, '{"error": "unavailable"}', 200)
    init = b[3]
    text = "\n".join(records)
    assert "RuntimeError" in text
    for leaked in ("TopSecret", FAKE_TOKEN, init[:30], "hash="):
        assert leaked not in text, leaked


def test_order_status_db_method_prefers_newer_record():
    """Bitta ref avval rad etilib, keyin qayta yuborilib qabul qilinsa - qabul (keyingisi)."""
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        await on_web_app_data(make_message(9, good(ref="samerf01", lat=41.2995, lon=69.2401))[0], **env.deps(bot))
        env.limiter.release(9)
        before = await env.db.order_status(9, "samerf01")
        await on_web_app_data(make_message(9, good(ref="samerf01"))[0], **env.deps(bot))
        after = await env.db.order_status(9, "samerf01")
        foreign = await env.db.order_status(10, "samerf01")
        await env.db.close()
        return before, after, foreign

    before, after, foreign = run(go())
    assert before == {"status": "rejected", "n": None, "reason": "area"}
    assert after == {"status": "accepted", "n": 1, "reason": None}
    assert foreign["status"] == "unknown"


def test_origin_helper():
    assert orderapi.origin_of("https://Chicken24.pages.dev/?lang=uz#x") == "https://chicken24.pages.dev"


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
