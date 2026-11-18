"""Admin uchun hisobot (/hisobot): bugun, shu hafta, shu oy.

Restoran 10:00 - 03:00 ishlaydi, shuning uchun "kun" 03:00 da almashadi (ish kuni):
yarim tundan keyingi buyurtmalar oldingi ish kuniga qo'shiladi.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

import config
from db import Database, PeriodStats


def format_price(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so'm"


def business_day_start(now_local: datetime) -> datetime:
    """Joriy ish kunining boshlanishi (config.CLOSE_AT, masalan 03:00) - mahalliy vaqtda."""
    start = now_local.replace(hour=config.CLOSE_AT.hour, minute=config.CLOSE_AT.minute, second=0, microsecond=0)
    if now_local < start:
        start -= timedelta(days=1)
    return start


def periods(now_local: datetime) -> list[tuple[str, datetime, datetime]]:
    """[(nom, boshlanish, tugash)] - mahalliy vaqtda, ish kuni bo'yicha."""
    today = business_day_start(now_local)
    week = today - timedelta(days=today.weekday())  # dushanba
    month = today.replace(day=1)
    end = now_local + timedelta(seconds=1)
    return [("Bugun", today, end), ("Shu hafta", week, end), ("Shu oy", month, end)]


def _period_line(name: str, s: PeriodStats) -> list[str]:
    if not s.orders:
        line = f"<b>{name}:</b> buyurtma yo'q"
        if s.cancelled:
            line += f" (bekor qilingan: {s.cancelled})"
        return [line]
    avg = s.total // s.orders
    lines = [
        f"<b>{name}:</b> {s.orders} ta · <b>{format_price(s.total)}</b>",
        f"   taomlar {format_price(s.items_total)} + yetkazish {format_price(s.delivery_total)}",
        f"   o'rtacha chek {format_price(avg)}",
    ]
    if s.cancelled:
        lines.append(f"   bekor qilingan: {s.cancelled} ta")
    return lines


async def build_report(db: Database, now: datetime | None = None) -> str:
    now_local = (now or datetime.now(timezone.utc)).astimezone(config.LOCAL_TZ)
    lines = [f"📊 <b>Chicken24 — hisobot</b> · {now_local:%d.%m.%Y %H:%M}", ""]
    month_stats: PeriodStats | None = None
    for name, start, end in periods(now_local):
        stats = await db.period_stats(start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        lines += _period_line(name, stats)
        month_stats = stats
    if month_stats and month_stats.top:
        lines += ["", "🏆 <b>Eng ko'p sotilgan (shu oy):</b>"]
        for i, (name, qty, total) in enumerate(month_stats.top, 1):
            lines.append(f"{i}. {html.escape(name)} — {qty} ta, {format_price(total)}")
    lines += ["", f"<i>Ish kuni {config.CLOSE_AT:%H:%M} da almashadi. Bekor qilinganlar summaga kirmaydi.</i>"]
    return "\n".join(lines)
