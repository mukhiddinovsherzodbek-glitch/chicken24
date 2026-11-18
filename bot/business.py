"""Biznes qoidalari: ish vaqti (Samarqand vaqti bo'yicha)."""
from __future__ import annotations

from datetime import datetime, time, timezone

import config


def is_open_at(t: time, open_from: time = config.OPEN_FROM, close_at: time = config.CLOSE_AT) -> bool:
    """Berilgan mahalliy vaqtda ochiqmi.

    open_from < close_at bo'lsa oddiy oraliq (masalan 09:00-18:00).
    Aks holda oraliq yarim tundan o'tadi (masalan 10:00-03:00): open_from dan keyin YOKI close_at dan oldin.
    Chegaralar: open_from - ochiq, close_at - yopiq.
    """
    t = t.replace(second=0, microsecond=0, tzinfo=None)
    if open_from < close_at:
        return open_from <= t < close_at
    return t >= open_from or t < close_at


def local_now() -> datetime:
    """Hozirgi Samarqand vaqti (server soat mintaqasiga bog'liq emas)."""
    return datetime.now(timezone.utc).astimezone(config.LOCAL_TZ)


def is_open_now() -> bool:
    return is_open_at(local_now().time())


def hours_text() -> str:
    """'10:00 – 03:00'"""
    return f"{config.OPEN_FROM:%H:%M} – {config.CLOSE_AT:%H:%M}"
