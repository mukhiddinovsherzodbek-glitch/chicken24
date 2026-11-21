"""Buyurtmalar guruhi va guruhdagi xatti-harakat testlari.

- guruhda bot jim: admin buyruqlari (admin va begona uchun ham, /buyruq@bot ham), /start, /til, matn, callback'lar;
- shaxsiy chatda admin buyruqlari faqat ADMIN_ID uchun; /guruh_id faqat guruhdagi admin uchun;
- chek ORDERS_CHAT_ID ga; guruh xatosida (ko'chgan ham) - admin'ga zaxira; hech qayerga yetmasa notified=0;
- bekor qilish xabari chek turgan chatga javob; ORDERS_ALSO_ADMIN;
- bot guruhga qo'shilganda (my_chat_member): begona - chiqib ketadi, admin - qoladi / chiqib ketib xabar beradi;
- sozlama tekshiruvi va loglarda chat ID yashirilishi.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import business  # noqa: E402
import config as config_module  # noqa: E402
import handlers  # noqa: E402
import logsafe  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.exceptions import TelegramMigrateToChat  # noqa: E402
from aiogram.methods import GetMe, LeaveChat, SendMessage  # noqa: E402
from aiogram.types import Update, User  # noqa: E402
from handlers import ReceiptData, deliver_receipt, on_cancel_order, on_web_app_data, resend_unnotified  # noqa: E402
from test_flow import Env, FakeBot, FakeCallback, good, make_message  # noqa: E402
from test_server import env as env_vars  # noqa: E402

business.is_open_now = lambda: True

ADMIN = 999          # test_flow.make_config dagi ADMIN_ID
STRANGER = 5
GROUP = -1001234567890
OTHER_GROUP = -1009876543210


def run(coro):
    return asyncio.run(coro)


def with_orders_chat(env: Env, chat_id: int | None = GROUP, also_admin: bool = False) -> Env:
    env.config = dataclasses.replace(env.config, orders_chat_id=chat_id, orders_also_admin=also_admin)
    return env


# ================================================================ haqiqiy Dispatcher: guruhda jimlik

class ApiBot(Bot):
    """Haqiqiy aiogram Bot, lekin API so'rovlari tarmoqqa chiqmaydi - yozib boriladi."""

    def __init__(self) -> None:
        super().__init__("123456:" + "A" * 35)
        self.calls: list = []

    async def __call__(self, method, request_timeout=None):
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Bot", username="chicken24bot")
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return SimpleNamespace(message_id=len(self.calls), chat=SimpleNamespace(id=method.chat_id))
        return True


def message_update(uid: int, chat_id: int, chat_type: str, text: str, n: int = 1) -> Update:
    chat = {"id": chat_id, "type": chat_type}
    if chat_type != "private":
        chat["title"] = "Xodimlar"
    data = {"update_id": n, "message": {
        "message_id": n, "date": 0, "chat": chat, "text": text,
        "from": {"id": uid, "is_bot": False, "first_name": "A"},
    }}
    if text.startswith("/"):
        data["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return Update.model_validate(data)


def callback_update(uid: int, chat_id: int, chat_type: str, data: str, n: int = 1) -> Update:
    chat = {"id": chat_id, "type": chat_type}
    if chat_type != "private":
        chat["title"] = "Xodimlar"
    return Update.model_validate({"update_id": n, "callback_query": {
        "id": str(n), "chat_instance": "ci", "data": data,
        "from": {"id": uid, "is_bot": False, "first_name": "A"},
        "message": {"message_id": 1, "date": 0, "chat": chat, "text": "x",
                    "from": {"id": 123456, "is_bot": True, "first_name": "Bot"}},
    }})


async def feed(env: Env, updates: list[Update]) -> ApiBot:
    bot = ApiBot()
    dp = Dispatcher(config=env.config, db=env.db, menu_store=env.menu_store, limiter=env.limiter)
    dp.include_router(handlers.router)
    try:
        for upd in updates:
            await dp.feed_update(bot, upd)
    finally:
        dp.sub_routers.remove(handlers.router)
        handlers.router._parent_router = None
    return bot


ADMIN_TEXTS = ["/excel", "/excel@chicken24bot", "/excel bugun", "/excel_bugun", "/excel_hafta", "/excel_oy",
               "/excel_hammasi", "/hisobot", "/hisobot@chicken24bot", "/backup", "/backup@chicken24bot"]
OTHER_TEXTS = ["/start", "/start@chicken24bot", "/til", "salom", "/guruh_id@boshqabot"]


def test_group_ignores_everything_for_admin_and_strangers():
    async def go():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(ADMIN, "uz", datetime.now(timezone.utc))
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(FakeBot()), lang="uz")  # buyurtma #1
        ups, n = [], 0
        for chat_type in ("group", "supergroup"):
            for uid in (ADMIN, STRANGER):
                for text in ADMIN_TEXTS + OTHER_TEXTS:
                    n += 1
                    ups.append(message_update(uid, GROUP, chat_type, text, n))
                for data in ("cancel:1", "lang:ru", "lang:uz:w"):
                    n += 1
                    ups.append(callback_update(uid, GROUP, chat_type, data, n))
        bot = await feed(env, ups)
        status = (await env.db.get_order(1))["status"]
        lang = await env.db.get_user_lang(ADMIN)
        await env.db.close()
        return bot.calls, status, lang

    calls, status, lang = run(go())
    real = [c for c in calls if not type(c).__name__ == "AnswerCallbackQuery"]
    assert real == [], [type(c).__name__ for c in real]
    assert status == "new", "guruhdagi callback buyurtmani bekor qilmadi"
    assert lang == "uz", "guruhdagi callback tilni o'zgartirmadi"


def test_private_admin_commands_only_for_admin():
    async def go():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(ADMIN, "uz", datetime.now(timezone.utc))
        await env.db.set_user_lang(STRANGER, "uz", datetime.now(timezone.utc))
        admin = await feed(env, [message_update(ADMIN, ADMIN, "private", "/hisobot")])
        stranger = await feed(env, [message_update(STRANGER, STRANGER, "private", t, i)
                                    for i, t in enumerate(["/hisobot", "/excel", "/backup", "/excel_bugun"], 1)])
        await env.db.close()
        return admin.calls, stranger.calls

    admin_calls, stranger_calls = run(go())
    assert len(admin_calls) == 1 and isinstance(admin_calls[0], SendMessage)
    assert admin_calls[0].chat_id == ADMIN and admin_calls[0].text.startswith("📊")
    assert stranger_calls == [], "begona foydalanuvchiga jim"


def test_group_id_only_for_admin_in_group():
    async def go():
        env = Env()
        await env.db.connect()
        await env.db.set_user_lang(ADMIN, "uz", datetime.now(timezone.utc))
        out = {}
        for name, upd in {
            "admin_group": message_update(ADMIN, GROUP, "group", "/guruh_id"),
            "admin_supergroup_mention": message_update(ADMIN, GROUP, "supergroup", "/guruh_id@chicken24bot"),
            "stranger_group": message_update(STRANGER, GROUP, "supergroup", "/guruh_id"),
            "admin_private": message_update(ADMIN, ADMIN, "private", "/guruh_id"),
        }.items():
            out[name] = (await feed(env, [upd])).calls
        await env.db.close()
        return out

    out = run(go())
    for name in ("admin_group", "admin_supergroup_mention"):
        assert len(out[name]) == 1 and out[name][0].chat_id == GROUP and str(GROUP) in out[name][0].text, name
        assert "ORDERS_CHAT_ID" in out[name][0].text
    assert out["stranger_group"] == [] and out["admin_private"] == []


# ================================================================ chek guruhga

def test_receipt_goes_to_orders_group_and_cancel_replies_there():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        bot = FakeBot()
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        row = await env.db.get_order(1)
        await on_cancel_order(FakeCallback(STRANGER, "cancel:1"), bot, env.config, env.db, lang="uz")
        await env.db.close()
        return bot, row

    bot, row = run(go())
    to_group = [s for s in bot.sent if s[1] == GROUP]
    to_admin = [s for s in bot.sent if s[1] == ADMIN]
    assert to_admin == [], "ORDERS_ALSO_ADMIN berilmagan - admin'ga nusxa yo'q"
    kinds = [s[0] for s in to_group]
    assert kinds == ["message", "location", "message"], kinds
    assert "Yangi buyurtma" in to_group[0][2] or "#1" in to_group[0][2]
    assert row["notified"] == 1 and row["admin_chat_id"] == GROUP and row["admin_msg_id"]
    cancel = to_group[2]
    assert "bekor qildi" in cancel[2] and cancel[3]["reply_parameters"].message_id == row["admin_msg_id"]


def test_orders_also_admin_sends_copy():
    async def go():
        env = with_orders_chat(Env(), also_admin=True)
        await env.db.connect()
        bot = FakeBot()
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        await on_cancel_order(FakeCallback(STRANGER, "cancel:1"), bot, env.config, env.db, lang="uz")
        await env.db.close()
        return bot

    bot = run(go())
    assert [s[0] for s in bot.sent if s[1] == GROUP] == ["message", "location", "message"]
    assert [s[0] for s in bot.sent if s[1] == ADMIN] == ["message", "location", "message"]


class GroupFailBot(FakeBot):
    def __init__(self, error: Exception, fail_admin: bool = False) -> None:
        super().__init__(fail_admin=fail_admin)
        self.error = error

    async def send_message(self, chat_id, text, **kw):
        if chat_id == GROUP:
            raise self.error
        return await super().send_message(chat_id, text, **kw)


def test_group_failure_falls_back_to_admin_with_warning():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        bot = GroupFailBot(RuntimeError("Forbidden: bot was kicked from the group chat"))
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        row = await env.db.get_order(1)
        await env.db.close()
        return bot, row

    logging.disable(logging.CRITICAL)
    try:
        bot, row = run(go())
    finally:
        logging.disable(logging.NOTSET)
    to_admin = [s for s in bot.sent if s[1] == ADMIN]
    assert "#1" in to_admin[0][2], "chek admin'ga zaxira sifatida"
    assert any("Guruhga yuborib bo'lmadi" in s[2] for s in to_admin if s[0] == "message")
    assert row["notified"] == 1 and row["admin_chat_id"] == ADMIN


def test_migrated_group_tells_admin_new_id():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        err = TelegramMigrateToChat(method=SendMessage(chat_id=GROUP, text="x"),
                                    message="group chat was upgraded to a supergroup chat",
                                    migrate_to_chat_id=-1005555555555)
        bot = GroupFailBot(err)
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        await env.db.close()
        return bot

    logging.disable(logging.CRITICAL)
    try:
        bot = run(go())
    finally:
        logging.disable(logging.NOTSET)
    notes = [s[2] for s in bot.sent if s[1] == ADMIN and s[0] == "message" and "Guruhga" in s[2]]
    assert notes and "-1005555555555" in notes[0] and "ORDERS_CHAT_ID" in notes[0]


def test_group_and_admin_both_fail_order_kept_and_resent():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        bot = GroupFailBot(RuntimeError("chat not found"), fail_admin=True)
        msg, answers = make_message(STRANGER, good())
        await on_web_app_data(msg, **env.deps(bot), lang="uz")
        before = (await env.db.get_order(1))["notified"]
        # guruh tuzaldi
        ok_bot = FakeBot()
        sent = await resend_unnotified(ok_bot, env.config, env.db)
        row = await env.db.get_order(1)
        await env.db.close()
        return answers, before, sent, row, ok_bot

    logging.disable(logging.CRITICAL)
    try:
        answers, before, sent, row, ok_bot = run(go())
    finally:
        logging.disable(logging.NOTSET)
    assert answers[0][0].startswith("✅"), "mijoz tasdiqni baribir oladi"
    assert before == 0 and sent == 1 and row["notified"] == 1 and row["admin_chat_id"] == GROUP
    assert ok_bot.sent[0][1] == GROUP and "Qayta yuborildi" in ok_bot.sent[0][2]


def test_old_order_without_chat_cancels_to_admin():
    async def go():
        env = with_orders_chat(Env())
        await env.db.connect()
        bot = FakeBot()
        await on_web_app_data(make_message(STRANGER, good())[0], **env.deps(bot), lang="uz")
        await env.db.conn.execute("UPDATE orders SET admin_chat_id = NULL")  # eski versiyada yozilgan chek
        await env.db.conn.commit()
        bot.sent.clear()
        await on_cancel_order(FakeCallback(STRANGER, "cancel:1"), bot, env.config, env.db, lang="uz")
        await env.db.close()
        return bot

    bot = run(go())
    cancel = [s for s in bot.sent if "bekor qildi" in s[2]]
    assert cancel and cancel[0][1] == ADMIN, "eski chek admin'da edi - javob ham o'sha yerga"


# ================================================================ bot guruhga qo'shildi

ADMIN_RIGHTS = {k: False for k in (
    "can_be_edited", "is_anonymous", "can_manage_chat", "can_delete_messages", "can_manage_video_chats",
    "can_restrict_members", "can_promote_members", "can_change_info", "can_invite_users",
    "can_post_stories", "can_edit_stories", "can_delete_stories", "can_send_welcome_messages")}


def member_update(adder: int, chat_id: int, old: str = "left", new: str = "member", chat_type: str = "supergroup"):
    new_member = {"status": new, "user": {"id": 123456, "is_bot": True, "first_name": "Bot"}}
    if new == "administrator":
        new_member.update(ADMIN_RIGHTS)
    return Update.model_validate({"update_id": 1, "my_chat_member": {
        "chat": {"id": chat_id, "type": chat_type, "title": "Xodimlar"},
        "from": {"id": adder, "is_bot": False, "first_name": "A"},
        "date": 0,
        "old_chat_member": {"status": old, "user": {"id": 123456, "is_bot": True, "first_name": "Bot"}},
        "new_chat_member": new_member,
    }})


def test_bot_leaves_foreign_groups():
    async def go():
        out = {}
        for name, orders_chat, upd in (
            ("stranger_adds", None, member_update(STRANGER, OTHER_GROUP)),
            ("stranger_adds_admin_status", GROUP, member_update(STRANGER, OTHER_GROUP, new="administrator")),
            ("admin_adds_no_setting", None, member_update(ADMIN, OTHER_GROUP)),
            ("admin_adds_orders_group", GROUP, member_update(ADMIN, GROUP)),
            ("admin_adds_other_group", GROUP, member_update(ADMIN, OTHER_GROUP)),
            ("bot_removed", GROUP, member_update(ADMIN, GROUP, old="member", new="left")),
        ):
            env = with_orders_chat(Env(), orders_chat)
            await env.db.connect()
            bot = await feed(env, [upd])
            await env.db.close()
            out[name] = bot.calls
        return out

    logging.disable(logging.CRITICAL)
    try:
        out = run(go())
    finally:
        logging.disable(logging.NOTSET)

    def left(calls):
        return [c.chat_id for c in calls if isinstance(c, LeaveChat)]

    assert left(out["stranger_adds"]) == [OTHER_GROUP] and len(out["stranger_adds"]) == 1
    assert left(out["stranger_adds_admin_status"]) == [OTHER_GROUP]
    assert out["admin_adds_no_setting"] == [], "ORDERS_CHAT_ID berilmagan - qoladi"
    assert out["admin_adds_orders_group"] == [], "o'z guruhi - qoladi"
    calls = out["admin_adds_other_group"]
    assert left(calls) == [OTHER_GROUP]
    dm = [c for c in calls if isinstance(c, SendMessage)]
    assert len(dm) == 1 and dm[0].chat_id == ADMIN and str(OTHER_GROUP) in dm[0].text
    assert out["bot_removed"] == []


# ================================================================ sozlama va loglar

def test_orders_chat_config():
    with env_vars(ORDERS_CHAT_ID="-1001234567890", ORDERS_ALSO_ADMIN="1"):
        cfg = config_module.load_config()
    assert cfg.orders_chat_id == GROUP and cfg.orders_also_admin is True
    assert str(GROUP) not in repr(cfg)
    with env_vars():
        cfg = config_module.load_config()
    assert cfg.orders_chat_id is None and cfg.orders_also_admin is False
    for bad in ("123", "abc", "0"):
        with env_vars(ORDERS_CHAT_ID=bad):
            try:
                config_module.load_config()
            except config_module.ConfigError as exc:
                assert "ORDERS_CHAT_ID" in str(exc)
            else:
                raise AssertionError(bad)


def test_chat_ids_masked_in_logs():
    logsafe.register_sensitive_ids(GROUP, 5012345678)
    out = logsafe.scrub(f"chek {GROUP} ga, admin 5012345678; buyurtma #12, summa 70000, user 15012345678")
    assert str(GROUP) not in out and "5012345678 " not in out.replace("15012345678", "")
    assert "-100*******890" in out and "*******678" in out
    assert "#12" in out and "70000" in out and "15012345678" in out, "boshqa sonlar o'zgarmaydi"


def test_env_example_has_group_placeholders():
    text = (BOT_DIR / ".env.example").read_text(encoding="utf-8")
    assert "\nORDERS_CHAT_ID=\n" in text and "\nORDERS_ALSO_ADMIN=\n" in text


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
