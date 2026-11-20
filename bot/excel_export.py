"""Admin uchun Excel eksport (/excel): buyurtmalar, taomlar va xulosa - uchta varaq.

Fayl faqat xotirada (BytesIO) yaratiladi, diskka yozilmaydi.
Og'ir qism (build_workbook) asyncio.to_thread() da ishlaydi - bot bloklanmaydi.
/backup (SQLite nusxa) va kunlik zaxira bundan mustaqil - ular tiklash uchun.
"""
from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import config
from reports import business_day_start

MONEY_FORMAT = "#,##0"  # minglik ajratgich (O'zbek/Rus sozlamada "30 000")
DATE_FORMAT = "DD.MM.YYYY HH:MM"
HEADER_FILL = PatternFill("solid", fgColor="FF7A1A")
HEADER_FONT = Font(bold=True, color="FFFFFF")
CANCELLED_FILL = PatternFill("solid", fgColor="E7E7E7")
CANCELLED_FONT = Font(color="808080")
LINK_FONT = Font(color="0563C1", underline="single")
# Jami qatori: sarlavhadan farqli - och to'q sariq fon, qalin to'q matn, ustida qalin chegara
TOTAL_FILL = PatternFill("solid", fgColor="FFE1C7")
TOTAL_FONT = Font(bold=True, color="7A3300")
TOTAL_BORDER = Border(top=Side(style="medium", color="FF7A1A"))
TOTAL_LABEL = "JAMI (qabul qilinganlar)"
STATUS_TEXT = {"cancelled": "bekor qilindi"}
DEFAULT_STATUS_TEXT = "qabul qilindi"

ORDER_HEADERS = [
    "№", "Sana va vaqt", "Holat", "Ism", "Telefon", "Telegram username", "Manzil", "Uy/qavat/xonadon",
    "Izoh", "Lokatsiya", "Taomlar", "Taomlar jami", "Yetkazish", "Jami", "To'lov",
]
ITEM_HEADERS = ["Buyurtma №", "Sana", "Holat", "Taom nomi", "Soni", "Narx", "Summa"]


# ---------------------------------------------------------------- davrlar

@dataclass(frozen=True)
class ExportRange:
    key: str
    title: str  # "Oxirgi 30 kun (18.10.2026 – 17.11.2026)"
    start: datetime | None  # UTC; None - boshidan
    end: datetime | None  # UTC; None - hozirgacha


PERIOD_KEYS = {
    "": "30kun", "30": "30kun", "bugun": "bugun", "hafta": "hafta", "oy": "oy", "hammasi": "hammasi",
}
PERIOD_HELP = (
    "/excel — oxirgi 30 kun\n/excel_bugun (yoki /excel bugun)\n/excel_hafta (yoki /excel hafta)\n"
    "/excel_oy (yoki /excel oy)\n/excel_hammasi (yoki /excel hammasi)"
)


def export_range(arg: str, now: datetime) -> ExportRange | None:
    """Buyruq argumentidan davr (ish kuni 03:00 da almashadi, /hisobot bilan bir xil). Noma'lum bo'lsa None."""
    key = PERIOD_KEYS.get(arg.strip().lower())
    if key is None:
        return None
    now_local = now.astimezone(config.LOCAL_TZ)
    today = business_day_start(now_local)
    end_local = now_local + timedelta(seconds=1)
    if key == "hammasi":
        return ExportRange(key, "Barcha buyurtmalar", None, None)
    start_local = {
        "bugun": today,
        "hafta": today - timedelta(days=today.weekday()),
        "oy": today.replace(day=1),
        "30kun": today - timedelta(days=29),
    }[key]
    names = {"bugun": "Bugun", "hafta": "Shu hafta", "oy": "Shu oy", "30kun": "Oxirgi 30 kun"}
    title = f"{names[key]} ({start_local:%d.%m.%Y %H:%M} – {now_local:%d.%m.%Y %H:%M})"
    return ExportRange(key, title, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))


# ---------------------------------------------------------------- yordamchilar

def _local_naive(iso: str) -> datetime:
    """Bazadagi UTC vaqt -> Samarqand vaqti (Excel uchun vaqt zonasisiz)."""
    return datetime.fromisoformat(iso).astimezone(config.LOCAL_TZ).replace(tzinfo=None)


def _address(row: dict) -> str:
    if row.get("address_type") == "location" and row.get("lat") is not None:
        return f"Joriy joylashuv ({row['lat']:.6f}, {row['lon']:.6f})"
    return row.get("address_text") or ""


def _maps_url(row: dict) -> str | None:
    if row.get("address_type") == "location" and row.get("lat") is not None and row.get("lon") is not None:
        return f"https://maps.google.com/?q={row['lat']:.6f},{row['lon']:.6f}"
    return None


def _items_total(row: dict, items: list[dict]) -> int:
    if row.get("items_total") is not None:
        return int(row["items_total"])
    return sum(int(i["line_total"]) for i in items)


def _write_header(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def _finish_table(ws, min_width: int = 10, max_width: int = 60, wrap_cols: tuple[int, ...] = ()) -> None:
    """Ustun kengligi mazmuniga mos, filtr yoqilgan."""
    for col_idx, column in enumerate(ws.iter_cols(min_row=1, max_row=ws.max_row), start=1):
        longest = 0
        for cell in column:
            value = cell.value
            if value is None:
                continue
            text = value.strftime("%d.%m.%Y %H:%M") if isinstance(value, datetime) else str(value)
            if isinstance(value, int):
                text = f"{value:,}"
            longest = max(longest, max(len(part) for part in text.split("\n")))
        ws.column_dimensions[get_column_letter(col_idx)].width = max(min_width, min(max_width, longest + 2))
        if col_idx in wrap_cols:
            for cell in column[1:]:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def _total_row(ws, label: str, label_cols: int, values: dict[int, int], money_cols: tuple[int, ...]) -> int:
    """Jadval ostiga: bitta bo'sh qator + jami qatori (tayyor sonlar, formula emas).
    Filtr diapazoni oldin (_finish_table da) o'rnatilgani uchun bu qator unga kirmaydi:
    saralash/filtr uni siljitmaydi va yashirmaydi. Qator raqamini qaytaradi."""
    last_col = ws.max_column
    r = ws.max_row + 2  # oxirgi ma'lumot qatoridan keyin bitta bo'sh qator
    ws.cell(r, 1, label)
    if label_cols > 1:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=label_cols)
    for col, value in values.items():
        ws.cell(r, col, value)
    for col in range(1, last_col + 1):
        cell = ws.cell(r, col)
        cell.fill = TOTAL_FILL
        cell.font = TOTAL_FONT
        cell.border = TOTAL_BORDER
        if col in money_cols:
            cell.number_format = MONEY_FORMAT
    ws.cell(r, 1).alignment = Alignment(horizontal="right", vertical="center")
    return r


def _plain_text(ws, row_idx: int, columns: tuple[int, ...]) -> None:
    """Formula in'eksiyasidan himoya: mijoz yozgan matn (ism, manzil, izoh...) '=' bilan boshlansa openpyxl uni
    FORMULA deb yozadi va admin faylni ochganda Excel uni bajaradi (=HYPERLINK(...), =cmd|...). Shu ustunlardagi
    har qanday matn majburan oddiy matn (string) turiga o'tkaziladi - Excel uni hech qachon hisoblamaydi."""
    for col in columns:
        cell = ws.cell(row_idx, col)
        if isinstance(cell.value, str):
            cell.data_type = "s"


def _grey_row(ws, row_idx: int) -> None:
    for cell in ws[row_idx]:
        cell.fill = CANCELLED_FILL
        if cell.hyperlink is None:
            cell.font = CANCELLED_FONT


# ---------------------------------------------------------------- kitob

def build_workbook(orders: list[tuple[dict, list[dict]]], period: ExportRange, generated_at: datetime) -> bytes:
    """Uchta varaqli .xlsx (bayt). Sinxron - asyncio.to_thread() da chaqiring."""
    wb = Workbook()

    # Yig'indilar BIR JOYDA hisoblanadi - jami qatorlari va Xulosa aynan bir xil sonlarni ko'rsatadi.
    # Faqat "qabul qilindi" (bekor qilinganlarsiz).
    active = [(row, items) for row, items in orders if row.get("status") != "cancelled"]
    cancelled_count = len(orders) - len(active)
    revenue = sum(int(row["total"]) for row, _ in active)
    items_revenue = sum(_items_total(row, items) for row, items in active)
    delivery_revenue = sum(int(row.get("delivery_fee") or 0) for row, _ in active)
    active_item_qty = sum(int(i["qty"]) for _, items in active for i in items)
    active_item_sum = sum(int(i["line_total"]) for _, items in active for i in items)

    # 1) Buyurtmalar
    ws = wb.active
    ws.title = "Buyurtmalar"
    _write_header(ws, ORDER_HEADERS)
    for row, items in orders:
        cancelled = row.get("status") == "cancelled"
        items_total = _items_total(row, items)
        delivery = int(row.get("delivery_fee") or 0)
        ws.append([
            row["id"],
            _local_naive(row["created_at"]),
            STATUS_TEXT.get(row.get("status"), DEFAULT_STATUS_TEXT),
            row.get("customer_name") or "",
            row.get("phone") or "",
            ("@" + row["username"]) if row.get("username") else "",
            _address(row),
            row.get("address_details") or "",
            row.get("comment") or "",
            None,  # lokatsiya - pastda havola bilan
            ", ".join(f"{i['name']} × {i['qty']}" for i in items),
            items_total,
            delivery,
            int(row["total"]),
            "naqd",
        ])
        r = ws.max_row
        ws.cell(r, 2).number_format = DATE_FORMAT
        # Ism, telefon, username, manzil, uy/qavat, izoh, taomlar - doim oddiy matn (formula emas)
        _plain_text(ws, r, (4, 5, 6, 7, 8, 9, 11))
        ws.cell(r, 5).number_format = "@"  # telefon: "+998..." boshidagi belgi yo'qolmaydi
        url = _maps_url(row)
        if url:
            link = ws.cell(r, 10)
            link.value = "Xaritada ochish"
            link.hyperlink = url
            link.font = LINK_FONT
        for c in (12, 13, 14):
            ws.cell(r, c).number_format = MONEY_FORMAT
        if cancelled:
            _grey_row(ws, r)
    _finish_table(ws, wrap_cols=(7, 9, 11))  # filtr shu yerda - faqat ma'lumot qatorlari
    ws.column_dimensions["A"].width = 7
    ws.column_dimensions["J"].width = 16
    _total_row(
        ws, f"{TOTAL_LABEL}: {len(active)} ta buyurtma", label_cols=11,
        values={12: items_revenue, 13: delivery_revenue, 14: revenue}, money_cols=(12, 13, 14),
    )

    # 2) Taomlar - har bir taom qatori alohida
    wi = wb.create_sheet("Taomlar")
    _write_header(wi, ITEM_HEADERS)
    for row, items in orders:
        when = _local_naive(row["created_at"])
        status = STATUS_TEXT.get(row.get("status"), DEFAULT_STATUS_TEXT)
        for item in items:
            wi.append([row["id"], when, status, item["name"], int(item["qty"]), int(item["price"]),
                       int(item["line_total"])])
            r = wi.max_row
            _plain_text(wi, r, (4,))
            wi.cell(r, 2).number_format = DATE_FORMAT
            wi.cell(r, 6).number_format = MONEY_FORMAT
            wi.cell(r, 7).number_format = MONEY_FORMAT
            if row.get("status") == "cancelled":
                _grey_row(wi, r)
    _finish_table(wi)
    _total_row(wi, TOTAL_LABEL, label_cols=4, values={5: active_item_qty, 7: active_item_sum}, money_cols=(7,))

    # 3) Xulosa (bekor qilinganlar tushumga va top-10 ga kirmaydi)
    avg = revenue // len(active) if active else 0
    qty_by_name: Counter[str] = Counter()
    sum_by_name: Counter[str] = Counter()
    for _, items in active:
        for item in items:
            qty_by_name[item["name"]] += int(item["qty"])
            sum_by_name[item["name"]] += int(item["line_total"])

    ws3 = wb.create_sheet("Xulosa")
    ws3.append(["Chicken24 — buyurtmalar hisoboti"])
    ws3["A1"].font = Font(bold=True, size=14)
    rows = [
        ("Davr", period.title),
        ("Yaratilgan", generated_at.astimezone(config.LOCAL_TZ).strftime("%d.%m.%Y %H:%M") + " (Samarqand vaqti)"),
        ("Buyurtmalar (qabul qilingan)", len(active)),
        ("Bekor qilingan", cancelled_count),
        ("Tushum (bekor qilinganlarsiz)", revenue),
        ("  shundan taomlar", items_revenue),
        ("  shundan yetkazish", delivery_revenue),
        ("O'rtacha chek", avg),
    ]
    ws3.append([])
    for label, value in rows:
        ws3.append([label, value])
        cell = ws3.cell(ws3.max_row, 2)
        if label.startswith(("Tushum", "  shundan", "O'rtacha")):
            cell.number_format = MONEY_FORMAT
        ws3.cell(ws3.max_row, 1).font = Font(bold=True)
    ws3.append([])
    ws3.append(["Eng ko'p sotilgan 10 taom", "Soni", "Summa"])
    for cell in ws3[ws3.max_row]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    top = sorted(qty_by_name.items(), key=lambda kv: (-kv[1], -sum_by_name[kv[0]], kv[0]))[:10]
    for name, qty in top:
        ws3.append([name, qty, sum_by_name[name]])
        _plain_text(ws3, ws3.max_row, (1,))
        ws3.cell(ws3.max_row, 3).number_format = MONEY_FORMAT
    ws3.append([])
    ws3.append([f"Ish kuni {config.CLOSE_AT:%H:%M} da almashadi. Vaqtlar Samarqand vaqtida."])
    ws3.cell(ws3.max_row, 1).font = Font(italic=True, color="808080")
    ws3.column_dimensions["A"].width = 34
    ws3.column_dimensions["B"].width = 44
    ws3.column_dimensions["C"].width = 14

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_filename(now: datetime) -> str:
    return f"chicken24_buyurtmalar_{now.astimezone(config.LOCAL_TZ):%Y-%m-%d}.xlsx"
