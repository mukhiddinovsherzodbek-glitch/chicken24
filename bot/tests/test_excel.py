"""/excel eksport testlari - Telegram'siz (fayl xotirada, openpyxl bilan qayta o'qiladi)."""
from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

import config  # noqa: E402
import excel_export  # noqa: E402
from db import Database  # noqa: E402
from handlers import cmd_excel  # noqa: E402
from menu import parse_menu  # noqa: E402
from validators import validate_order  # noqa: E402

SKD = config.LOCAL_TZ
CFG = config.Config(bot_token="1:x", admin_id=999, webapp_url="https://example.pages.dev",
                    menu_path=Path("menu.json"), db_path=Path("t.db"))
MENU = parse_menu({
    "categories": [{"id": "c", "name": "C"}],
    "items": [
        {"id": "w10", "category": "c", "name": "Original Wings (10 dona)", "price": 55000},
        {"id": "pepsi", "category": "c", "name": "Pepsi", "price": 10000},
    ],
})


def local(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=SKD).astimezone(timezone.utc)


def order(items, location=False):
    data = {"name": "Aziz", "phone": "+998901234567", "items": [{"id": i, "qty": q} for i, q in items],
            "address_details": "12-uy, 3-qavat", "comment": "Domofon 45"}
    if location:
        data.update(address_type="location", lat=39.6548, lon=66.9757)
    else:
        data.update(address_type="text", address_text="Samarqand, Registon 5")
    return validate_order(data, MENU, delivery_fee=10000)


async def seeded_db() -> Database:
    """3 ta buyurtma: 1-qabul (lokatsiya), 2-bekor qilingan, 3-qabul (matnli manzil)."""
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    await db.connect()
    t = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db.save_order(user_id=1, username="aziz", order=order([("w10", 2), ("pepsi", 1)], location=True), created_at=t)
    oid = await db.save_order(user_id=2, username=None, order=order([("w10", 1)]), created_at=t)
    await db.conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (oid,))
    await db.conn.commit()
    await db.save_order(user_id=3, username=None, order=order([("pepsi", 3)]), created_at=t)
    return db


class Msg:
    def __init__(self, uid: int, chat_type: str = "private", chat_id: int | None = None) -> None:
        self.from_user = SimpleNamespace(id=uid)
        self.chat = SimpleNamespace(id=uid if chat_id is None else chat_id, type=chat_type)
        self.texts: list[str] = []
        self.docs: list = []

    async def answer(self, text, **kw):
        self.texts.append(text)

    async def answer_document(self, document, caption=None, **kw):
        self.docs.append((document, caption))


def excel(uid: int, args: str | None, db_factory=seeded_db) -> Msg:
    async def scenario():
        db = await db_factory()
        msg = Msg(uid)
        await cmd_excel(msg, SimpleNamespace(args=args), CFG, db)
        await db.close()
        return msg

    return asyncio.run(scenario())


def workbook_of(msg: Msg):
    document, _ = msg.docs[0]
    return load_workbook(io.BytesIO(document.data))


# ------------------------------------------------------------ ruxsat

def test_non_admin_gets_nothing():
    msg = excel(5, None)
    assert msg.texts == [] and msg.docs == []


# ------------------------------------------------------------ fayl tarkibi

def test_three_sheets_and_filename():
    msg = excel(999, "hammasi")
    document, caption = msg.docs[0]
    assert document.filename.startswith("chicken24_buyurtmalar_") and document.filename.endswith(".xlsx")
    assert "bekor qilingan: 1 ta" in caption
    wb = workbook_of(msg)
    assert wb.sheetnames == ["Buyurtmalar", "Taomlar", "Xulosa"]
    ws = wb["Buyurtmalar"]
    assert [c.value for c in ws[1]] == excel_export.ORDER_HEADERS
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref
    assert ws["A1"].font.bold and ws["A1"].fill.fgColor.rgb.endswith("FF7A1A")
    row2 = [c.value for c in ws[2]]
    assert row2[0] == 1 and isinstance(row2[1], datetime)
    assert row2[10] == "Original Wings (10 dona) × 2, Pepsi × 1"
    assert row2[11:15] == [120000, 10000, 130000, "naqd"]
    assert ws.cell(2, 12).number_format == "#,##0"
    # Lokatsiya - bosiladigan Google Maps havolasi
    assert ws.cell(2, 10).hyperlink.target == "https://maps.google.com/?q=39.654800,66.975700"
    assert ws.cell(4, 10).value is None and ws.cell(4, 7).value == "Samarqand, Registon 5"


def test_phone_stays_text():
    ws = workbook_of(excel(999, "hammasi"))["Buyurtmalar"]
    cell = ws.cell(2, 5)
    assert cell.value == "+998901234567" and cell.data_type == "s" and cell.number_format == "@"


def test_cancelled_marked_grey_and_excluded_from_revenue():
    wb = workbook_of(excel(999, "hammasi"))
    ws = wb["Buyurtmalar"]
    assert ws.cell(3, 3).value == "bekor qilindi" and ws.cell(2, 3).value == "qabul qilindi"
    assert ws.cell(3, 1).fill.fgColor.rgb.endswith("E7E7E7")
    assert not ws.cell(2, 1).fill.fgColor.rgb.endswith("E7E7E7")
    xul = {r[0]: r[1] for r in wb["Xulosa"].iter_rows(values_only=True) if r and r[0]}
    assert xul["Buyurtmalar (qabul qilingan)"] == 2
    assert xul["Bekor qilingan"] == 1
    assert xul["Tushum (bekor qilinganlarsiz)"] == 130000 + 40000
    assert xul["O'rtacha chek"] == 85000
    # Top: bekor qilingan buyurtmadagi 1 ta wings hisoblanmaydi
    rows = list(wb["Xulosa"].iter_rows(values_only=True))
    top_start = next(i for i, r in enumerate(rows) if r[0] == "Eng ko'p sotilgan 10 taom")
    top = [(r[0], r[1]) for r in rows[top_start + 1:] if r[0] and r[1] is not None]
    assert top == [("Pepsi", 4), ("Original Wings (10 dona)", 2)]
    # Taomlar varag'i: har qator alohida, Holat ustuni bilan, bekor qilinganniki kulrang
    wi = wb["Taomlar"]
    assert [c.value for c in wi[1]] == excel_export.ITEM_HEADERS
    items = list(wi.iter_rows(min_row=2, max_row=5, values_only=True))
    assert [(r[0], r[2], r[3], r[4], r[5], r[6]) for r in items] == [
        (1, "qabul qilindi", "Original Wings (10 dona)", 2, 55000, 110000),
        (1, "qabul qilindi", "Pepsi", 1, 10000, 10000),
        (2, "bekor qilindi", "Original Wings (10 dona)", 1, 55000, 55000),
        (3, "qabul qilindi", "Pepsi", 3, 10000, 30000),
    ]
    assert wi.cell(4, 1).fill.fgColor.rgb.endswith("E7E7E7")


# ------------------------------------------------------------ jami qatorlari

def _total_row(ws):
    """Jadval ostidagi jami qatori: oxirgi qator; undan oldin bitta bo'sh qator."""
    r = ws.max_row
    assert all(c.value is None for c in ws[r - 1]), "jami qatoridan oldin bo'sh qator bo'lishi kerak"
    assert str(ws.cell(r, 1).value).startswith("JAMI (qabul qilinganlar)")
    return r


def test_orders_total_row_excludes_cancelled_and_matches_summary():
    wb = workbook_of(excel(999, "hammasi"))
    ws = wb["Buyurtmalar"]
    r = _total_row(ws)
    assert r == 6  # sarlavha + 3 buyurtma + bo'sh qator + jami
    assert ws.cell(r, 1).value == "JAMI (qabul qilinganlar): 2 ta buyurtma"
    # Bekor qilingan (#2, 65 000) kirmaydi: 130 000 + 40 000
    assert (ws.cell(r, 12).value, ws.cell(r, 13).value, ws.cell(r, 14).value) == (150000, 20000, 170000)
    for c in (12, 13, 14):
        assert isinstance(ws.cell(r, c).value, int), "formula emas, tayyor son"
        assert ws.cell(r, c).number_format == "#,##0"
    assert ws.cell(r, 14).font.bold and ws.cell(r, 14).fill.fgColor.rgb.endswith("FFE1C7")
    assert ws.cell(r, 14).border.top.style == "medium"
    # Filtr faqat ma'lumot qatorlarida - jami qatori kirmaydi
    assert ws.auto_filter.ref == "A1:P4"  # 16-ustun: Masofa (km)
    # Xulosa bilan aynan teng
    xul = {row[0]: row[1] for row in wb["Xulosa"].iter_rows(values_only=True) if row and row[0]}
    assert xul["Tushum (bekor qilinganlarsiz)"] == ws.cell(r, 14).value
    assert xul["  shundan taomlar"] == ws.cell(r, 12).value
    assert xul["  shundan yetkazish"] == ws.cell(r, 13).value
    assert xul["Buyurtmalar (qabul qilingan)"] == 2


def test_items_total_row_excludes_cancelled():
    wb = workbook_of(excel(999, "hammasi"))
    wi = wb["Taomlar"]
    r = _total_row(wi)
    assert wi.auto_filter.ref == "A1:G5"
    # Qabul qilingan taomlar: 2 + 1 + 3 = 6 dona, 110 000 + 10 000 + 30 000 (bekor qilingan 55 000 kirmaydi)
    assert (wi.cell(r, 5).value, wi.cell(r, 7).value) == (6, 150000)
    xul = {row[0]: row[1] for row in wb["Xulosa"].iter_rows(values_only=True) if row and row[0]}
    assert xul["  shundan taomlar"] == wi.cell(r, 7).value
    assert wb["Buyurtmalar"].cell(_total_row(wb["Buyurtmalar"]), 12).value == wi.cell(r, 7).value


def test_empty_period_sends_message_not_file():
    async def empty_db():
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        await db.connect()
        return db

    msg = excel(999, "bugun", empty_db)
    # Bo'sh davr: fayl (va demak jami qatori ham) umuman yuborilmaydi
    assert msg.docs == [] and msg.texts[0].startswith("Bu davrda buyurtma yo'q")


def test_only_cancelled_period_has_zero_totals():
    async def cancelled_only():
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        await db.connect()
        oid = await db.save_order(user_id=1, username=None, order=order([("pepsi", 1)]),
                                  created_at=datetime.now(timezone.utc))
        await db.conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (oid,))
        await db.conn.commit()
        return db

    wb = workbook_of(excel(999, "hammasi", cancelled_only))
    ws = wb["Buyurtmalar"]
    r = _total_row(ws)
    assert ws.cell(r, 1).value == "JAMI (qabul qilinganlar): 0 ta buyurtma"
    assert ws.cell(r, 14).value == 0


def test_unknown_period_shows_help():
    msg = excel(999, "yil")
    assert msg.docs == [] and "/excel hafta" in msg.texts[0]


# ------------------------------------------------------------ davr chegaralari (ish kuni 03:00)

def test_period_boundaries():
    now = datetime(2026, 11, 18, 1, 30, tzinfo=SKD)  # chorshanba, 01:30 - hali 17-noyabr ish kuni
    bugun = excel_export.export_range("bugun", now)
    assert bugun.start == datetime(2026, 11, 17, 3, 0, tzinfo=SKD)
    hafta = excel_export.export_range("hafta", now)
    assert hafta.start == datetime(2026, 11, 16, 3, 0, tzinfo=SKD)  # dushanba 03:00
    oy = excel_export.export_range("oy", now)
    assert oy.start == datetime(2026, 11, 1, 3, 0, tzinfo=SKD)
    kun30 = excel_export.export_range("", now)
    assert kun30.start == datetime(2026, 10, 19, 3, 0, tzinfo=SKD)  # 30 ish kuni
    hammasi = excel_export.export_range("hammasi", now)
    assert hammasi.start is None and hammasi.end is None
    assert excel_export.export_range("yil", now) is None


def test_bugun_includes_after_midnight_excludes_before_0300():
    async def scenario():
        db = Database(Path(tempfile.mkdtemp()) / "t.db")
        await db.connect()
        await db.save_order(user_id=1, username=None, order=order([("pepsi", 1)]), created_at=local(2026, 11, 17, 2, 59))  # oldingi ish kuni
        await db.save_order(user_id=1, username=None, order=order([("pepsi", 2)]), created_at=local(2026, 11, 17, 3, 0))   # bugun
        await db.save_order(user_id=1, username=None, order=order([("pepsi", 3)]), created_at=local(2026, 11, 18, 1, 0))   # bugun (yarim tundan keyin)
        rng = excel_export.export_range("bugun", datetime(2026, 11, 18, 1, 30, tzinfo=SKD))
        rows = await db.orders_with_items(rng.start, rng.end)
        await db.close()
        return [r["id"] for r, _ in rows]

    assert asyncio.run(scenario()) == [2, 3]


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
