"""O'z-o'ziga ping: Render bepul xizmati 15 daqiqa kiruvchi so'rovsiz qolsa uxlaydi.

Bot har SELF_PING_SECONDS da o'zining OCHIQ manziliga (RENDER_EXTERNAL_URL yoki PUBLIC_URL + /health)
GET yuboradi. So'rov internet orqali Render'ning kirish nuqtasidan qaytib keladi - shuning uchun Render
uni kiruvchi trafik deb hisoblaydi. Lokal kompyuterda bu o'zgaruvchilar yo'q - ping o'chiq.

Xatolar bot ishini to'xtatmaydi; logga faqat qisqa sabab yoziladi (manzil yozilmaydi).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import aiohttp

logger = logging.getLogger(__name__)

SELF_PING_SECONDS = 8 * 60      # Render 15 daqiqada uxlatadi - 8 daqiqa bilan ikki marta zaxira
SELF_PING_TIMEOUT_SECONDS = 30


async def ping_once(session: aiohttp.ClientSession, url: str) -> int:
    """Bitta GET. HTTP holat kodini qaytaradi (xato bo'lsa istisno)."""
    async with session.get(url, allow_redirects=False) as resp:
        await resp.read()
        return resp.status


async def self_ping_loop(url: str, interval: float = SELF_PING_SECONDS, *,
                         ping: Callable[[aiohttp.ClientSession, str], Awaitable[int]] = ping_once,
                         sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Fon vazifasi: to'xtatilguncha (cancel) ishlaydi, hech qachon xato bilan yiqilmaydi."""
    failures = 0
    timeout = aiohttp.ClientTimeout(total=SELF_PING_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "chicken24-self-ping"}) as session:
        while True:
            await sleep(interval)
            try:
                status = await ping(session, url)
                error = None if status == 200 else f"HTTP {status}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - tarmoq/timeout: faqat qisqa log
                error = type(exc).__name__
            if error is None:
                if failures:
                    logger.info("Self-ping tiklandi (%d ta xatodan keyin)", failures)
                failures = 0
                logger.debug("Self-ping: 200")
            else:
                failures += 1
                logger.warning("Self-ping muvaffaqiyatsiz (%s), ketma-ket %d-marta", error, failures)
