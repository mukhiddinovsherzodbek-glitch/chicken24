"""POST /api/web-orders (chicken24.uz sayt buyurtmasi): origin, Turnstile, IP cheklovi, manfiy user_id,
admin cheki "sayt" belgisi, mijozga Telegram xabari yuborilmasligi, idempotentlik, rad etish matni."""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import webapi  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from test_flow import Env, FakeBot, good  # noqa: E402

ORIGIN = "https://chicken24.uz"
PHONE = "+998901234567"


def run(coro):
    return asyncio.run(coro)


def payload(**over) -> dict:
    data = good(ref=secrets.token_hex(16), lang="uz")
    data.update(over)
    return data


async def with_api(fn, *, secret: str = "", verify=None, open_now: bool = True):
    business.is_open_now = lambda: open_now
    env = Env()
    await env.db.connect()
    bot = FakeBot()
    api = webapi.WebOrderAPI(bot=bot, config=env.config, menu_store=env.menu_store, db=env.db,
                             limiter=env.limiter, origins=frozenset({ORIGIN}), turnstile_secret=secret,
                             verify_captcha=verify)
    app = web.Application()
    api.register(app)
    try:
        async with TestClient(TestServer(app)) as client:
            return await fn(client, env, bot, api)
    finally:
        await env.db.close()


async def post(client, body, origin=ORIGIN):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    resp = await client.post(webapi.WEB_ORDERS_PATH, data=json.dumps(body), headers=headers)
    return resp.status, await resp.json(), resp


def test_accepted_negative_user_and_receipt_to_admin_only():
    async def go(client, env, bot, api):
        status, body, resp = await post(client, payload())
        assert status == 200 and body["status"] == "accepted" and body["n"] and body["message"] is None
        assert resp.headers["Access-Control-Allow-Origin"] == ORIGIN
        row = await env.db._db.fetchone("SELECT user_id FROM orders")
        assert row[0] == webapi.web_user_id(PHONE) < 0
        assert {m[1] for m in bot.sent} == {env.config.admin_id}  # mijozga (manfiy id) hech narsa yuborilmagan
        texts = [m[2] for m in bot.sent if isinstance(m[2], str)]
        assert any("sayt" in t for t in texts)
        assert not any("tg://user" in t for t in texts)
    run(with_api(go))


def test_idempotent_same_ref():
    async def go(client, env, bot, api):
        data = payload()
        _, first, _ = await post(client, data)
        _, second, _ = await post(client, data)
        assert first["n"] == second["n"] and second["status"] == "accepted"
        assert (await env.db._db.fetchone("SELECT COUNT(*) FROM orders"))[0] == 1
    run(with_api(go))


def test_closed_returns_message():
    async def go(client, env, bot, api):
        status, body, _ = await post(client, payload())
        assert status == 200 and body["status"] == "rejected" and body["reason"] == "closed"
        assert body["message"] and "<" not in body["message"]
    run(with_api(go, open_now=False))


def test_bad_phone_and_bad_input():
    async def go(client, env, bot, api):
        _, body, _ = await post(client, payload(phone="12345"))
        assert body["status"] == "rejected" and body["reason"] == "val.phone"
        status, _, _ = await post(client, payload(ref="!"))
        assert status == 400
    run(with_api(go))


def test_origin_and_preflight():
    async def go(client, env, bot, api):
        status, _, _ = await post(client, payload(), origin="https://evil.example")
        assert status == 403
        status, _, _ = await post(client, payload(), origin=None)
        assert status == 403
        ok = await client.options(webapi.WEB_ORDERS_PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        assert ok.status == 204 and ok.headers["Access-Control-Allow-Origin"] == ORIGIN
        bad = await client.options(webapi.WEB_ORDERS_PATH, headers={"Origin": "https://evil.example"})
        assert bad.status == 403
    run(with_api(go))


def test_turnstile_required_when_secret_set():
    seen = []

    async def verify(token, ip):
        seen.append(token)
        return token == "good"

    async def go(client, env, bot, api):
        status, body, _ = await post(client, payload())
        assert status == 403 and body["error"] == "captcha"
        status, body, _ = await post(client, payload(cf_token="bad"))
        assert status == 403
        status, body, _ = await post(client, payload(cf_token="good"))
        assert status == 200 and body["status"] == "accepted"
        assert (await env.db._db.fetchone("SELECT COUNT(*) FROM orders"))[0] == 1
    run(with_api(go, secret="s", verify=verify))


def test_ip_limit():
    async def go(client, env, bot, api):
        codes = [(await post(client, payload(phone=f"+99890123456{i}")))[0] for i in range(webapi.IP_LIMIT + 1)]
        assert codes[:webapi.IP_LIMIT] == [200] * webapi.IP_LIMIT and codes[-1] == 429
    run(with_api(go))


def test_plain_text_and_user_id():
    assert webapi.plain_text("<b>Yopiq</b> &amp; ok") == "Yopiq & ok"
    assert webapi.web_user_id("+998901234567") == -998901234567


async def get(client, path, origin=ORIGIN):
    resp = await client.get(path, headers={"Origin": origin} if origin else {})
    return resp.status, await resp.json(), resp


def test_web_config_and_quote():
    async def go(client, env, bot, api):
        status, body, resp = await get(client, webapi.WEB_CONFIG_PATH)
        assert status == 200 and set(body) == {"base_km", "base_fee", "extra_per_km"}
        assert resp.headers["Access-Control-Allow-Origin"] == ORIGIN
        lat, lon = api._delivery.origin
        status, near, _ = await get(client, f"{webapi.WEB_QUOTE_PATH}?lat={lat}&lon={lon}")
        assert status == 200 and near["fee"] == body["base_fee"] and near["km"] <= 0.1
        status, far, _ = await get(client, f"{webapi.WEB_QUOTE_PATH}?lat={lat + 0.1}&lon={lon}")  # ~11 km
        assert status == 200 and far["fee"] > near["fee"] and far["km"] > body["base_km"]
        assert (far["fee"] - body["base_fee"]) % body["extra_per_km"] == 0
        for bad in ("", "?lat=abc&lon=1", "?lat=95&lon=1", "?lat=1"):
            status, _, _ = await get(client, webapi.WEB_QUOTE_PATH + bad)
            assert status == 400
        status, _, _ = await get(client, f"{webapi.WEB_QUOTE_PATH}?lat={lat}&lon={lon}", origin="https://evil.example")
        assert status == 403
        status, _, _ = await get(client, webapi.WEB_CONFIG_PATH, origin=None)
        assert status == 403
        ok = await client.options(webapi.WEB_QUOTE_PATH, headers={"Origin": ORIGIN})
        assert ok.status == 204 and ok.headers["Access-Control-Allow-Methods"] == "GET"
    run(with_api(go))


def test_web_quote_rate_limit():
    async def go(client, env, bot, api):
        lat, lon = api._delivery.origin
        codes = [(await get(client, f"{webapi.WEB_QUOTE_PATH}?lat={lat}&lon={lon}"))[0]
                 for _ in range(webapi.QUOTE_LIMIT + 1)]
        assert codes[:webapi.QUOTE_LIMIT] == [200] * webapi.QUOTE_LIMIT and codes[-1] == 429
    run(with_api(go))
