"""Yetkazish narxi: restorandan mijoz lokatsiyasigacha AVTOMOBIL YO'LI masofasi bo'yicha.

Narx qoidasi (butun son arifmetikasi, suzuvchi nuqta xatosiz):
    t = floor(km * 10 + 0.5)            # 0.1 km gacha yaxlitlash (half-up)
    billable_km = (t + 4) // 10
    extra_km = max(0, billable_km - BASE_KM)
    fee = BASE_FEE + EXTRA_PER_KM * extra_km
Standart sozlamada: <=2.5 km - 10 000; 2.6-3.5 - 12 500; 3.6-4.5 - 15 000; 4.6-5.5 - 17 500 ... Maksimal masofa yo'q.

Masofa manbai:
1) OpenRouteService Directions (driving-car) - ORS_API_KEY bo'lsa. Joriy manzil:
   https://api.heigit.org/openrouteservice/v2/directions/driving-car/json, kalit `Authorization` sarlavhasida.
   Timeout 4 s, natija 24 soat keshlanadi (kalit - koordinatalar 4 xonagacha), kunlik 1800 so'rovdan oshmaydi.
2) Zaxira - to'g'ri chiziq (haversine) * ROAD_FACTOR, "taxminiy" belgisi bilan: kalit yo'q, kunlik limit tugagan,
   ORS xato / timeout / 429 bo'lsa. Buyurtma masofa xizmati tufayli hech qachon to'xtab qolmaydi.
Kalit loglarga va Mini App'ga chiqmaydi.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Awaitable, Callable

import aiohttp

logger = logging.getLogger(__name__)

ORS_DIRECTIONS_URL = "https://api.heigit.org/openrouteservice/v2/directions/driving-car/json"
ORS_TIMEOUT_SECONDS = 4.0
ORS_DAILY_LIMIT = 1800
CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_ENTRIES = 20_000
EARTH_RADIUS_KM = 6371.0


# ================================================================ narx

@dataclass(frozen=True)
class Tariff:
    base_fee: int = 10_000
    base_km: int = 2
    extra_per_km: int = 2_500

    def as_json(self) -> dict:
        return {"base_km": self.base_km, "base_fee": self.base_fee, "extra_per_km": self.extra_per_km}


def km_to_tenths(km: float | Decimal | str) -> int:
    """Masofa -> 0.1 km birliklarida (half-up): t = floor(km*10 + 0.5). Decimal orqali - 2.55 aniq 26 bo'ladi."""
    value = km if isinstance(km, Decimal) else Decimal(str(km))
    if value < 0:
        raise ValueError("masofa manfiy bo'lishi mumkin emas")
    return int((value * 10 + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def fee_for_tenths(tenths: int, tariff: Tariff) -> int:
    billable_km = (tenths + 4) // 10
    extra_km = max(0, billable_km - tariff.base_km)
    return tariff.base_fee + tariff.extra_per_km * extra_km


def delivery_fee(km: float | Decimal | str, tariff: Tariff = Tariff()) -> int:
    return fee_for_tenths(km_to_tenths(km), tariff)


@dataclass(frozen=True)
class Quote:
    tenths: int          # masofa 0.1 km birliklarida (yaxlitlangan)
    fee: int             # yetkazish narxi, so'm
    approx: bool         # True - taxminiy (to'g'ri chiziq * ROAD_FACTOR), ORS emas

    @property
    def km(self) -> float:
        return self.tenths / 10

    def as_json(self) -> dict:
        return {"km": self.km, "fee": self.fee, "approx": self.approx}


# ================================================================ masofa

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.radians
    dp, dl = rad(lat2 - lat1), rad(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def valid_point(lat: Any, lon: Any) -> bool:
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and not isinstance(lat, bool) and not isinstance(lon, bool)
            and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


class OrsError(Exception):
    """ORS javob bermadi / xato / limit (sababi qisqa, kalitsiz)."""


Fetch = Callable[[str, dict, dict, float], Awaitable[tuple[int, Any]]]


async def http_post_json(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, Any]:
    """POST JSON -> (holat kodi, JSON yoki None)."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(url, json=body, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = None
            return resp.status, data


class DeliveryService:
    """Masofa va narx: ORS (kesh, kunlik limit) yoki zaxira (haversine * ROAD_FACTOR)."""

    def __init__(self, *, origin: tuple[float, float], tariff: Tariff = Tariff(), road_factor: float = 1.3,
                 api_key: str | None = None, url: str = ORS_DIRECTIONS_URL, timeout: float = ORS_TIMEOUT_SECONDS,
                 daily_limit: int = ORS_DAILY_LIMIT, cache_ttl: float = CACHE_TTL_SECONDS,
                 fetch: Fetch = http_post_json, clock: Callable[[], float] = time.time,
                 today: Callable[[], str] | None = None) -> None:
        self.origin = origin
        self.tariff = tariff
        self.road_factor = road_factor
        self._key = api_key or None
        self._url = url
        self._timeout = timeout
        self._daily_limit = daily_limit
        self._ttl = cache_ttl
        self._fetch = fetch
        self._clock = clock
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self._cache: dict[tuple[float, float], tuple[float, float]] = {}  # kalit -> (muddat, km)
        self._day = ""
        self.requests_today = 0
        self._limit_logged = False

    @classmethod
    def from_config(cls, config: Any) -> "DeliveryService":
        return cls(
            origin=(config.restaurant_lat, config.restaurant_lon),
            tariff=Tariff(config.delivery_base_fee, config.delivery_base_km, config.delivery_extra_per_km),
            road_factor=config.road_factor,
            api_key=config.ors_api_key,
        )

    def __repr__(self) -> str:  # kalit hech qachon ko'rinmasin
        return f"DeliveryService(ors={'on' if self._key else 'off'}, origin={self.origin})"

    @staticmethod
    def cache_key(lat: float, lon: float) -> tuple[float, float]:
        return round(lat, 4), round(lon, 4)

    def _approx_km(self, lat: float, lon: float) -> float:
        return haversine_km(self.origin[0], self.origin[1], lat, lon) * self.road_factor

    def _take_quota(self) -> bool:
        day = self._today()
        if day != self._day:
            self._day, self.requests_today, self._limit_logged = day, 0, False
        if self.requests_today >= self._daily_limit:
            if not self._limit_logged:
                logger.warning("ORS kunlik limiti (%d) tugadi - bugun taxminiy masofa ishlatiladi", self._daily_limit)
                self._limit_logged = True
            return False
        self.requests_today += 1
        return True

    async def _ors_km(self, lat: float, lon: float) -> float:
        body = {
            "coordinates": [[self.origin[1], self.origin[0]], [lon, lat]],  # ORS tartibi: [lon, lat]
            "instructions": False,
            "geometry": False,
        }
        headers = {"Authorization": self._key, "Content-Type": "application/json", "Accept": "application/json"}
        try:
            status, data = await asyncio.wait_for(self._fetch(self._url, body, headers, self._timeout),
                                                  self._timeout + 0.5)
        except asyncio.TimeoutError:
            raise OrsError("timeout") from None
        except Exception as exc:  # noqa: BLE001 - tarmoq xatosi
            raise OrsError(type(exc).__name__) from None
        if status != 200:
            raise OrsError(f"HTTP {status}")
        try:
            meters = data["routes"][0]["summary"].get("distance", 0)  # nol masofada maydon bo'lmasligi mumkin
            km = float(meters) / 1000
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise OrsError("javob tuzilmasi noto'g'ri") from None
        if not math.isfinite(km) or km < 0:
            raise OrsError("noto'g'ri masofa")
        return km

    async def distance_km(self, lat: float, lon: float) -> tuple[float, bool]:
        """(km, taxminiymi). Hech qachon xato tashlamaydi (nuqta noto'g'ri bo'lsa - ValueError)."""
        if not valid_point(lat, lon):
            raise ValueError("lat/lon diapazondan tashqari")
        key = self.cache_key(lat, lon)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1], False
        if self._key and self._take_quota():
            try:
                km = await self._ors_km(lat, lon)
            except OrsError as exc:
                logger.warning("ORS masofa olinmadi (%s) - taxminiy masofa ishlatiladi", exc)
            else:
                if len(self._cache) >= CACHE_MAX_ENTRIES:
                    self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                    if len(self._cache) >= CACHE_MAX_ENTRIES:
                        self._cache.clear()
                self._cache[key] = (now + self._ttl, km)
                return km, False
        return self._approx_km(lat, lon), True

    async def quote(self, lat: float, lon: float) -> Quote:
        km, approx = await self.distance_km(lat, lon)
        tenths = km_to_tenths(Decimal(repr(km)))
        return Quote(tenths=tenths, fee=fee_for_tenths(tenths, self.tariff), approx=approx)
