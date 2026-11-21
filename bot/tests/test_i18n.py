"""Ikki tillilik (uz/ru) testlari: lug'atlar, menu.json ruscha maydonlari, til tanlash oqimi (Telegram'siz)."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import handlers  # noqa: E402
import i18n  # noqa: E402
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User  # noqa: E402
from config import WELCOME_IMAGE_PATH, WELCOME_IMAGE_RU_PATH  # noqa: E402
from handlers import (  # noqa: E402
    LanguageMiddleware,
    WelcomePhoto,
    cmd_lang,
    cmd_start,
    on_lang_chosen,
    on_web_app_data,
)
from test_flow import Env, FakeBot, good, make_message, order_button, NO_LOC  # noqa: E402

business.is_open_now = lambda: True

# Taom nomlari tarjimadan OLDINGI holati (ruscha qo'shilganda ham o'zgarmasligi kerak)
EXPECTED_NAMES = {
    "burger-1": "Chicken Burger", "burger-2": "Chicken Cheese", "burger-3": "Chicken Hot", "burger-4": "Chicken Big",
    "burger-5": "Beef Burger", "burger-6": "Beef Cheese", "burger-7": "Beef Hot", "burger-8": "Beef Big",
    "lavash-1": "Chicken Lavash", "lavash-2": "Lavash Cheese", "lavash-3": "Lavash Hot",
    "pizza-1": "Margarita", "pizza-2": "Peperoni", "pizza-3": "Tovuqli", "pizza-4": "Go'shtli",
    "pizza-5": "Achchiq", "pizza-6": "Maxsus",
    "chicken-1": "Original Wings (6 dona)", "chicken-2": "Original Wings (10 dona)", "chicken-3": "Original Wings (1 kg)",
    "chicken-4": "Hot Wings (6 dona)", "chicken-5": "Hot Wings (10 dona)", "chicken-6": "Hot Wings (1 kg)",
    "chicken-7": "Chicken Strips (5 dona)", "chicken-8": "Chicken Strips (8 dona)", "chicken-9": "Chicken Strips (1 kg)",
    "chicken-10": "Chicken Fillet (2 dona)", "chicken-11": "Chicken Fillet (3 dona)", "chicken-12": "Chicken Fillet (1 kg)",
    "chicken-13": "Chicken Bites (10 dona)", "chicken-14": "Chicken Bites (15 dona)", "chicken-15": "Chicken Bites (1 kg)",
    "chicken-16": "Chicken Legs (3 dona)", "chicken-17": "Chicken Legs (5 dona)", "chicken-18": "Chicken Legs (1 kg)",
    "chicken-21": "Chicken Mangal (1 kg)", "chicken-22": "Butun tovuq (1 dona)", "chicken-23": "Chicken Mix (1 kg)",
    "chicken-24": "Chicken Mix (12 dona)", "kombo-1": "1 kg Chicken + 2 fri + 2 sous",
    "kombo-2": "1 kg Chicken + 1 L Pepsi + 2 sous", "kombo-3": "Bir Oila Kombo",
    "xachapuri-1": "Xachapuri Adjarskiy", "xachapuri-2": "Xachapuri Klassik",
    "qoshimchalar-1": "Kartoshka Fri", "qoshimchalar-2": "Kartoshka Shariklari", "qoshimchalar-3": "Kartoshka Derevenskiy",
    "qoshimchalar-4": "Sirniye Palochki", "qoshimchalar-5": "Sirniye Shariklar", "qoshimchalar-6": "Chesnochniy Xleb",
    "qoshimchalar-7": "Sezar salat", "qoshimchalar-8": "Yashil salat", "qoshimchalar-9": "Ketchup",
    "qoshimchalar-10": "Sarimsoq sousi", "qoshimchalar-11": "Pishloq sousi", "qoshimchalar-12": "Achchiq sous",
}

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def run(coro):
    return asyncio.run(coro)


def load_miniapp_i18n() -> dict:
    text = (ROOT / "miniapp" / "i18n.js").read_text(encoding="utf-8")
    body = text.split("window.CHICKEN24_I18N =", 1)[1].strip()
    return json.loads(body.rstrip(";").strip())


# ------------------------------------------------------------ lug'atlar

def test_bot_dictionaries_have_same_keys():
    uz, ru = set(i18n.TEXTS["uz"]), set(i18n.TEXTS["ru"])
    assert uz == ru, {"faqat uz": sorted(uz - ru), "faqat ru": sorted(ru - uz)}
    for key in uz:
        assert i18n.TEXTS["ru"][key].strip(), key
        # parametrlar ham bir xil bo'lishi kerak
        assert set(re.findall(r"\{(\w+)\}", i18n.TEXTS["uz"][key])) == set(re.findall(r"\{(\w+)\}", i18n.TEXTS["ru"][key])), key


def test_bot_ru_texts_are_russian():
    skip = {"lang.prompt", "lang.btn_uz", "lang.btn_ru"}  # ikki tilda / tugma nomlari
    for key, text in i18n.TEXTS["ru"].items():
        if key in skip or not re.search(r"[A-Za-zА-Яа-я]{3}", text):
            continue
        assert CYRILLIC.search(text), f"ruscha emas: {key}"


def test_miniapp_dictionaries_have_same_keys():
    d = load_miniapp_i18n()
    uz, ru = set(d["uz"]), set(d["ru"])
    assert uz == ru, {"faqat uz": sorted(uz - ru), "faqat ru": sorted(ru - uz)}
    for key in uz:
        assert set(re.findall(r"\{(\w+)\}", d["uz"][key])) == set(re.findall(r"\{(\w+)\}", d["ru"][key])), key


def strip_js_comments(src: str) -> str:
    """// va /* */ izohlarni olib tashlaydi; '...', "...", `...` ichiga tegmaydi.
    (app.js dagi regex literallarida qo'shtirnoq va // yo'q - shuning uchun yetarli.)"""
    out, i, n, quote = [], 0, len(src), None
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
        elif ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
        elif src.startswith("//", i):
            i = src.find("\n", i) if "\n" in src[i:] else n
        elif src.startswith("/*", i):
            i = src.find("*/", i + 2) + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def test_miniapp_uses_only_existing_keys_and_no_hardcoded_texts():
    d = load_miniapp_i18n()
    src = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
    # Izohlarsiz kod (izohlarda T('kalit') kabi misollar bor)
    code = strip_js_comments(src)
    used = set(re.findall(r"\bT\('([\w.]+)'", code)) | {k + s for k in re.findall(r"\bTP\('([\w.]+)'", code)
                                                         for s in ("_one", "_few", "_many")}
    used |= {f"reject.{c}" for c in ("limit", "menu", "area", "data", "server", "closed")}
    used |= {"loc.denied", "loc.no_gps", "loc.gps_timeout"}  # xaritadan (messages) olinadi
    missing = sorted(k for k in used if k not in d["uz"])
    assert not missing, missing
    # Izohlarsiz kodda mijozga ko'rinadigan o'zbekcha/ruscha matn qolmasin
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`", code)
    bad_words = re.compile(r"\b(buyurtma|savat|manzil|qo'lda|qo\\'lda|yetkaz|bekor|taom|telefon raqam|so\\'m|qabul)\w*", re.I)
    offenders = [a or b for a, b in literals if bad_words.search(a or b) or CYRILLIC.search(a or b)]
    offenders = [o for o in offenders if not o.startswith(("Menyu yuklanmadi", "sendData xatosi"))]  # console log
    assert not offenders, offenders


# ------------------------------------------------------------ menu.json

def test_menu_has_russian_fields_and_names_unchanged():
    menu = json.loads((ROOT / "miniapp" / "menu.json").read_text(encoding="utf-8"))
    for cat in menu["categories"]:
        assert cat.get("name_ru", "").strip(), cat["id"]
        assert CYRILLIC.search(cat["name_ru"]), cat["id"]
    names = {}
    for it in menu["items"]:
        names[it["id"]] = it["name"]
        if it.get("desc"):
            assert it.get("desc_ru", "").strip(), f"{it['id']}: desc_ru yo'q"
        if it.get("items"):
            assert len(it.get("items_ru", [])) == len(it["items"]), f"{it['id']}: items_ru"
        if it.get("variant"):
            assert it.get("variant_ru", "").strip(), f"{it['id']}: variant_ru"
        assert "name_ru" not in it, f"{it['id']}: taom nomi tarjima qilinmaydi"
    # Avvalgi taomlar nomi o'zgarmagan (tarjima qilinmagan); yangi taomlar qo'shilishi mumkin
    assert {k: names.get(k) for k in EXPECTED_NAMES} == EXPECTED_NAMES
    assert all(name.strip() for name in names.values())


def test_kombo_items_ru_keep_product_names():
    menu = {i["id"]: i for i in json.loads((ROOT / "miniapp" / "menu.json").read_text(encoding="utf-8"))["items"]}
    assert "Chicken" in menu["kombo-1"]["items_ru"][0] and "Pepsi" in menu["kombo-2"]["items_ru"][1]


# ------------------------------------------------------------ til tanlash oqimi (bot)

class LangBot(FakeBot):
    """FakeBot + send_photo (salomlashuv) va xabarlar ro'yxati."""

    def __init__(self) -> None:
        super().__init__()
        self.photos: list = []

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        self.photos.append((chat_id, photo, caption, reply_markup))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="F")])


def tg_message(user_id: int, text: str) -> Message:
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=user_id, type="private"),
                   from_user=User(id=user_id, is_bot=False, first_name="Ali"), text=text)


async def through_middleware(env: Env, bot, event, handler_called: list):
    async def handler(ev, data):
        handler_called.append(data.get("lang"))
    data = {"db": env.db, "config": env.config, "bot": bot, "event_from_user": event.from_user}
    return await LanguageMiddleware()(handler, event, data)


def prompts(bot) -> list:
    return [s for s in bot.sent if s[0] == "message" and s[2] == "Tilni tanlang / Выберите язык"]


def test_start_asks_language_first_and_old_customer_too():
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        called: list = []
        await through_middleware(env, bot, tg_message(40, "/start"), called)   # yangi mijoz
        await through_middleware(env, bot, tg_message(41, "salom"), called)    # eski mijoz (tili yo'q)
        await env.db.close()
        return bot, called

    bot, called = run(scenario())
    assert called == [], "til tanlanmaguncha handler ishlamasligi kerak"
    assert len(prompts(bot)) == 2
    buttons = prompts(bot)[0][3]["reply_markup"].inline_keyboard[0]
    assert [b.text for b in buttons] == ["🇺🇿 O'zbekcha", "🇷🇺 Русский"]
    assert [b.callback_data for b in buttons] == ["lang:uz", "lang:ru"]


def test_middleware_passes_saved_lang_admin_and_web_app_data():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(42, "ru", datetime.now(timezone.utc))
        bot = LangBot()
        called: list = []
        await through_middleware(env, bot, tg_message(42, "salom"), called)    # tili bor -> o'tadi (ru)
        await through_middleware(env, bot, tg_message(999, "/hisobot"), called)  # admin buyrug'i -> o'tadi
        mw = LanguageMiddleware()  # Mini App buyurtmasi (web_app_data) -> o'tadi

        async def handler(ev, data):
            called.append(("wad", data.get("lang")))
        await mw(handler, Message.model_construct(message_id=2, date=datetime.now(timezone.utc),
                                                  chat=Chat(id=43, type="private"),
                                                  from_user=User(id=43, is_bot=False, first_name="A"),
                                                  web_app_data=SimpleNamespace(data="{}")),
                 {"db": env.db, "config": env.config, "bot": bot,
                  "event_from_user": User(id=43, is_bot=False, first_name="A")})
        await env.db.close()
        return bot, called

    bot, called = run(scenario())
    assert called == ["ru", None, ("wad", None)]
    assert prompts(bot) == []


def _callback(user_id: int, data: str):
    edits: list = []

    async def edit_text(text, **kw):
        edits.append(text)

    async def answer(*a, **kw):
        return None

    cb = SimpleNamespace(from_user=SimpleNamespace(id=user_id, first_name="Ali"), data=data,
                         message=SimpleNamespace(edit_text=edit_text), answer=answer)
    return cb, edits


def test_choosing_language_sends_welcome_with_right_image():
    async def scenario():
        env = Env()
        await env.db.connect()
        WelcomePhoto.file_ids = {}
        bot = LangBot()
        cb_ru, edits_ru = _callback(50, "lang:ru")
        await on_lang_chosen(cb_ru, bot, env.config, env.db, lang=None)
        cb_uz, edits_uz = _callback(51, "lang:uz")
        await on_lang_chosen(cb_uz, bot, env.config, env.db, lang=None)
        langs = (await env.db.get_user_lang(50), await env.db.get_user_lang(51))
        await env.db.close()
        return bot, langs, edits_ru, edits_uz

    bot, langs, edits_ru, edits_uz = run(scenario())
    assert langs == ("ru", "uz")
    assert edits_ru == ["🇷🇺 Выбран русский язык"] and edits_uz == ["🇺🇿 O'zbekcha tanlandi"]
    (c1, p1, cap1, kb1), (c2, p2, cap2, kb2) = bot.photos
    assert Path(p1.path) == WELCOME_IMAGE_RU_PATH and cap1.startswith("Здравствуйте, Ali!")
    assert order_button(kb1).text == "Сделать заказ"
    assert parse_qs(urlparse(order_button(kb1).web_app.url).query) == {"lang": ["ru"]}
    assert Path(p2.path) == WELCOME_IMAGE_PATH and cap2.startswith("Assalomu alaykum, Ali!")
    assert parse_qs(urlparse(order_button(kb2).web_app.url).query) == {"lang": ["uz"]}


def test_til_command_changes_language_and_resends_keyboard():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(60, "uz", datetime.now(timezone.utc))
        bot = LangBot()
        await cmd_lang(SimpleNamespace(chat=SimpleNamespace(id=60)), bot)
        cb, _ = _callback(60, "lang:ru")
        await on_lang_chosen(cb, bot, env.config, env.db, lang="uz")  # avvalgi til bor -> almashtirish
        lang = await env.db.get_user_lang(60)
        await env.db.close()
        return bot, lang

    bot, lang = run(scenario())
    assert lang == "ru"
    assert len(prompts(bot)) == 1  # /til - til tanlash xabari
    confirm = [s for s in bot.sent if s[0] == "message" and s[2].startswith("✅ Язык изменён")]
    assert confirm and order_button(confirm[0][3]["reply_markup"]).text == "Сделать заказ"
    url = order_button(confirm[0][3]["reply_markup"]).web_app.url
    assert parse_qs(urlparse(url).query) == {"lang": ["ru"]}
    assert bot.photos == [], "tilni almashtirganda salomlashuv rasmi qayta yuborilmaydi"


async def start_via_middleware(env: Env, bot, user_id: int) -> None:
    """/start - haqiqiy yo'l: middleware (til saqlangan bo'lsa o'tkazadi) -> cmd_start."""
    async def handler(ev, data):
        return await cmd_start(ev, data["bot"])
    data = {"db": env.db, "config": env.config, "bot": bot,
            "event_from_user": User(id=user_id, is_bot=False, first_name="Ali")}
    await LanguageMiddleware()(handler, tg_message(user_id, "/start"), data)


def test_start_always_asks_language_even_if_saved():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(70, "uz", datetime.now(timezone.utc))
        await env.db.set_user_lang(999, "ru", datetime.now(timezone.utc))  # admin
        bot = LangBot()
        WelcomePhoto.file_ids = {}
        await start_via_middleware(env, bot, 70)
        await start_via_middleware(env, bot, 999)
        before_choice = (list(bot.photos), list(bot.sent))
        cb, edits = _callback(70, "lang:ru:w")
        await on_lang_chosen(cb, bot, env.config, env.db, lang="uz")
        lang = await env.db.get_user_lang(70)
        await env.db.close()
        return bot, before_choice, edits, lang

    bot, (photos_before, sent_before), edits, lang = run(scenario())
    # 1) /start -> faqat til so'rovi (mijozga ham, admin'ga ham); salomlashuv hali yo'q
    assert len(sent_before) == 2 and photos_before == []
    for s in sent_before:
        assert s[2] == "Tilni tanlang / Выберите язык"
        markup = s[3]["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup), "eski reply-klaviatura (Mini App tugmasi) olib tashlanmaydi"
        assert [b.callback_data for b in markup.inline_keyboard[0]] == ["lang:uz:w", "lang:ru:w"]
    # 2) tanlangach: til almashdi, ruscha rasm + salomlashuv + klaviatura
    assert lang == "ru" and edits == ["🇷🇺 Выбран русский язык"]
    photo, caption, markup = bot.photos[0][1], bot.photos[0][2], bot.photos[0][3]
    assert Path(photo.path) == WELCOME_IMAGE_RU_PATH and caption.startswith("Здравствуйте, Ali!")
    assert order_button(markup).text == "Сделать заказ"
    assert parse_qs(urlparse(order_button(markup).web_app.url).query) == {"lang": ["ru"]}
    assert not [s for s in bot.sent if s[0] == "message" and s[2].startswith("✅ Язык изменён")]


def test_start_same_language_still_welcomes_with_uz_image():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(71, "uz", datetime.now(timezone.utc))
        bot = LangBot()
        WelcomePhoto.file_ids = {}
        cb, _ = _callback(71, "lang:uz:w")
        await on_lang_chosen(cb, bot, env.config, env.db, lang="uz")
        await env.db.close()
        return bot

    bot = run(scenario())
    assert len(bot.photos) == 1 and Path(bot.photos[0][1].path) == WELCOME_IMAGE_PATH
    assert bot.photos[0][2].startswith("Assalomu alaykum, Ali!")


def test_til_prompt_has_no_welcome_flag_and_bad_flag_ignored():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(72, "uz", datetime.now(timezone.utc))
        bot = LangBot()
        await cmd_lang(SimpleNamespace(chat=SimpleNamespace(id=72)), bot)
        cb, edits = _callback(72, "lang:ru:x")  # noma'lum belgi - e'tiborsiz
        await on_lang_chosen(cb, bot, env.config, env.db, lang="uz")
        lang = await env.db.get_user_lang(72)
        await env.db.close()
        return bot, edits, lang

    bot, edits, lang = run(scenario())
    assert [b.callback_data for b in prompts(bot)[0][3]["reply_markup"].inline_keyboard[0]] == ["lang:uz", "lang:ru"]
    assert edits == [] and lang == "uz" and bot.photos == []


# ------------------------------------------------------------ buyurtma javoblari mijoz tilida

def test_order_answer_in_russian_admin_receipt_in_uzbek():
    async def scenario():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(80, "ru", datetime.now(timezone.utc))
        bot = FakeBot()
        msg, answers = make_message(80, good())
        await on_web_app_data(msg, **env.deps(bot), lang="ru")
        await env.db.close()
        return answers, bot.sent

    answers, sent = run(scenario())
    text, markup = answers[0]
    assert text.startswith("✅ <b>Ваш заказ принят!</b>") and "Номер заказа: <b>#1</b>" in text
    assert "Klassik burger × 2 = 50 000 сум" in text, "taom nomi o'zbekcha, valyuta ruscha"
    assert "Итого: 90 000 сум" in text and "Оплата: наличными курьеру" in text
    q = parse_qs(urlparse(order_button(markup).web_app.url).query)
    assert q == {"lang": ["ru"], "ok": ["abc123xyz"], "n": ["1"]}
    assert order_button(markup).text == "Сделать заказ"
    offer_text, offer_markup = answers[1]
    assert offer_text.startswith("Передумали?") and offer_markup.inline_keyboard[0][0].text == "❌ Отменить заказ"
    receipt = sent[0][2]
    assert "yangi buyurtma #1" in receipt and "Jami: 90 000 so'm" in receipt and "сум" not in receipt


def test_rejection_in_russian_no_location():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(81, good(**NO_LOC))  # Toshkent
        await on_web_app_data(msg, **env.deps(FakeBot()), lang="ru")
        await env.db.close()
        return answers

    text, markup = run(scenario())[0]
    assert text.startswith("❌ Заказ не принят.") and "местоположение" in text and "Самарканд" not in text
    assert parse_qs(urlparse(order_button(markup).web_app.url).query)["r"] == ["location"]


def test_order_without_saved_lang_uses_payload_lang_and_saves_it():
    async def scenario():
        env = Env()
        await env.db.connect()
        msg, answers = make_message(82, good(lang="ru"))
        await on_web_app_data(msg, **env.deps(FakeBot()), lang=None)
        saved = await env.db.get_user_lang(82)
        await env.db.close()
        return answers, saved

    answers, saved = run(scenario())
    assert saved == "ru" and answers[0][0].startswith("✅ <b>Ваш заказ принят!</b>")


def test_closed_and_limit_in_russian():
    async def scenario():
        env = Env()
        await env.db.connect()
        business.is_open_now = lambda: False
        try:
            msg, closed = make_message(83, good())
            await on_web_app_data(msg, **env.deps(FakeBot()), lang="ru")
        finally:
            business.is_open_now = lambda: True
        await on_web_app_data(make_message(84, good())[0], **env.deps(FakeBot()), lang="ru")
        msg, limited = make_message(84, good(ref="again123"))
        await on_web_app_data(msg, **env.deps(FakeBot()), lang="ru")
        await env.db.close()
        return closed, limited

    closed, limited = run(scenario())
    assert "Мы работаем с 10:00 до 03:00" in closed[0][0]
    assert limited[0][0].startswith("⏳ Заказ не принят")


def test_fallback_text_in_customer_language():
    """Har qanday matn (eski mijoz ham): pastki klaviatura olib tashlanadi, menyu tugmasi va inline tugma."""
    async def scenario():
        env = Env()
        await env.db.connect()
        bot = LangBot()
        out: list = []

        async def answer(text, **kw):
            out.append((text, kw.get("reply_markup")))

        def msg(text):
            return SimpleNamespace(answer=answer, text=text, chat=SimpleNamespace(id=5),
                                   from_user=SimpleNamespace(id=5))

        await handlers.fallback_text(msg("salom"), bot, env.config, env.db, lang="ru")
        await handlers.fallback_text(msg("salom"), bot, env.config, env.db, lang="uz")
        await handlers.fallback_text(msg("/excel"), bot, env.config, env.db, lang="uz")  # admin buyrug'i - javobsiz
        await env.db.close()
        return out, bot

    out, bot = run(scenario())
    (ru, ru_kb), (uz, uz_kb) = out
    assert len(out) == 2, "begona foydalanuvchining admin buyrug'iga javob yo'q"
    assert "«Сделать заказ»" in ru and "«Buyurtma berish»" in uz
    assert order_button(ru_kb).text == "Сделать заказ" and order_button(uz_kb).text == "Buyurtma berish"
    assert bot.kb_removed == [5], "pastki klaviatura BIR MARTA olib tashlanadi (har xabarda '⌛' chiqmaydi)"
    assert [b.text for _, b in bot.menu_buttons] == ["Заказ", "Buyurtma"], "menyu tugmasi faqat til o'zgarganda"


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
