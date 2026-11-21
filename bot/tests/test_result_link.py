"""Mini App tugmasi havolasidagi natija (?ok= / ?rej= / ?cx=) bazadan olinishi testlari.

Xato: buyurtma qabul qilingach mijoz Mini App'ni ochmasdan /til (yoki /start) bossa, yangi klaviatura
faqat ?lang= bilan yuborilardi va ?ok= yo'qolardi -> Mini App'da savat to'la, buyurtma "kutilmoqda".
Endi har bir klaviatura build_webapp_url() orqali, natija esa bazadan.
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import handlers  # noqa: E402
from db import Database  # noqa: E402
from handlers import (  # noqa: E402
    WelcomePhoto,
    build_webapp_url,
    cmd_start,
    on_cancel_order,
    on_lang_chosen,
    on_web_app_data,
)
from test_flow import Env, FakeBot, good, make_message, order_button, NO_LOC  # noqa: E402

business.is_open_now = lambda: True


def run(coro):
    return asyncio.run(coro)


def q(url: str) -> dict:
    return parse_qs(urlparse(url).query)


def kb_query(markup) -> dict:
    return q(order_button(markup).web_app.url)


class LangBot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.photos: list = []

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, reply_markup))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="F")])


def callback(user_id: int, data: str):
    async def edit_text(text, **kw):
        return None

    async def answer(*a, **kw):
        return None

    return SimpleNamespace(from_user=SimpleNamespace(id=user_id, first_name="Ali"), data=data,
                           message=SimpleNamespace(edit_text=edit_text), answer=answer)


async def til(env: Env, bot, user_id: int, new_lang: str, old_lang: str = "uz") -> dict:
    """/til -> til tanlash tugmasi: yangi klaviatura havolasi parametrlari."""
    await on_lang_chosen(callback(user_id, f"lang:{new_lang}"), bot, env.config, env.db, lang=old_lang)
    msg = [s for s in bot.sent if s[0] == "message" and s[1] == user_id][-1]
    return kb_query(msg[3]["reply_markup"])


# ------------------------------------------------------------ asosiy xato: /til natijani yo'qotmasin

def test_til_after_accepted_order_keeps_ok():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(1, "uz", datetime.now(timezone.utc))
        bot = LangBot()
        await on_web_app_data(make_message(1, good())[0], **env.deps(bot), lang="uz")
        query = await til(env, bot, 1, "ru")
        await env.db.close()
        return query

    assert run(scenario()) == {"lang": ["ru"], "ok": ["abc123xyz"], "n": ["1"]}


def test_start_after_accepted_order_keeps_ok():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        WelcomePhoto.file_ids = {}
        await on_web_app_data(make_message(2, good())[0], **env.deps(bot), lang="uz")
        # /start -> til so'raladi (klaviaturasiz inline xabar), keyin "Русский" -> salomlashuv
        await cmd_start(SimpleNamespace(chat=SimpleNamespace(id=2)), bot)
        prompt = [s for s in bot.sent if s[0] == "message" and s[1] == 2][-1]
        await on_lang_chosen(callback(2, "lang:ru:w"), bot, env.config, env.db, lang="uz")
        await env.db.close()
        return prompt, kb_query(bot.photos[-1][1])

    prompt, query = run(scenario())
    assert prompt[2] == "Tilni tanlang / Выберите язык" and not hasattr(prompt[3]["reply_markup"], "keyboard")
    assert query == {"lang": ["ru"], "ok": ["abc123xyz"], "n": ["1"]}


def test_rejected_and_cancelled_give_rej_and_cx():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        # rad etilgan (Toshkent - hududdan tashqari)
        await on_web_app_data(make_message(3, good(ref="rejref1", **NO_LOC))[0],
                              **env.deps(bot), lang="uz")
        rej = await til(env, bot, 3, "ru")
        # qabul qilinib, keyin bekor qilingan
        await on_web_app_data(make_message(4, good(ref="cxref12"))[0], **env.deps(bot), lang="uz")
        cb = callback(4, "cancel:1")
        cb.answer = lambda *a, **kw: asyncio.sleep(0)
        await on_cancel_order(cb, bot, env.config, env.db, lang="uz")
        after_cancel = kb_query([s for s in bot.sent if s[0] == "message" and s[1] == 4][-1][3]["reply_markup"])
        cx = await til(env, bot, 4, "ru")
        await env.db.close()
        return rej, after_cancel, cx

    rej, after_cancel, cx = run(scenario())
    assert rej == {"lang": ["ru"], "rej": ["rejref1"], "r": ["location"]}
    assert after_cancel == {"lang": ["uz"], "cx": ["cxref12"]}
    assert cx == {"lang": ["ru"], "cx": ["cxref12"]}


def test_no_orders_only_lang():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        first = await on_lang_chosen(callback(5, "lang:ru"), bot, env.config, env.db, lang=None)
        url = await build_webapp_url(env.config, env.db, 5, "uz")
        await env.db.close()
        return kb_query(bot.photos[-1][1]), q(url), first

    welcome, direct, _ = run(scenario())
    assert welcome == {"lang": ["ru"]} and direct == {"lang": ["uz"]}


def test_newest_result_wins():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        out = []
        # 1) qabul qilindi
        await on_web_app_data(make_message(6, good(ref="first111"))[0], **env.deps(bot), lang="uz")
        out.append(q(await build_webapp_url(env.config, env.db, 6, "uz")))
        # 2) keyingisi rad etildi (30 soniya ichida - limit)
        await on_web_app_data(make_message(6, good(ref="second22"))[0], **env.deps(bot), lang="uz")
        out.append(q(await build_webapp_url(env.config, env.db, 6, "uz")))
        # 3) keyin yana qabul qilindi -> eski rad emas, yangi ok
        env.limiter.release(6)
        await on_web_app_data(make_message(6, good(ref="third333"))[0], **env.deps(bot), lang="uz")
        out.append(q(await build_webapp_url(env.config, env.db, 6, "uz")))
        await env.db.close()
        return out

    first, second, third = run(scenario())
    assert first == {"lang": ["uz"], "ok": ["first111"], "n": ["1"]}
    assert second == {"lang": ["uz"], "rej": ["second22"], "r": ["limit"]}
    assert third == {"lang": ["uz"], "ok": ["third333"], "n": ["2"]}


def test_order_without_ref_clears_old_result():
    """Eng oxirgi buyurtmada ref yo'q (eski Mini App) - avvalgi buyurtma natijasi qo'yilmaydi."""
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        await on_web_app_data(make_message(7, good(ref="old11111"))[0], **env.deps(bot), lang="uz")
        env.limiter.release(7)
        data = good()
        data.pop("ref")
        await on_web_app_data(make_message(7, data)[0], **env.deps(bot), lang="uz")
        url = await build_webapp_url(env.config, env.db, 7, "uz")
        await env.db.close()
        return q(url)

    assert run(scenario()) == {"lang": ["uz"]}


def test_result_survives_bot_restart():
    async def scenario():
        env = Env()
        await env.db.connect()
        await on_web_app_data(make_message(8, good())[0], **env.deps(LangBot()), lang="uz")
        await env.db.close()
        db2 = Database(env.config.db_path)  # bot qayta ishga tushdi
        await db2.connect()
        url = await build_webapp_url(env.config, db2, 8, "ru")
        await db2.close()
        return q(url)

    assert run(scenario()) == {"lang": ["ru"], "ok": ["abc123xyz"], "n": ["1"]}


def test_results_are_per_user():
    async def scenario():
        env = Env()
        await env.db.connect()
        await on_web_app_data(make_message(9, good())[0], **env.deps(LangBot()), lang="uz")
        url = await build_webapp_url(env.config, env.db, 10, "uz")
        await env.db.close()
        return q(url)

    assert run(scenario()) == {"lang": ["uz"]}


def test_db_error_falls_back_to_lang_only():
    class BrokenDB:
        async def latest_result(self, user_id):
            raise RuntimeError("baza yo'q")

    env = Env()
    url = run(build_webapp_url(env.config, BrokenDB(), 1, "ru"))
    assert q(url) == {"lang": ["ru"]}


def test_rejection_older_than_order_is_ignored():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.record_rejection(11, "oldrej11", "limit", datetime.now(timezone.utc) - timedelta(minutes=5))
        await on_web_app_data(make_message(11, good())[0], **env.deps(LangBot()), lang="uz")
        res = await env.db.latest_result(11)
        await env.db.close()
        return res

    assert run(scenario()) == {"kind": "ok", "ref": "abc123xyz", "order_id": 1, "reason": None}


# ------------------------------------------------------------ kodda URL faqat bitta joyda yig'iladi

def test_order_and_rejection_timestamps_strictly_increase():
    """Windows soati bir necha ms bir xil qiymat berishi mumkin: buyurtma va rad etish vaqti hech qachon teng emas
    (aks holda qaysi biri oxirgi ekanini aniqlab bo'lmaydi - natija havolasi noto'g'ri chiqardi)."""
    from db import utc_now

    stamps = [utc_now() for _ in range(5000)]
    assert all(a < b for a, b in zip(stamps, stamps[1:]))
    src = (BOT_DIR / "handlers.py").read_text(encoding="utf-8")
    assert "created_at = utc_now()" in src and "record_rejection(user.id, ref, code, utc_now())" in src


def test_url_built_only_in_build_webapp_url():
    """Mini App havolasi bitta joyda: build_webapp_url -> webapp_info -> inline tugma (menyu tugmasi - buyruqlar)."""
    src = (BOT_DIR / "handlers.py").read_text(encoding="utf-8")
    assert src.count("WebAppInfo(") == 1, "WebAppInfo faqat webapp_info ichida"
    calls = [m.start() for m in re.finditer(r"webapp_url_with_result\(", src)]
    assert len(calls) == 2  # ta'rif + build_webapp_url ichida
    body = src[src.index("async def build_webapp_url"):src.index("async def webapp_info")]
    assert "webapp_url_with_result(" in body and "config.webapp_url" in body
    assert src.count("config.webapp_url") == 1
    assert src.count("await build_webapp_url(") == 1  # faqat webapp_info ichida
    assert src.count("await webapp_info(") == 1      # faqat inline tugma (order_keyboard)
    assert "MenuButtonWebApp" not in src, "matn maydoni yonidagi tugma Mini App emas"
    assert "ReplyKeyboardMarkup" not in src and "KeyboardButton(" not in src.replace("InlineKeyboardButton(", "")
    for other in BOT_DIR.glob("*.py"):
        if other.name != "handlers.py":
            text = other.read_text(encoding="utf-8")
            assert "WebAppInfo(" not in text and "build_webapp_url" not in text, other.name


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
