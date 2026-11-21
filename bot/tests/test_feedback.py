"""Taklif va shikoyat: FEEDBACK_USERNAME sozlamasi, /taklif buyrug'i, guruh chekidagi @mention.

- mention faqat guruhdagi YANGI buyurtma chekining eng oxirgi qatorida (qayta yuborilgan chek ham);
- bekor qilish xabarida, admin nusxasida (ORDERS_ALSO_ADMIN), admin zaxirasida va guruhsiz rejimda yo'q;
- /taklif shaxsiy chatda uz/ru matn + URL tugma; guruhda jim.
"""
from __future__ import annotations

import dataclasses
import html
import logging
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import config as config_module  # noqa: E402
import i18n  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from handlers import group_mention, on_cancel_order, on_web_app_data, resend_unnotified  # noqa: E402
from test_flow import Env, FakeBot, FakeCallback, good, make_message  # noqa: E402
from test_groups import ADMIN, GROUP, STRANGER, GroupFailBot, feed, message_update, run, with_orders_chat  # noqa: E402
from test_server import env as env_vars  # noqa: E402

MENTION = "@Chicken24_uz"
URL = "https://t.me/Chicken24_uz"


def last_line(text: str) -> str:
    return text.rstrip("\n").split("\n")[-1]


def order_and_cancel(env: Env, bot) -> None:
    async def go():
        await env.db.connect()
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        await on_cancel_order(FakeCallback(STRANGER, "cancel:1"), bot, env.config, env.db, lang="uz")
        await env.db.close()

    run(go())


class TagCheck(HTMLParser):
    """Telegram HTML: teglar juft yopilganini tekshiradi."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, tag


# ------------------------------------------------------------------ sozlama

def test_feedback_username_default_env_and_validation():
    with env_vars():
        cfg = config_module.load_config()
    assert cfg.feedback_username == "Chicken24_uz" and cfg.feedback_url == URL
    for raw, expected in (("@Boshqa_Admin1", "Boshqa_Admin1"), ("  qwerty  ", "qwerty"), ("", "Chicken24_uz")):
        with env_vars(FEEDBACK_USERNAME=raw):
            assert config_module.load_config().feedback_username == expected, raw
    for bad in ("abc", "bad-name", "a b c d e", "x" * 33, "https://t.me/Chicken24_uz", "<b>abcde</b>"):
        with env_vars(FEEDBACK_USERNAME=bad):
            try:
                config_module.load_config()
            except config_module.ConfigError as exc:
                assert "FEEDBACK_USERNAME" in str(exc)
            else:
                raise AssertionError(bad)


def test_env_example_and_deploy_doc_mention_feedback_username():
    assert "FEEDBACK_USERNAME" in (BOT_DIR / ".env.example").read_text(encoding="utf-8")
    assert "FEEDBACK_USERNAME" in (BOT_DIR.parent / "deploy" / "DEPLOY_UZ.md").read_text(encoding="utf-8")


def test_i18n_feedback_keys_both_languages():
    assert i18n.t("uz", "btn.feedback") == "Taklif va shikoyat"
    assert i18n.t("ru", "btn.feedback") == "Предложения и жалобы"
    assert i18n.t("uz", "feedback.text") != i18n.t("ru", "feedback.text")
    assert set(i18n.TEXTS["uz"]) == set(i18n.TEXTS["ru"])


# ------------------------------------------------------------------ guruh chekidagi mention

def test_group_receipt_ends_with_mention_and_html_stays_valid():
    env = with_orders_chat(Env())
    bot = FakeBot()
    order_and_cancel(env, bot)
    group_msgs = [s for s in bot.sent if s[1] == GROUP and s[0] == "message"]
    receipt, cancel = group_msgs
    assert "#1" in receipt[2] and last_line(receipt[2]) == MENTION
    assert receipt[2].count(MENTION) == 1
    assert receipt[2].endswith("\n\n" + MENTION), "alohida, eng oxirgi qator"
    checker = TagCheck()
    checker.feed(receipt[2])
    assert checker.stack == [], "HTML teglar buzilmagan"
    assert MENTION not in cancel[2], "bekor qilish xabarida mention yo'q"


def test_mention_is_html_safe():
    cfg = Env().config
    assert html.escape(group_mention(cfg)) == group_mention(cfg) == "\n\n" + MENTION
    other = dataclasses.replace(cfg, feedback_username="Boshqa_admin")
    assert group_mention(other) == "\n\n@Boshqa_admin"


def test_admin_copy_has_no_mention():
    env = with_orders_chat(Env(), also_admin=True)
    bot = FakeBot()
    order_and_cancel(env, bot)
    to_group = [s[2] for s in bot.sent if s[1] == GROUP and s[0] == "message"]
    to_admin = [s[2] for s in bot.sent if s[1] == ADMIN and s[0] == "message"]
    assert last_line(to_group[0]) == MENTION
    assert to_admin and all(MENTION not in text for text in to_admin), "admin nusxasida mention yo'q"


def test_without_orders_group_no_mention():
    env = Env()  # ORDERS_CHAT_ID yo'q - chek admin'ga
    bot = FakeBot()
    order_and_cancel(env, bot)
    to_admin = [s[2] for s in bot.sent if s[1] == ADMIN and s[0] == "message"]
    assert to_admin and "#1" in to_admin[0] and all(MENTION not in text for text in to_admin)


def test_group_failure_admin_fallback_has_no_mention():
    env = with_orders_chat(Env())
    bot = GroupFailBot(RuntimeError("Forbidden"))
    logging.disable(logging.CRITICAL)
    try:
        order_and_cancel(env, bot)
    finally:
        logging.disable(logging.NOTSET)
    to_admin = [s[2] for s in bot.sent if s[1] == ADMIN and s[0] == "message"]
    assert "#1" in to_admin[0] and all(MENTION not in text for text in to_admin)


def test_resent_receipt_to_group_has_mention():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        failing = GroupFailBot(RuntimeError("chat not found"), fail_admin=True)
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(failing), lang="uz")
        ok_bot = FakeBot()
        await resend_unnotified(ok_bot, env.config, env.db)
        await env.db.close()
        return ok_bot

    logging.disable(logging.CRITICAL)
    try:
        ok_bot = run(go())
    finally:
        logging.disable(logging.NOTSET)
    text = ok_bot.sent[0][2]
    assert ok_bot.sent[0][1] == GROUP and "Qayta yuborildi" in text and last_line(text) == MENTION


# ------------------------------------------------------------------ /taklif

def taklif_calls(lang: str, chat_type: str = "private", text: str = "/taklif"):
    async def go():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(STRANGER, lang, datetime.now(timezone.utc))
        chat_id = STRANGER if chat_type == "private" else GROUP
        bot = await feed(env, [message_update(STRANGER, chat_id, chat_type, text)])
        await env.db.close()
        return bot.calls

    return run(go())


def test_taklif_command_uz():
    calls = taklif_calls("uz")
    assert len(calls) == 1 and isinstance(calls[0], SendMessage) and calls[0].chat_id == STRANGER
    assert calls[0].text == i18n.t("uz", "feedback.text")
    (button,), = calls[0].reply_markup.inline_keyboard
    assert button.text == "Taklif va shikoyat" and button.url == URL


def test_taklif_command_ru():
    calls = taklif_calls("ru", text="/taklif@chicken24bot")
    assert calls[0].text == i18n.t("ru", "feedback.text")
    (button,), = calls[0].reply_markup.inline_keyboard
    assert button.text == "Предложения и жалобы" and button.url == URL


def test_taklif_silent_in_group():
    assert taklif_calls("uz", chat_type="supergroup") == []


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
