"""Salomlashuv testlari: rasm (tilga mos), file_id keshi, matnli zaxira, klaviatura (Telegram'siz)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import handlers  # noqa: E402
from aiogram.types import FSInputFile  # noqa: E402
from config import WELCOME_IMAGE_PATH, WELCOME_IMAGE_RU_PATH, Config  # noqa: E402
from handlers import WelcomePhoto, build_welcome_caption, order_keyboard, send_welcome  # noqa: E402

CONFIG = Config(bot_token="1:x", admin_id=999, webapp_url="https://example.pages.dev",
                menu_path=Path("menu.json"), db_path=Path("t.db"))


class FakeBot:
    """send_photo / send_message ni yozib boradi; photo_errors marta rasm yuborishda xato beradi."""

    def __init__(self, photo_errors: int = 0) -> None:
        self.photos: list = []
        self.texts: list = []
        self.photo_errors = photo_errors

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
        if self.photo_errors > 0:
            self.photo_errors -= 1
            raise RuntimeError("tarmoq xatosi (sinov)")
        self.photos.append((photo, caption, reply_markup))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id=f"FILE_{len(self.photos)}")])

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.texts.append((text, reply_markup))


def reset():
    WelcomePhoto.file_ids = {}
    handlers.WELCOME_IMAGE_PATH = WELCOME_IMAGE_PATH
    handlers.WELCOME_IMAGE_RU_PATH = WELCOME_IMAGE_RU_PATH
    handlers.business.is_open_now = lambda: True


def welcome(bot, lang="uz", name="Aziz"):
    asyncio.run(send_welcome(bot, 1, CONFIG, None, lang, name))  # baza yo'q -> faqat ?lang=


def url_query(markup) -> dict:
    return parse_qs(urlparse(markup.inline_keyboard[-1][0].web_app.url).query)


def test_welcome_images_exist():
    assert WELCOME_IMAGE_PATH.is_file(), WELCOME_IMAGE_PATH
    assert WELCOME_IMAGE_RU_PATH.is_file(), WELCOME_IMAGE_RU_PATH


def test_caption_content_and_escape():
    text = build_welcome_caption("<b>Ali</b>", is_open=True)
    assert text.startswith("Assalomu alaykum, &lt;b&gt;Ali&lt;/b&gt;! 👋")
    for part in ("Chicken24 ga xush kelibsiz!", "🕙 Ish vaqti: 10:00 – 03:00",
                 "🚚 Yetkazish: 10 000 so'mdan, masofaga qarab", "💵 To'lov: naqd",
                 "Buyurtma berish uchun quyidagi tugmani bosing 👇"):
        assert part in text, part
    assert "yopiqmiz" not in text
    assert "🍗" not in text


def test_caption_russian():
    text = build_welcome_caption("Ali", is_open=False, lang="ru")
    for part in ("Здравствуйте, Ali! 👋", "Добро пожаловать в Chicken24!", "🕙 Мы работаем с 10:00 до 03:00",
                 "🚚 Доставка: от 10 000 сум, в зависимости от расстояния", "💵 Оплата: наличными", "⛔ Сейчас мы закрыты, откроемся в 10:00.",
                 "Чтобы сделать заказ, нажмите кнопку ниже 👇"):
        assert part in text, part
    assert "so'm" not in text


def test_caption_closed_line_and_limit():
    text = build_welcome_caption("A" * 5000, is_open=False)
    assert "⛔ Hozir yopiqmiz, 10:00 da ochamiz." in text
    assert len(text) <= 1024


def test_first_upload_then_file_id_reused():
    reset()
    bot = FakeBot()
    welcome(bot)
    welcome(bot)
    first, second = bot.photos[0][0], bot.photos[1][0]
    assert isinstance(first, FSInputFile) and Path(first.path) == WELCOME_IMAGE_PATH  # 1-marta fayldan
    assert second == "FILE_1"  # keyin eng katta o'lchamdagi file_id
    markup = bot.photos[0][2]
    button = markup.inline_keyboard[-1][0]
    assert button.text == "Buyurtma berish" and url_query(markup) == {"lang": ["uz"]}
    assert len(markup.inline_keyboard) == 1, "rasm ostida bitta inline tugma"


def test_russian_welcome_uses_ru_image_and_keyboard():
    reset()
    bot = FakeBot()
    welcome(bot, "ru")
    photo, caption, markup = bot.photos[0]
    assert Path(photo.path) == WELCOME_IMAGE_RU_PATH
    assert caption.startswith("Здравствуйте")
    assert markup.inline_keyboard[-1][0].text == "Сделать заказ" and url_query(markup) == {"lang": ["ru"]}


def test_russian_image_missing_falls_back_to_uz_image():
    reset()
    handlers.WELCOME_IMAGE_RU_PATH = Path("yoq/welcome_ru.jpg")
    bot = FakeBot()
    welcome(bot, "ru")
    handlers.WELCOME_IMAGE_RU_PATH = WELCOME_IMAGE_RU_PATH
    photo, caption, _ = bot.photos[0]
    assert Path(photo.path) == WELCOME_IMAGE_PATH and caption.startswith("Здравствуйте")


def test_stale_file_id_reuploads():
    reset()
    WelcomePhoto.file_ids = {str(WELCOME_IMAGE_PATH): "ESKI_ID"}
    bot = FakeBot(photo_errors=1)
    welcome(bot)
    assert isinstance(bot.photos[0][0], FSInputFile) and WelcomePhoto.file_ids[str(WELCOME_IMAGE_PATH)] == "FILE_1"
    assert not bot.texts


def test_missing_file_falls_back_to_text():
    reset()
    handlers.WELCOME_IMAGE_PATH = Path("yoq/welcome.jpg")
    bot = FakeBot()
    welcome(bot)
    handlers.WELCOME_IMAGE_PATH = WELCOME_IMAGE_PATH
    assert not bot.photos and bot.texts and bot.texts[0][0].startswith("Assalomu alaykum")
    assert bot.texts[0][1].inline_keyboard[-1][0].text == "Buyurtma berish"


class ResultDB:
    """Faqat latest_result() - klaviatura havolasi natijani bazadan oladi."""

    def __init__(self, result):
        self.result = result

    async def latest_result(self, user_id):
        return self.result


def test_inline_order_button_single_and_keeps_result():
    db = ResultDB({"kind": "ok", "ref": "abc123", "order_id": 5, "reason": None})
    markup = asyncio.run(order_keyboard(CONFIG, db, 1, "uz"))
    assert len(markup.inline_keyboard) == 1 and len(markup.inline_keyboard[0]) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "Buyurtma berish" and button.callback_data is None
    # til + natijali havola (ok/n) birga - sendData zaxira yo'li uchun
    assert button.web_app.url == "https://example.pages.dev?lang=uz&ok=abc123&n=5"
    ru = asyncio.run(order_keyboard(CONFIG, ResultDB({"kind": "rej", "ref": "abc123", "order_id": None,
                                                       "reason": "limit"}), 1, "ru"))
    assert ru.inline_keyboard[0][0].web_app.url == "https://example.pages.dev?lang=ru&rej=abc123&r=limit"


def test_long_caption_photo_and_text_sent_separately():
    """Izoh 1024 belgidan oshsa: rasm izohsiz, matn va tugma alohida xabarda."""
    reset()
    original = handlers.build_welcome_caption
    handlers.build_welcome_caption = lambda *a, **kw: "Salom " + "x" * 1100
    try:
        bot = FakeBot()
        welcome(bot)
    finally:
        handlers.build_welcome_caption = original
    photo, caption, markup = bot.photos[0]
    assert caption is None and markup is None
    text, kb = bot.texts[0]
    assert len(text) > 1024 and kb.inline_keyboard[-1][0].text == "Buyurtma berish"


def test_welcome_keyboard_keeps_last_result():
    """/start (salomlashuv) klaviaturasi ham oxirgi natijani yo'qotmaydi."""
    reset()
    bot = FakeBot()
    db = ResultDB({"kind": "cx", "ref": "r9", "order_id": 9, "reason": None})
    asyncio.run(send_welcome(bot, 1, CONFIG, db, "ru", "Ali"))
    assert url_query(bot.photos[0][2]) == {"lang": ["ru"], "cx": ["r9"]}


def test_no_drumstick_emoji_in_bot_texts():
    for path in BOT_DIR.glob("*.py"):
        assert "🍗" not in path.read_text(encoding="utf-8"), path.name


def test_upload_error_falls_back_to_text():
    reset()
    bot = FakeBot(photo_errors=5)
    welcome(bot)
    assert not bot.photos and len(bot.texts) == 1


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)  # kutilgan xato loglari chiqmasin
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
