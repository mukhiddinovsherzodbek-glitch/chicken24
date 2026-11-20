"""Uxlab qolmaslik va tez ishga tushish testlari (Render bepul rejasi).

- /health: GET va HEAD -> 200, 1 soniyadan tez, bazaga tegmaydi, bot hali tayyor bo'lmasa ham ishlaydi;
- webhook: sir noto'g'ri -> darhol 401; bot tayyor bo'lguncha yangilanish kutadi; juda uzoq bo'lsa 503;
- run() webhook rejimida: HTTP server baza ulanishidan OLDIN javob beradi, setWebhook (sir bilan,
  drop_pending_updates=False) har ishga tushishda, to'xtagach router qayta ulanishi mumkin;
- self-ping: faqat RENDER_EXTERNAL_URL / PUBLIC_URL bo'lsa; xato botni to'xtatmaydi; logda manzil yo'q;
- ishga tushish qatori: rejim, baza turi, self-ping - manzil/token/parolsiz.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import socket
import sys
import tempfile
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import aiohttp  # noqa: E402
import config as config_module  # noqa: E402
import main as main_module  # noqa: E402
import selfping  # noqa: E402
import webhook  # noqa: E402
from aiogram import Bot, Dispatcher, Router  # noqa: E402
from aiogram.types import Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from handlers import router as main_router  # noqa: E402
from test_flow import MENU  # noqa: E402
from test_server import FAKE_TOKEN, SECRET, ExplodingDB, env, update_json  # noqa: E402

PING_URL = "https://chicken24-bot.onrender.com/health"


def run(coro):
    return asyncio.run(coro)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def capture(logger_name: str) -> Capture:
    handler = Capture()
    lg = logging.getLogger(logger_name)
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    return handler


# ================================================================ /health

def test_health_get_and_head_fast_without_db_even_before_ready():
    async def scenario():
        bot = Bot(FAKE_TOKEN)
        ready = asyncio.Event()  # hech qachon o'rnatilmaydi - bot "hali tayyor emas"
        app = webhook.build_app(Dispatcher(db=ExplodingDB()), bot, SECRET, ready=ready)
        out = []
        async with TestClient(TestServer(app)) as client:
            for method in ("GET", "HEAD", "GET"):
                t0 = time.perf_counter()
                resp = await client.request(method, "/health")
                body = await resp.read()
                out.append((method, resp.status, time.perf_counter() - t0, body))
        await bot.session.close()
        return out

    for method, status, seconds, body in run(scenario()):
        assert status == 200, method
        assert seconds < 1.0, f"{method} /health {seconds:.3f}s"
        if method == "GET":
            assert json.loads(body) == {"ok": True}


# ================================================================ webhook: tayyor bo'lguncha kutish

def test_webhook_waits_until_ready_and_rejects_bad_secret_immediately():
    async def scenario():
        bot = Bot(FAKE_TOKEN)
        dp = Dispatcher()
        r = Router()
        got: list[str] = []

        @r.message()
        async def capture_msg(message: Message) -> None:
            got.append(message.text)

        dp.include_router(r)
        ready = asyncio.Event()
        path = webhook.webhook_path(SECRET)
        hdr = {"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET}
        body = json.dumps(update_json("salom"))
        out = {}
        async with TestClient(TestServer(webhook.build_app(dp, bot, SECRET, ready=ready))) as client:
            t0 = time.perf_counter()
            bad = await client.post(path, data=body, headers={**hdr, "X-Telegram-Bot-Api-Secret-Token": "x" * 20})
            out["bad"] = (bad.status, time.perf_counter() - t0)
            pending = asyncio.create_task(client.post(path, data=body, headers=hdr))
            await asyncio.sleep(0.3)
            out["waiting"] = not pending.done() and got == []
            ready.set()
            resp = await asyncio.wait_for(pending, 5)
            out["after_ready"] = resp.status
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.02)
            out["handled"] = list(got)
        await bot.session.close()
        return out

    out = run(scenario())
    assert out["bad"][0] == 401 and out["bad"][1] < 1.0, "noto'g'ri sir - kutmasdan 401"
    assert out["waiting"] is True, "bot tayyor bo'lguncha yangilanish qayta ishlanmaydi"
    assert out["after_ready"] == 200 and out["handled"] == ["salom"]


def test_webhook_returns_503_if_never_ready():
    async def scenario():
        bot = Bot(FAKE_TOKEN)
        app = webhook.build_app(Dispatcher(), bot, SECRET, ready=asyncio.Event(), ready_timeout=0.2)
        hdr = {"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET}
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(webhook.webhook_path(SECRET), data=json.dumps(update_json("x")), headers=hdr)
            status = resp.status
        await bot.session.close()
        return status

    webhook.logger.disabled = True
    try:
        assert run(scenario()) == 503, "Telegram 503 dan keyin qayta yuboradi - xabar yo'qolmaydi"
    finally:
        webhook.logger.disabled = False


# ================================================================ run(): tez ishga tushish (webhook)

class FakeTgBot(Bot):
    """Haqiqiy aiogram Bot, faqat setWebhook tarmoqqa chiqmaydi (yozib qo'yiladi)."""

    instances: list["FakeTgBot"] = []

    def __init__(self, token, default=None) -> None:
        super().__init__(token, default=default)
        self.webhooks: list = []
        FakeTgBot.instances.append(self)

    async def set_webhook(self, url, **kw):
        self.webhooks.append((url, kw))
        return True


class SlowDB:
    """Neon uyg'onishini taqlid qiladi: connect() gate ochilguncha kutadi."""

    kind = "postgres"

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        await self.gate.wait()
        self.connected = True

    async def close(self) -> None:
        self.closed = True


def make_server_config(menu_path: Path, port: int):
    return config_module.Config(
        bot_token=FAKE_TOKEN, admin_id=999, webapp_url="https://example.pages.dev", menu_path=menu_path,
        db_path=menu_path.parent / "t.db", menu_url=None, database_url="postgresql://u:p@h/db",
        webhook_url="https://chicken24-bot.onrender.com", webhook_secret=SECRET, port=port,
        self_ping_url=None,
    )


def test_run_webhook_serves_health_before_db_and_sets_webhook_each_start():
    tmp = Path(tempfile.mkdtemp())
    menu_path = tmp / "menu.json"
    menu_path.write_text(json.dumps(MENU), encoding="utf-8")

    saved = (main_module.Bot, main_module.Database.from_config, main_module.startup_jobs,
             main_module.background_tasks, webhook.install_stop_signals)
    stops: list[asyncio.Event] = []
    started: list = []

    async def fake_startup_jobs(*a, **kw):
        started.append(a)

    main_module.Bot = FakeTgBot
    main_module.background_tasks = lambda *a, **kw: []
    main_module.startup_jobs = fake_startup_jobs
    webhook.install_stop_signals = stops.append

    async def one_start(port: int, db: SlowDB) -> dict:
        main_module.Database.from_config = classmethod(lambda cls, cfg: db)
        cfg = make_server_config(menu_path, port)
        task = asyncio.create_task(main_module.run(cfg))
        out: dict = {}
        async with aiohttp.ClientSession() as session:
            url = f"http://127.0.0.1:{port}"
            t0 = time.perf_counter()
            for _ in range(200):
                try:
                    async with session.get(url + "/health") as resp:
                        out["health"] = resp.status
                        break
                except aiohttp.ClientConnectionError:
                    await asyncio.sleep(0.02)
            out["health_seconds"] = time.perf_counter() - t0
            async with session.head(url + "/health") as resp:
                out["head"] = resp.status
            out["db_connected_when_health_ok"] = db.connected
            out["webhook_before_db"] = list(FakeTgBot.instances[-1].webhooks)
            hdr = {"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET}
            pending = asyncio.create_task(session.post(url + webhook.webhook_path(SECRET),
                                                       data=json.dumps({"update_id": 1}), headers=hdr))
            await asyncio.sleep(0.3)
            out["update_waits_for_db"] = not pending.done()
            db.gate.set()  # Neon uyg'ondi
            resp = await asyncio.wait_for(pending, 5)
            out["update_status"] = resp.status
            resp.release()
            for _ in range(100):
                if FakeTgBot.instances[-1].webhooks and started:
                    break
                await asyncio.sleep(0.02)
            out["webhooks"] = list(FakeTgBot.instances[-1].webhooks)
        stops[-1].set()
        await asyncio.wait_for(task, 10)
        out["db_closed"] = db.closed
        return out

    async def scenario():
        first = await one_start(free_port(), SlowDB())
        second = await one_start(free_port(), SlowDB())  # qayta ishga tushish (router qayta ulanadi)
        return first, second

    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # {"update_id": 1} - aiogram "unknown update type"
            first, second = run(scenario())
    finally:
        logging.disable(disabled)
        (main_module.Bot, main_module.Database.from_config, main_module.startup_jobs,
         main_module.background_tasks, webhook.install_stop_signals) = saved
        main_module.detach_router(SimpleNamespace(sub_routers=[]))

    for out in (first, second):
        assert out["health"] == 200 and out["head"] == 200
        assert out["health_seconds"] < 2.0
        assert out["db_connected_when_health_ok"] is False, "/health baza ulanishini kutmaydi"
        assert out["webhook_before_db"] == []
        assert out["update_waits_for_db"] is True and out["update_status"] == 200, (out["update_waits_for_db"], out["update_status"])
        assert len(out["webhooks"]) == 1, "setWebhook har ishga tushishda bir marta"
        url, kw = out["webhooks"][0]
        assert url == "https://chicken24-bot.onrender.com" + webhook.webhook_path(SECRET)
        assert kw["secret_token"] == SECRET and kw["drop_pending_updates"] is False
        assert out["db_closed"] is True
    assert main_router.parent_router is None, "to'xtagach router ajratiladi"


def test_router_can_be_attached_again_after_detach():
    dp1 = Dispatcher()
    dp1.include_router(main_router)
    main_module.detach_router(dp1)
    dp2 = Dispatcher()
    dp2.include_router(main_router)  # xatosiz
    main_module.detach_router(dp2)
    assert main_router.parent_router is None


# ================================================================ self-ping

def test_self_ping_config():
    with env():
        assert config_module.load_config().self_ping_url is None, "lokal kompyuterda o'chiq"
    with env(RENDER_EXTERNAL_URL="https://chicken24-bot.onrender.com"):
        cfg = config_module.load_config()
        assert cfg.self_ping_url == PING_URL
        assert PING_URL not in repr(cfg)
    with env(RENDER_EXTERNAL_URL="https://a.onrender.com/", PUBLIC_URL="https://bot.example.com/"):
        assert config_module.load_config().self_ping_url == "https://bot.example.com/health"
    with env(RENDER_EXTERNAL_URL="not a url"):
        assert config_module.load_config().self_ping_url is None
    with env(PUBLIC_URL="ftp://x"):
        try:
            config_module.load_config()
        except config_module.ConfigError as exc:
            assert "PUBLIC_URL" in str(exc)
        else:
            raise AssertionError("ConfigError kutilgan edi")


def test_self_ping_loop_survives_errors_and_logs_no_url():
    results = iter([200, RuntimeError("tarmoq " + PING_URL), 503, asyncio.TimeoutError(), 200, 200])
    calls: list[str] = []
    sleeps: list[float] = []

    async def fake_ping(session, url):
        calls.append(url)
        r = next(results)
        if isinstance(r, BaseException):
            raise r
        return r

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 6:
            raise asyncio.CancelledError

    log = capture("selfping")
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    try:
        try:
            run(selfping.self_ping_loop(PING_URL, ping=fake_ping, sleep=fake_sleep))
        except asyncio.CancelledError:
            pass
    finally:
        logging.disable(disabled)
        logging.getLogger("selfping").removeHandler(log)
        logging.getLogger("selfping").propagate = True
    assert calls == [PING_URL] * 6, "xatolardan keyin ham davom etadi"
    assert sleeps[0] == selfping.SELF_PING_SECONDS == 480
    warnings = [line for line in log.lines if "muvaffaqiyatsiz" in line]
    assert len(warnings) == 3 and "RuntimeError" in warnings[0] and "HTTP 503" in warnings[1]
    assert any("tiklandi (3 ta" in line for line in log.lines)
    assert not any("onrender" in line or "https://" in line for line in log.lines), "logda manzil yo'q"


def test_ping_once_hits_real_health_endpoint():
    async def scenario():
        bot = Bot(FAKE_TOKEN)
        app = webhook.build_app(Dispatcher(db=ExplodingDB()), bot, SECRET)
        async with TestClient(TestServer(app)) as client:
            async with aiohttp.ClientSession() as session:
                status = await selfping.ping_once(session, str(client.make_url("/health")))
        await bot.session.close()
        return status

    assert run(scenario()) == 200


def test_startup_summary_line():
    db = SimpleNamespace(kind="postgres")
    tmp = Path(tempfile.mkdtemp()) / "menu.json"
    on = dataclasses.replace(make_server_config(tmp, 8080), self_ping_url=PING_URL)
    line = main_module.startup_summary(on, db)
    assert line == "Ishga tushdi: rejim=webhook, baza=postgres, self-ping=yoqilgan (har 8 daqiqa)"
    local = config_module.Config(bot_token=FAKE_TOKEN, admin_id=1, webapp_url="https://e.pages.dev",
                                 menu_path=tmp, db_path=tmp)
    assert main_module.startup_summary(local, SimpleNamespace(kind="sqlite")) == \
        "Ishga tushdi: rejim=polling, baza=sqlite, self-ping=o'chiq"
    for secret in (FAKE_TOKEN, SECRET, "u:p@", "onrender", "https://"):
        assert secret not in line


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
