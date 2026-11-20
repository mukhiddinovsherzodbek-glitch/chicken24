"""Mini App'dan kelgan buyurtmani tekshirish va summani menyudan hisoblash.

Klientdan kelgan narx yoki jami summaga HECH QACHON ishonilmaydi:
narxlar faqat menu.json dan olinadi.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

import i18n
from logsafe import mask_phone
from menu import Menu

MAX_PAYLOAD_BYTES = 4096
NAME_MIN, NAME_MAX = 2, 50
ADDRESS_MAX = 300
DETAILS_MAX = 100  # uy / qavat / xonadon
COMMENT_MAX = 200  # izoh (domofon, qo'ng'iroq qiling...)
MAX_LINES = 30
QTY_MIN, QTY_MAX = 1, 50
MAX_ID_LENGTH = 64
ADDRESS_TYPES = ("location", "text")

# [0-9] ataylab: \d boshqa yozuvlardagi raqamlarni ham qabul qilib yuboradi
PHONE_RE = re.compile(r"\+998[0-9]{9}")


# Mini App yaratgan buyurtma belgisi (tarixdagi yozuvni bot javobi bilan bog'lash uchun)
# Mini App yozuvi belgisi: yangilari 32 belgi (128 bit, crypto.getRandomValues), eskilari 6-24 belgi
REF_RE = re.compile(r"[a-z0-9]{6,64}")


class ValidationError(Exception):
    """Buyurtma noto'g'ri.

    key + params - mijozga ko'rsatiladigan matn kaliti (i18n.py, "val.*"), klient ma'lumotisiz;
    message(lang) - shu matn kerakli tilda; user_message - o'zbekchasi,
    detail - admin logi uchun batafsil sabab (o'zbekcha),
    code - Mini App'ga qaytariladigan qisqa sabab kodi (menu | area | data).
    """

    def __init__(self, key: str, detail: str | None = None, code: str = "data", **params: Any) -> None:
        self.key = key
        self.params = params
        self.detail = detail or i18n.t("uz", key, **params)
        self.code = code
        super().__init__(self.detail)

    def message(self, lang: str | None) -> str:
        return i18n.t(lang, self.key, **self.params)

    @property
    def user_message(self) -> str:
        return self.message("uz")


def extract_ref(data: Any) -> str | None:
    """Payload'dagi 'ref' ni xavfsiz oladi (noto'g'ri bo'lsa None)."""
    if isinstance(data, dict):
        ref = data.get("ref")
        if isinstance(ref, str) and REF_RE.fullmatch(ref):
            return ref
    return None


@dataclass(frozen=True)
class OrderLine:
    item_id: str
    name: str
    price: int
    qty: int

    @property
    def line_total(self) -> int:
        return self.price * self.qty


@dataclass(frozen=True)
class ValidatedOrder:
    name: str
    phone: str
    address_type: str  # "location" | "text"
    address_text: str | None
    lat: float | None
    lon: float | None
    lines: tuple[OrderLine, ...]
    total: int  # umumiy summa = items_total + delivery_fee
    address_details: str | None = None  # uy / qavat / xonadon (ixtiyoriy)
    comment: str | None = None  # izoh (ixtiyoriy)
    items_total: int = 0  # faqat taomlar
    delivery_fee: int = 0  # yetkazish narxi (config.DELIVERY_FEE)


@dataclass(frozen=True)
class DeliveryArea:
    """Yetkazish hududi: markaz va radius (sozlamasi config.py da)."""

    name: str
    lat: float
    lon: float
    radius_km: float

    def distance_km(self, lat: float, lon: float) -> float:
        return haversine_km(self.lat, self.lon, lat, lon)

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_km(lat, lon) <= self.radius_km


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ikki nuqta orasidagi masofa (km), Yer radiusi 6371 km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_control_chars(text: str, allow_newline: bool = False) -> bool:
    for ch in text:
        if ch == "\n" and allow_newline:
            continue
        code = ord(ch)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            return True
    return False


# Ko'rinmas boshqaruv belgilari: matn yo'nalishini o'zgartiruvchi (U+202E "RLO" - admin chekida matnni teskari
# ko'rsatib chalg'itadi), nol kenglikdagi bo'shliq, BOM va h.k. Mijoz ularni ko'rmaydi (boshqa ilovadan
# nusxalaganda ilashib keladi), shuning uchun rad etilmaydi - indamasdan olib tashlanadi.
# U+200C/U+200D (ZWNJ/ZWJ) qoldiriladi: ular murakkab emoji va ba'zi yozuvlar uchun kerak.
_INVISIBLE_CHARS = dict.fromkeys(
    [0x200B, 0x200E, 0x200F, 0xFEFF, *range(0x202A, 0x202F), *range(0x2060, 0x2065), *range(0x2066, 0x206A)]
)


def _strip_invisible(text: str) -> str:
    return text.translate(_INVISIBLE_CHARS)


def _reject_constant(name: str) -> Any:
    # json standart holatda NaN/Infinity ni qabul qiladi - biz rad etamiz
    raise ValueError(f"ruxsat etilmagan qiymat: {name}")


def parse_payload(raw: str | None) -> dict[str, Any]:
    """web_app_data matnini xavfsiz JSON obyektga aylantiradi."""
    if not raw:
        raise ValidationError("val.empty", "Bo'sh web_app_data")
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValidationError("val.too_big", "Payload 4096 baytdan katta")
    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ValidationError("val.broken", f"JSON parse xatosi: {exc}") from None
    if not isinstance(data, dict):
        raise ValidationError("val.broken", "JSON ildizi obyekt emas")
    return data


def _validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("val.name_missing", "name matn emas")
    # Ortiqcha bo'shliqlarni bittaga qisqartiramiz
    name = " ".join(_strip_invisible(value).split())
    if _has_control_chars(name):
        raise ValidationError("val.name_chars", "name: boshqaruv belgilari")
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise ValidationError("val.name_len", f"name uzunligi {len(name)}", min=NAME_MIN, max=NAME_MAX)
    return name


def _validate_phone(value: Any) -> str:
    if not isinstance(value, str) or not PHONE_RE.fullmatch(value):
        raise ValidationError("val.phone", f"phone formati noto'g'ri: {mask_phone(value)}")
    return value


def _validate_address(data: dict[str, Any]) -> tuple[str, str | None, float | None, float | None]:
    address_type = data.get("address_type")
    if address_type not in ADDRESS_TYPES:
        raise ValidationError("val.addr_type", f"address_type noto'g'ri: {address_type!r}")

    if address_type == "location":
        lat, lon = data.get("lat"), data.get("lon")
        if not _is_number(lat) or not _is_number(lon):
            raise ValidationError("val.loc_missing", "lat/lon son emas")
        lat, lon = float(lat), float(lon)
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValidationError("val.loc_bad", "lat/lon cheksiz")
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValidationError("val.loc_bad", f"lat/lon diapazondan tashqari: {lat}, {lon}")
        # location turida qo'lda yozilgan matn e'tiborga olinmaydi
        return address_type, None, lat, lon

    text = data.get("address_text")
    if not isinstance(text, str):
        raise ValidationError("val.addr_missing", "address_text matn emas")
    text = _strip_invisible(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValidationError("val.addr_missing", "address_text bo'sh")
    if len(text) > ADDRESS_MAX:
        raise ValidationError("val.addr_len", f"address_text uzunligi {len(text)}", max=ADDRESS_MAX)
    if _has_control_chars(text, allow_newline=True):
        raise ValidationError("val.addr_chars", "address_text: boshqaruv belgilari")
    return address_type, text, None, None


def _validate_optional_text(value: Any, max_len: int, key: str, field: str) -> str | None:
    """Ixtiyoriy matn maydoni: bo'sh bo'lsa None, aks holda uzunlik va belgilar tekshiriladi.
    key - xato matni kalitlari prefiksi (i18n: val.<key>_type / _len / _chars)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"val.{key}_type", f"{field} matn emas")
    text = " ".join(_strip_invisible(value).split())  # yangi qator va ortiqcha bo'shliqlar bitta bo'shliq
    if not text:
        return None
    if len(text) > max_len:
        raise ValidationError(f"val.{key}_len", f"{field} uzunligi {len(text)}", max=max_len)
    if _has_control_chars(text):
        raise ValidationError(f"val.{key}_chars", f"{field}: boshqaruv belgilari")
    return text


def _validate_items(value: Any, menu: Menu) -> tuple[OrderLine, ...]:
    if not isinstance(value, list) or not value:
        raise ValidationError("val.cart_empty", "items bo'sh yoki massiv emas")
    if len(value) > MAX_LINES:
        raise ValidationError("val.cart_lines", f"items soni {len(value)}", max=MAX_LINES)

    lines: list[OrderLine] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError("val.cart_broken", f"items[{index}] obyekt emas")
        item_id = entry.get("id")
        qty = entry.get("qty")
        if not isinstance(item_id, str) or not item_id or len(item_id) > MAX_ID_LENGTH:
            raise ValidationError("val.cart_broken", f"items[{index}].id noto'g'ri")
        # item_id - mijozdan kelgan matn: logga !r bilan yoziladi (yangi qator bilan soxta log qatori yasab bo'lmaydi)
        if item_id in seen:
            raise ValidationError("val.cart_broken", f"takroriy id: {item_id!r}")
        seen.add(item_id)
        if not _is_int(qty) or not QTY_MIN <= qty <= QTY_MAX:
            raise ValidationError("val.qty", f"{item_id!r}: qty noto'g'ri ({str(qty)[:40]!r})", min=QTY_MIN, max=QTY_MAX)
        menu_item = menu.items.get(item_id)
        if menu_item is None:
            raise ValidationError("val.menu", f"menyuda yo'q id: {item_id!r}", code="menu")
        # Narx FAQAT menyudan olinadi; klient yuborgan "price" e'tiborsiz qoladi
        lines.append(OrderLine(item_id=item_id, name=menu_item.name, price=menu_item.price, qty=qty))
    return tuple(lines)


def calculate_total(lines: tuple[OrderLine, ...] | list[OrderLine]) -> int:
    return sum(line.line_total for line in lines)


def _check_area(lat: float, lon: float, area: DeliveryArea) -> None:
    distance = area.distance_km(lat, lon)
    if distance > area.radius_km:
        # Hudud nomi matnga i18n dan keladi (uz: config.DELIVERY_AREA_NAME, ru: "г. Самарканд")
        raise ValidationError(
            "val.area",
            f"hududdan tashqari lokatsiya: {lat}, {lon} ({distance:.1f} km > {area.radius_km} km)",
            code="area",
            km=f"{distance:.0f}",
        )


def validate_order(
    data: dict[str, Any],
    menu: Menu,
    area: DeliveryArea | None = None,
    delivery_fee: int = 0,
) -> ValidatedOrder:
    """To'liq tekshiruv. Muvaffaqiyatsiz bo'lsa ValidationError.

    area berilsa, "location" turidagi manzil shu hudud ichida bo'lishi shart.
    Qo'lda yozilgan manzilni koordinataga aylantirib bo'lmaydi - u tekshirilmaydi.
    Umumiy summa = taomlar (menyudan) + delivery_fee (sozlamadan); klient yuborgan summa e'tiborsiz.
    """
    name = _validate_name(data.get("name"))
    phone = _validate_phone(data.get("phone"))
    address_type, address_text, lat, lon = _validate_address(data)
    if area is not None and lat is not None and lon is not None:
        _check_area(lat, lon, area)
    address_details = _validate_optional_text(data.get("address_details"), DETAILS_MAX, "details", "address_details")
    comment = _validate_optional_text(data.get("comment"), COMMENT_MAX, "comment", "comment")
    lines = _validate_items(data.get("items"), menu)
    items_total = calculate_total(lines)
    return ValidatedOrder(
        name=name,
        phone=phone,
        address_type=address_type,
        address_text=address_text,
        lat=lat,
        lon=lon,
        lines=lines,
        total=items_total + delivery_fee,
        address_details=address_details,
        comment=comment,
        items_total=items_total,
        delivery_fee=delivery_fee,
    )
