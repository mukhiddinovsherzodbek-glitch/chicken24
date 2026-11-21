"""Yetkazish narxi (yo'l masofasi bo'yicha) testlari: delivery.py, process_order, API, Excel, migratsiya.

- yaxlitlash va narx chegaralari (Decimal - suzuvchi nuqta xatosiz);
- ORS javobi (mock): to'g'ri masofa, so'rov tuzilmasi ([lon, lat], Authorization), kesh, kunlik hisoblagich;
- ORS xato / timeout / 429 / kalit yo'q - zaxira (haversine * ROAD_FACTOR), "taxminiy" belgisi;
- server narxni qayta hisoblaydi (mijoz summasi e'tiborsiz), lokatsiyasiz buyurtma rad etiladi,
  sendData va POST /api/orders bir xil natija;
- /api/config va /api/delivery-quote; admin cheki, mijoz xabari, Excel ustuni, migratsiya; kalit loglarda yo'q.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import secrets
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import config as config_module  # noqa: E402
import delivery  # noqa: E402
import excel_export  # noqa: E402
import handlers  # noqa: E402
import orderapi  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from db import Database  # noqa: E402
from delivery import DeliveryService, Tariff, delivery_fee, km_to_tenths  # noqa: E402
from handlers import ReceiptData, build_receipt, on_web_app_data  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from test_flow import Env, FakeBot, good, make_message  # noqa: E402
from test_orders_http import ORIGIN, init_data_for, post, with_api  # noqa: E402
from test_server import env as env_vars  # noqa: E402

business.is_open_now = lambda: True
ORIGIN_PT = (39.6542, 66.9597)      # restoran (standart)
POINT = (39.6548, 66.9757)          # Registon yaqini
TASHKENT = (41.2995, 69.2401)       # endi rad etilmaydi - uzoq, narx katta
KEY = "ORS_TEST_KEY_" + "Z" * 24


def run(coro):
    return asyncio.run(coro)


class FakeOrs:
    """ORS o'rnida: javoblar navbati yoki bitta javob; so'rovlarni yozib boradi."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses) or [(200, {"routes": [{"summary": {"distance": 3412.7}}]})]
        self.calls: list = []

    async def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        r = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(r, BaseException):
            raise r
        if r == "sleep":
            await asyncio.sleep(timeout + 2)
        return r


def service(fetch=None, key: str | None = KEY, **kw) -> DeliveryService:
    return DeliveryService(origin=ORIGIN_PT, api_key=key, fetch=fetch or FakeOrs(), **kw)


# ================================================================ narx formulasi

def test_rounding_and_fee_boundaries():
    cases = {
        "0": (0, 10_000), "2.0": (20, 10_000), "2.5": (25, 10_000), "2.54": (25, 10_000), "2.55": (26, 12_500),
        "2.6": (26, 12_500), "3.0": (30, 12_500), "3.5": (35, 12_500), "3.6": (36, 15_000), "4.5": (45, 15_000),
        "4.6": (46, 17_500), "5.5": (55, 17_500), "5.6": (56, 20_000), "12.34": (123, 35_000),  # billable 12 km -> 10 qo'shimcha km
    }
    for km, (tenths, fee) in cases.items():
        assert km_to_tenths(km) == tenths, km
        assert delivery_fee(km) == fee, km
    # suzuvchi nuqta: 2.55 float ham aniq 2.6 ga yaxlitlanadi
    assert km_to_tenths(2.55) == 26 and km_to_tenths(Decimal("2.549999")) == 25
    assert delivery_fee("3.6", Tariff(base_fee=5000, base_km=3, extra_per_km=1000)) == 6000


# ================================================================ ORS va zaxira

def test_ors_distance_request_shape_and_quote():
    ors = FakeOrs()
    q = run(service(ors).quote(*POINT))
    assert (q.km, q.fee, q.approx) == (3.4, 12_500, False)
    url, body, headers, timeout = ors.calls[0]
    assert url == delivery.ORS_DIRECTIONS_URL == "https://api.heigit.org/openrouteservice/v2/directions/driving-car/json"
    assert body["coordinates"] == [[ORIGIN_PT[1], ORIGIN_PT[0]], [POINT[1], POINT[0]]], "ORS tartibi [lon, lat]"
    assert body["instructions"] is False and body["geometry"] is False
    assert headers["Authorization"] == KEY and timeout == 4.0
    assert q.as_json() == {"km": 3.4, "fee": 12_500, "approx": False}


def test_fallback_on_errors_timeout_429_and_missing_key():
    expected_km = delivery.haversine_km(*ORIGIN_PT, *POINT) * 1.3
    expected = (km_to_tenths(repr(expected_km)), True)
    for name, fetch in {
        "500": FakeOrs((500, {"error": "x"})),
        "429": FakeOrs((429, {"error": "Rate Limit Exceeded"})),
        "bad_json": FakeOrs((200, {"oops": 1})),
        "network": FakeOrs(OSError("connection refused")),
        "timeout": FakeOrs("sleep"),
    }.items():
        svc = service(fetch, timeout=0.05)
        q = run(svc.quote(*POINT))
        assert (q.tenths, q.approx) == expected, name
    no_key = FakeOrs()
    q = run(service(no_key, key=None).quote(*POINT))
    assert q.approx is True and no_key.calls == [], "kalit yo'q - ORS chaqirilmaydi"


def test_cache_by_rounded_coordinates_and_ttl():
    ors = FakeOrs()
    now = [1000.0]
    svc = service(ors, clock=lambda: now[0])
    run(svc.quote(39.65481, 66.97572))
    run(svc.quote(39.65484, 66.97568))     # 4 xonagacha bir xil - keshdan
    assert len(ors.calls) == 1
    now[0] += 24 * 3600 + 1                # TTL tugadi
    run(svc.quote(39.65481, 66.97572))
    assert len(ors.calls) == 2
    # taxminiy natija keshlanmaydi - ORS tiklanganda darhol yo'l masofasi
    flaky = FakeOrs((500, {}), (200, {"routes": [{"summary": {"distance": 1000}}]}))
    svc2 = service(flaky)
    first, second = run(svc2.quote(*POINT)), run(svc2.quote(*POINT))
    assert first.approx is True and second.approx is False and second.km == 1.0


def test_daily_counter_limit_and_reset():
    ors = FakeOrs()
    day = ["2026-11-21"]
    svc = service(ors, daily_limit=3, today=lambda: day[0])
    results = [run(svc.quote(39.6 + i / 100, 66.9)) for i in range(5)]
    assert [r.approx for r in results] == [False, False, False, True, True]
    assert len(ors.calls) == 3 and svc.requests_today == 3
    day[0] = "2026-11-22"
    assert run(svc.quote(39.7, 66.95)).approx is False and svc.requests_today == 1


def test_zero_distance_and_invalid_point():
    q = run(service(FakeOrs((200, {"routes": [{"summary": {}}]}))).quote(*ORIGIN_PT))
    assert (q.km, q.fee, q.approx) == (0.0, 10_000, False), "nol masofada ORS distance maydonini bermaydi"
    try:
        run(service().quote(95.0, 66.0))
    except ValueError:
        pass
    else:
        raise AssertionError("diapazondan tashqari nuqta")


# ================================================================ buyurtma: server narxni qayta hisoblaydi

def test_server_recalculates_fee_and_ignores_client_sums():
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        svc = service(FakeOrs((200, {"routes": [{"summary": {"distance": 4040}}]})))  # 4.0 km -> 15 000
        msg, answers = make_message(1, good(delivery_fee=0, total=1, items_total=1, distance_km=0.1))
        await on_web_app_data(msg, **env.deps(bot), lang="uz", delivery=svc)
        row = await env.db.get_order(1)
        await env.db.close()
        return row, answers, bot

    row, answers, bot = run(go())
    assert row["delivery_fee"] == 15_000 and row["distance_km"] == 4.0 and row["distance_approx"] == 0
    assert row["total"] == row["items_total"] + 15_000
    assert "Yetkazish (4.0 km): 15 000 so'm" in answers[0][0], answers[0][0]
    receipt = next(s[2] for s in bot.sent if s[1] == 999 and s[0] == "message")
    assert "Yetkazish: 15 000 so'm — 4.0 km" in receipt and "taxminiy" not in receipt


def test_far_location_accepted_with_approx_mark():
    """Samarqand cheklovi yo'q: Toshkent ham qabul (narx masofaga qarab), ORS yo'q - taxminiy."""
    async def go():
        env = Env()
        await env.db.connect()
        bot = FakeBot()
        msg, answers = make_message(2, good(lat=TASHKENT[0], lon=TASHKENT[1]))
        await on_web_app_data(msg, **env.deps(bot), lang="ru")
        row = await env.db.get_order(1)
        await env.db.close()
        return row, answers, bot

    row, answers, bot = run(go())
    assert row is not None and row["distance_approx"] == 1 and row["distance_km"] > 250
    assert row["delivery_fee"] == delivery_fee(str(row["distance_km"]))
    assert answers[0][0].startswith("✅") and ", примерно" in answers[0][0]
    receipt = next(s[2] for s in bot.sent if s[1] == 999 and s[0] == "message")
    assert "(taxminiy masofa)" in receipt


def test_order_without_location_rejected():
    async def go():
        env = Env()
        await env.db.connect()
        out = []
        for lang in ("uz", "ru"):
            bot = FakeBot()
            env.limiter.release(3)
            data = good(ref=secrets.token_hex(16), address_type="text", address_text="Registon 1", lat=None, lon=None)
            msg, answers = make_message(3, data)
            await on_web_app_data(msg, **env.deps(bot), lang=lang)
            out.append(answers[0][0])
        count = (await (await env.db.conn.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        res = await env.db.order_status(data["ref"])
        await env.db.close()
        return out, count, res

    (uz, ru), count, res = run(go())
    assert count == 0 and res == {"status": "rejected", "n": None, "reason": "location"}
    assert "lokatsiya" in uz and "местоположение" in ru


def test_send_data_and_post_same_delivery_result():
    p = good(ref=secrets.token_hex(16))

    async def via_send_data():
        env = Env()
        await env.db.connect()
        await on_web_app_data(make_message(5, p)[0], **env.deps(FakeBot()), lang="uz")
        row = await env.db.get_order(1)
        await env.db.close()
        return row

    async def via_post():
        async def fn(client, env, bot, _api):
            status, body, _ = await post(client, p, init_data=init_data_for(5))
            return body, await env.db.get_order(1)
        return await with_api(fn)

    a = run(via_send_data())
    body, b = run(via_post())
    assert body["status"] == "accepted"
    for col in ("delivery_fee", "distance_km", "distance_approx", "total", "items_total"):
        assert a[col] == b[col], col


# ================================================================ API: /api/config, /api/delivery-quote

def test_config_and_quote_endpoints():
    async def go():
        env = Env()
        cfg = env.config
        svc = service(FakeOrs((200, {"routes": [{"summary": {"distance": 2550}}]})))
        api = orderapi.DeliveryAPI(config=cfg, delivery=svc)
        bot = Bot("123456:" + "A" * 35)
        app = webhook.build_app(Dispatcher(), bot, "x" * 20, api=api)
        out = {}
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/config", headers={"Origin": ORIGIN})
            out["config"] = (r.status, await r.json(), r.headers.get("Cache-Control"), r.headers.get("Access-Control-Allow-Origin"))
            hdr = {"Origin": ORIGIN, "Authorization": f"tma {init_data_for(7)}"}
            r = await client.get(f"/api/delivery-quote?lat={POINT[0]}&lon={POINT[1]}", headers=hdr)
            out["quote"] = (r.status, await r.json(), r.headers.get("Access-Control-Allow-Origin"))
            out["no_auth"] = (await client.get(f"/api/delivery-quote?lat=1&lon=1", headers={"Origin": ORIGIN})).status
            out["bad"] = [(await client.get(f"/api/delivery-quote?{q}", headers=hdr)).status
                          for q in ("lat=91&lon=1", "lat=a&lon=1", "lat=1", "lat=nan&lon=1", "lat=1&lon=181")]
            out["evil"] = (await client.get("/api/config", headers={"Origin": "https://evil.example"})).status
            pre = await client.options("/api/delivery-quote", headers={
                "Origin": ORIGIN, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
            out["preflight"] = (pre.status, pre.headers.get("Access-Control-Allow-Methods"))
            api.user_limiter = orderapi.WindowLimiter(2, 60.0)
            out["limit"] = [(await client.get(f"/api/delivery-quote?lat=1&lon=1", headers=hdr)).status for _ in range(3)]
        await bot.session.close()
        return out

    out = run(go())
    status, body, cache, acao = out["config"]
    assert status == 200 and body == {"base_km": 2, "base_fee": 10_000, "extra_per_km": 2_500}
    assert "max-age" in cache and acao == ORIGIN
    assert out["quote"] == (200, {"km": 2.6, "fee": 12_500, "approx": False}, ORIGIN)
    assert out["no_auth"] == 401 and set(out["bad"]) == {400} and out["evil"] == 403
    assert out["preflight"] == (204, "GET")
    assert out["limit"] == [200, 200, 429]


# ================================================================ admin, Excel, migratsiya, sozlama, loglar

def test_receipt_distance_lines():
    base = dict(order_id=1, created_at=datetime.now(timezone.utc), user_id=5, username=None, name="A",
                phone="+998901234567", address_type="location", address_text=None, lat=39.65, lon=66.97,
                address_details=None, comment=None, lines=(("Burger", 1, 25000),), total=37500, items_total=25000,
                delivery_fee=12500)
    exact = build_receipt(ReceiptData(**base, distance_km=3.4, distance_approx=False))
    approx = build_receipt(ReceiptData(**base, distance_km=3.4, distance_approx=True))
    old = build_receipt(ReceiptData(**base))
    assert "🚚 Yetkazish: 12 500 so'm — 3.4 km" in exact and "taxminiy" not in exact
    assert "— 3.4 km <i>(taxminiy masofa)</i>" in approx
    assert "🚚 Yetkazish: 12 500 so'm\n" in old, "eski buyurtma - masofasiz"


def test_excel_distance_column():
    rows = []
    for i, (km, approx) in enumerate(((3.4, 0), (12.6, 1), (None, 0)), start=1):
        rows.append(({"id": i, "created_at": "2026-11-21T10:00:00+00:00", "status": "new", "customer_name": "A",
                      "phone": "+998901234567", "username": None, "address_type": "location", "address_text": None,
                      "lat": 39.6, "lon": 66.9, "address_details": None, "comment": None, "items_total": 25000,
                      "delivery_fee": 12500, "total": 37500, "distance_km": km, "distance_approx": approx},
                     [{"item_id": "a", "name": "Burger", "price": 25000, "qty": 1, "line_total": 25000}]))
    now = datetime.now(timezone.utc)
    period = excel_export.export_range("hammasi", now)
    data = excel_export.build_workbook(rows, period, now)
    ws = load_workbook(io.BytesIO(data)).worksheets[0]
    col = excel_export.DISTANCE_COL
    assert ws.cell(1, col).value == "Masofa (km)"
    assert ws.cell(2, col).value == 3.4 and ws.cell(2, col).number_format == "0.0"
    assert ws.cell(3, col).value == 12.6 and "taxminiy" in ws.cell(3, col).number_format
    assert ws.cell(4, col).value is None


def test_migration_adds_distance_columns_keeps_orders():
    path = Path(tempfile.mkdtemp()) / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT, "
                "customer_name TEXT NOT NULL, phone TEXT NOT NULL, address_type TEXT NOT NULL, address_text TEXT, "
                "lat REAL, lon REAL, total INTEGER NOT NULL, created_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO orders (user_id, customer_name, phone, address_type, address_text, total, created_at) "
                "VALUES (7, 'Ali', '+998901234567', 'text', 'Registon', 50000, '2026-01-01T10:00:00+00:00')")
    con.commit()
    con.close()

    async def go():
        db = Database(path)
        await db.connect()
        row = await db.get_order(1)
        await db.close()
        return row

    logging.disable(logging.CRITICAL)
    try:
        row = run(go())
    finally:
        logging.disable(logging.NOTSET)
    assert row["distance_km"] is None and row["distance_approx"] == 0
    assert row["customer_name"] == "Ali" and row["address_type"] == "text" and row["total"] == 50000


def test_delivery_config_parsing():
    with env_vars():
        cfg = config_module.load_config()
    assert cfg.restaurant_from_env is False and (cfg.restaurant_lat, cfg.restaurant_lon) == ORIGIN_PT
    assert (cfg.delivery_base_fee, cfg.delivery_base_km, cfg.delivery_extra_per_km, cfg.road_factor) == (10000, 2, 2500, 1.3)
    assert cfg.ors_api_key is None
    with env_vars(RESTAURANT_LAT="39.66", RESTAURANT_LON="66.94", DELIVERY_BASE_FEE="12000", DELIVERY_BASE_KM="3",
                  DELIVERY_EXTRA_PER_KM="3000", ROAD_FACTOR="1.4", ORS_API_KEY=KEY):
        cfg = config_module.load_config()
    assert cfg.restaurant_from_env and (cfg.restaurant_lat, cfg.restaurant_lon) == (39.66, 66.94)
    assert (cfg.delivery_base_fee, cfg.delivery_base_km, cfg.delivery_extra_per_km, cfg.road_factor) == (12000, 3, 3000, 1.4)
    assert cfg.ors_api_key == KEY and KEY not in repr(cfg)
    svc = DeliveryService.from_config(cfg)
    assert KEY not in repr(svc) and svc.tariff == Tariff(12000, 3, 3000)
    for bad in ({"RESTAURANT_LAT": "39.6"}, {"RESTAURANT_LAT": "95", "RESTAURANT_LON": "66"},
                {"DELIVERY_BASE_FEE": "abc"}, {"ROAD_FACTOR": "0.5"}):
        with env_vars(**bad):
            try:
                config_module.load_config()
            except config_module.ConfigError:
                continue
        raise AssertionError(bad)


def test_ors_key_never_logged():
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    lg = logging.getLogger("delivery")
    handler = Grab()
    lg.addHandler(handler)
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    try:
        for fetch in (FakeOrs((401, {"error": f"bad key {KEY}"})), FakeOrs(RuntimeError(f"auth {KEY} failed")),
                      FakeOrs("sleep")):
            run(service(fetch, timeout=0.05).quote(*POINT))
        run(service(FakeOrs(), daily_limit=0).quote(*POINT))
    finally:
        logging.disable(disabled)
        lg.removeHandler(handler)
    text = "\n".join(records)
    assert "ORS masofa olinmadi" in text and "kunlik limiti" in text
    assert KEY not in text


def test_i18n_delivery_keys_both_languages():
    import i18n
    for key in ("val.loc_required", "totals.delivery_km", "totals.approx", "welcome.delivery"):
        assert key in i18n.TEXTS["uz"] and key in i18n.TEXTS["ru"], key
    assert "Samarqand" not in i18n.TEXTS["uz"]["welcome.delivery"] and "{area}" not in json.dumps(i18n.TEXTS)
    from test_i18n import load_miniapp_i18n
    d = load_miniapp_i18n()
    for key in ("sum.delivery_km", "sum.delivery_km_approx", "sum.total_from", "quote.loading", "quote.from",
                "delivery.tariff", "form.loc_note", "reject.location"):
        assert d["uz"][key] and d["ru"][key] and d["uz"][key] != d["ru"][key], key
    for lang in ("uz", "ru"):
        text = json.dumps(d[lang], ensure_ascii=False)
        assert "faqat Samarqand" not in text and "только по" not in text and "{area}" not in text, lang


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
