"""Buyruqlar menyusi (/ menyu) va admin buyruqlari himoyasi testlari - Telegram'siz."""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "tests"))

import commands  # noqa: E402
import config  # noqa: E402
from handlers import EXCEL_SHORTCUTS, cmd_backup, cmd_excel, cmd_excel_shortcut, cmd_report  # noqa: E402
from test_excel import Msg, seeded_db  # noqa: E402

CFG = config.Config(bot_token="1:x", admin_id=999, webapp_url="https://example.pages.dev",
                    menu_path=Path("menu.json"), db_path=Path("t.db"))


class MenuBot:
    """set_my_commands / delete_my_commands chaqiruvlarini yozib boradi."""

    def __init__(self, fail_admin_scope: bool = False) -> None:
        self.menus: dict[str, list[str]] = {}
        self.deleted: list[tuple[str, str | None]] = []
        self.fail_admin_scope = fail_admin_scope

    @staticmethod
    def _key(scope) -> str:
        return f"chat:{scope.chat_id}" if scope.type == "chat" else scope.type

    async def set_my_commands(self, cmds, scope=None, language_code=None, **kw):
        if self.fail_admin_scope and scope.type == "chat":
            raise RuntimeError("Bad Request: chat not found (sinov)")
        self.menus[self._key(scope)] = [c.command for c in cmds]

    async def delete_my_commands(self, scope=None, language_code=None, **kw):
        if self.fail_admin_scope and scope.type == "chat":
            raise RuntimeError("Bad Request: chat not found (sinov)")
        self.deleted.append((self._key(scope), language_code))


ADMIN_NAMES = ["start", "til", "excel", "excel_bugun", "excel_hafta", "excel_oy", "excel_hammasi", "hisobot", "backup"]


# ------------------------------------------------------------ menyu

def test_customer_menu_start_and_til_admin_full_list():
    bot = MenuBot()
    asyncio.run(commands.apply_commands(bot, 999))
    assert bot.menus["default"] == ["start", "til"]
    assert bot.menus["chat:999"] == ADMIN_NAMES
    # Eski buyruqlar barcha umumiy scope'lar va tillarda tozalangan
    scopes = {s for s, _ in bot.deleted}
    assert {"default", "all_private_chats", "all_group_chats", "all_chat_administrators", "chat:999"} <= scopes
    assert {lang for _, lang in bot.deleted} == {None, "uz", "ru", "en"}


def test_command_names_valid_and_descriptions_uzbek():
    for name, desc in commands.ADMIN_COMMANDS + commands.CUSTOMER_COMMANDS:
        assert commands.COMMAND_NAME_RE.fullmatch(name), name
        assert 1 <= len(desc) <= 256 and desc, name
    descs = dict(commands.ADMIN_COMMANDS)
    assert descs["excel"] == "Excel: oxirgi 30 kun" and descs["excel_bugun"] == "Excel: bugun"
    assert descs["excel_hammasi"] == "Excel: hamma buyurtmalar" and descs["backup"] == "Bazani zaxiralash"
    assert dict(commands.CUSTOMER_COMMANDS) == {
        "start": "Boshlash / Начать",
        "til": "Tilni o'zgartirish / Сменить язык",
    }
    assert descs["til"] == "Tilni o'zgartirish / Сменить язык"


def test_admin_scope_error_does_not_crash():
    bot = MenuBot(fail_admin_scope=True)
    asyncio.run(commands.apply_commands(bot, 999))  # xato tashlamasligi kerak
    assert bot.menus == {"default": ["start", "til"]}


# ------------------------------------------------------------ admin buyruqlari himoyasi

def _run_cmd(uid: int, call):
    async def scenario():
        db = await seeded_db()
        msg = Msg(uid)
        await call(msg, db)
        await db.close()
        return msg

    return asyncio.run(scenario())


class DocBot:
    def __init__(self) -> None:
        self.docs: list = []

    async def send_document(self, chat_id, document, caption=None, **kw):
        self.docs.append(chat_id)

    async def send_message(self, chat_id, text, **kw):
        self.docs.append(("msg", chat_id))


def test_non_admin_gets_silence_on_all_admin_commands():
    bot = DocBot()
    calls = {
        "/excel": lambda m, db: cmd_excel(m, SimpleNamespace(args=None), CFG, db),
        "/excel hammasi": lambda m, db: cmd_excel(m, SimpleNamespace(args="hammasi"), CFG, db),
        "/hisobot": lambda m, db: cmd_report(m, CFG, db),
        "/backup": lambda m, db: cmd_backup(m, bot, CFG, db),
    }
    for name in EXCEL_SHORTCUTS:
        calls["/" + name] = (lambda n: lambda m, db: cmd_excel_shortcut(m, SimpleNamespace(command=n), CFG, db))(name)
    assert set(calls) >= {"/excel_bugun", "/excel_hafta", "/excel_oy", "/excel_hammasi"}
    for name, call in calls.items():
        msg = _run_cmd(5, call)
        assert msg.texts == [] and msg.docs == [], f"{name}: admin bo'lmaganga javob bo'lmasligi kerak"
    assert bot.docs == []


def _sheet_values(msg: Msg) -> dict[str, list]:
    wb = load_workbook(io.BytesIO(msg.docs[0][0].data))
    # "Xulosa" dagi yaratilgan vaqt va davr satri soniyaga qarab farq qilishi mumkin - qolganlari solishtiriladi
    return {
        ws.title: [r for r in ws.iter_rows(values_only=True) if not (r and r[0] in ("Davr", "Yaratilgan"))]
        for ws in wb
    }


def test_shortcuts_equal_argument_form():
    async def both(name: str, arg: str):
        db = await seeded_db()  # ikkala buyruq BITTA bazada
        short, long = Msg(999), Msg(999)
        await cmd_excel_shortcut(short, SimpleNamespace(command=name), CFG, db)
        await cmd_excel(long, SimpleNamespace(args=arg), CFG, db)
        await db.close()
        return short, long

    for name, arg in EXCEL_SHORTCUTS.items():
        short, long = asyncio.run(both(name, arg))
        assert len(short.docs) == len(long.docs) == 1, name
        assert short.docs[0][0].filename == long.docs[0][0].filename
        assert short.docs[0][1].splitlines()[0] == long.docs[0][1].splitlines()[0]
        assert short.docs[0][1].splitlines()[-1] == long.docs[0][1].splitlines()[-1]
        assert _sheet_values(short) == _sheet_values(long), name


def test_aiogram_command_filter_routing():
    """Haqiqiy aiogram filtri: /excel_bugun -> qisqa buyruq handleri, '/excel bugun' -> /excel handleri."""
    from datetime import datetime, timezone

    from aiogram.filters import Command
    from aiogram.types import Chat, Message, User

    def msg(text: str) -> Message:
        return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"),
                       from_user=User(id=1, is_bot=False, first_name="A"), text=text)

    async def check(flt, text):
        return bool(await flt(msg(text), bot=None))

    excel_f, short_f = Command("excel"), Command(*EXCEL_SHORTCUTS)
    cases = {
        "/excel": (True, False),
        "/excel bugun": (True, False),
        "/excel_bugun": (False, True),
        "/excel_hammasi": (False, True),
        "/excel_yil": (False, False),
    }
    for text, (want_excel, want_short) in cases.items():
        got = (asyncio.run(check(excel_f, text)), asyncio.run(check(short_f, text)))
        assert got == (want_excel, want_short), (text, got)


def test_plain_excel_still_last_30_days():
    msg = _run_cmd(999, lambda m, db: cmd_excel(m, SimpleNamespace(args=None), CFG, db))
    assert "Oxirgi 30 kun" in msg.docs[0][1]


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
