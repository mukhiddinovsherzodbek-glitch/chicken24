"""Validatsiya va summa hisoblash testlari.

Ishga tushirish (bot/ papkasida):
    python -m pytest tests -q        (pytest o'rnatilgan bo'lsa)
    python tests/test_validation.py  (pytest'siz ham ishlaydi)
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

from menu import MenuError, MenuStore, load_menu_file, parse_menu  # noqa: E402
from validators import ValidationError, parse_payload, validate_order  # noqa: E402

# Testlar haqiqiy menu.json'ga bog'liq bo'lmasligi uchun o'z menyusi
TEST_MENU = parse_menu(
    {
        "categories": [
            {"id": "burger", "name": "Burger"},
            {"id": "pizza", "name": "Pizza"},
        ],
        "items": [
            {"id": "burger-1", "category": "burger", "name": "Klassik burger", "price": 25000},
            {"id": "pizza-1", "category": "pizza", "name": "Margarita", "price": 60000},
        ],
    }
)


def good_payload(**overrides):
    data = {
        "name": "Aziz",
        "phone": "+998901234567",
        "address_type": "text",
        "address_text": "Toshkent, Chilonzor 5-kvartal, 12-uy",
        "lat": None,
        "lon": None,
        "items": [{"id": "burger-1", "qty": 2}, {"id": "pizza-1", "qty": 1}],
    }
    data.update(overrides)
    return data


def expect_error(data, menu=TEST_MENU):
    try:
        validate_order(data, menu)
    except ValidationError as exc:
        return exc
    raise AssertionError(f"ValidationError kutilgan edi: {data}")


# ------------------------------------------------------------ to'g'ri holatlar

def test_valid_text_order_and_total():
    order = validate_order(good_payload(), TEST_MENU)
    assert order.total == 2 * 25000 + 60000
    assert order.address_type == "text"
    assert order.lat is None and order.lon is None
    assert [line.line_total for line in order.lines] == [50000, 60000]


def test_valid_location_order_ignores_text():
    order = validate_order(
        good_payload(address_type="location", address_text="", lat=41.311081, lon=69.240562), TEST_MENU
    )
    assert order.address_type == "location"
    assert order.address_text is None
    assert (order.lat, order.lon) == (41.311081, 69.240562)


def test_name_whitespace_normalized():
    order = validate_order(good_payload(name="  Aziz   Karimov "), TEST_MENU)
    assert order.name == "Aziz Karimov"


# ------------------------------------------------------------ soxta narx

def test_fake_price_is_ignored():
    data = good_payload(
        items=[{"id": "burger-1", "qty": 2, "price": 1}, {"id": "pizza-1", "qty": 1, "price": 0}],
        total=1,
    )
    order = validate_order(data, TEST_MENU)
    assert order.total == 110000, "Narx klientdan emas, menyudan olinishi kerak"
    assert all(line.price in (25000, 60000) for line in order.lines)


# ------------------------------------------------------------ telefon

def test_bad_phones():
    for phone in [
        "901234567",
        "+99890123456",
        "+9989012345678",
        "+997901234567",
        "+998 90 123 45 67",
        "+99890123456a",
        "+998٩٠١٢٣٤٥٦٧",  # arabcha raqamlar - \d bilan o'tib ketardi
        12345,
        None,
    ]:
        expect_error(good_payload(phone=phone))


# ------------------------------------------------------------ miqdor

def test_bad_qty():
    for qty in [0, -1, 51, 1.5, "2", True, None]:
        expect_error(good_payload(items=[{"id": "burger-1", "qty": qty}]))


def test_qty_bounds_ok():
    assert validate_order(good_payload(items=[{"id": "burger-1", "qty": 1}]), TEST_MENU).total == 25000
    assert validate_order(good_payload(items=[{"id": "burger-1", "qty": 50}]), TEST_MENU).total == 1_250_000


# ------------------------------------------------------------ ID

def test_unknown_id():
    exc = expect_error(good_payload(items=[{"id": "burger-999", "qty": 1}]))
    assert "burger-999" in exc.detail
    assert "burger-999" not in exc.user_message  # mijozga klient matni qaytarilmaydi


def test_duplicate_id():
    expect_error(good_payload(items=[{"id": "burger-1", "qty": 1}, {"id": "burger-1", "qty": 1}]))


def test_items_empty_or_too_many():
    expect_error(good_payload(items=[]))
    expect_error(good_payload(items="burger-1"))
    many = [{"id": f"x-{i}", "qty": 1} for i in range(31)]
    exc = expect_error(good_payload(items=many))
    assert "items soni" in exc.detail


# ------------------------------------------------------------ ism

def test_bad_names():
    for name in ["", "A", " A ", "x" * 51, None, 123, "Ali\x00"]:
        expect_error(good_payload(name=name))


# ------------------------------------------------------------ manzil

def test_bad_address():
    expect_error(good_payload(address_type="email"))
    expect_error(good_payload(address_type=None))
    expect_error(good_payload(address_type="text", address_text=""))
    expect_error(good_payload(address_type="text", address_text="   "))
    expect_error(good_payload(address_type="text", address_text="a" * 301))
    expect_error(good_payload(address_type="location", lat=None, lon=None))
    expect_error(good_payload(address_type="location", lat=91, lon=69))
    expect_error(good_payload(address_type="location", lat=41, lon=181))
    expect_error(good_payload(address_type="location", lat="41.3", lon="69.2"))
    expect_error(good_payload(address_type="location", lat=True, lon=69.2))


def test_address_300_ok():
    order = validate_order(good_payload(address_text="a" * 300), TEST_MENU)
    assert len(order.address_text) == 300


# ------------------------------------------------------------ JSON parse

def test_parse_payload():
    assert parse_payload(json.dumps(good_payload()))["name"] == "Aziz"
    for raw in ["", "not json", "[1,2]", '{"lat": NaN}', '{"a": Infinity}', "x" * 5000]:
        try:
            parse_payload(raw)
        except ValidationError:
            continue
        raise AssertionError(f"parse_payload xato berishi kerak edi: {raw[:30]!r}")


# ------------------------------------------------------------ menyu

def test_menu_rejects_bad_structure():
    bad_menus = [
        {"categories": [], "items": [{"id": "a", "category": "x", "name": "A", "price": 1}]},
        {"categories": [{"id": "c", "name": "C"}], "items": [{"id": "a", "category": "c", "name": "A", "price": 0}]},
        {"categories": [{"id": "c", "name": "C"}], "items": [{"id": "a", "category": "c", "name": "A", "price": "1000"}]},
        {"categories": [{"id": "c", "name": "C"}], "items": [
            {"id": "a", "category": "c", "name": "A", "price": 1},
            {"id": "a", "category": "c", "name": "B", "price": 2},
        ]},
    ]
    for data in bad_menus:
        try:
            parse_menu(data)
        except MenuError:
            continue
        raise AssertionError(f"MenuError kutilgan edi: {data}")


def test_menu_store_reloads_on_change():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "menu.json"
        base = {"categories": [{"id": "c", "name": "C"}], "items": [{"id": "a", "category": "c", "name": "A", "price": 100}]}
        path.write_text(json.dumps(base), encoding="utf-8")
        store = MenuStore(path)
        assert store.get().items["a"].price == 100

        time.sleep(0.05)
        base["items"][0]["price"] = 2000
        path.write_text(json.dumps(base), encoding="utf-8")
        assert store.get().items["a"].price == 2000, "Narx o'zgarganda qayta yuklanishi kerak"

        # Buzuq fayl - eski menyu qoladi
        time.sleep(0.05)
        path.write_text("{ buzuq", encoding="utf-8")
        assert store.get().items["a"].price == 2000


# ------------------------------------------------------------ manzil: hudud cheklovi yo'q, lokatsiya shart

def _loc(lat, lon):
    return good_payload(address_type="location", address_text="", lat=lat, lon=lon)


def test_any_location_accepted_no_area_limit():
    for name, (lat, lon) in {
        "Registon": (39.6548, 66.9757),
        "Urgut": (39.4020, 67.2430),
        "Toshkent": (41.2995, 69.2401),
        "Moskva": (55.7558, 37.6173),
    }.items():
        order = validate_order(_loc(lat, lon), TEST_MENU, require_location=True)
        assert (order.lat, order.lon) == (lat, lon), name


def test_new_order_requires_location():
    try:
        validate_order(good_payload(address_text="Samarqand, Registon ko'chasi 5"), TEST_MENU, require_location=True)
    except ValidationError as exc:
        assert exc.code == "location" and exc.key == "val.loc_required"
    else:
        raise AssertionError("lokatsiyasiz buyurtma rad etilishi kerak")
    for bad in ((95.0, 66.0), (39.0, 181.0)):
        try:
            validate_order(_loc(*bad), TEST_MENU, require_location=True)
        except ValidationError as exc:
            assert exc.key == "val.loc_bad"
        else:
            raise AssertionError(bad)
    # eski ma'lumotlar uchun matnli manzil tekshiruvi qoladi (require_location=False)
    order = validate_order(good_payload(address_text="Registon 5"), TEST_MENU)
    assert order.address_type == "text" and order.address_text == "Registon 5"


def test_miniapp_has_no_area_limit_and_no_fee_formula():
    js = (BOT_DIR.parent / "miniapp" / "app.js").read_text(encoding="utf-8")
    assert "DELIVERY_AREA" not in js and "radiusKm" not in js and "distanceKm" not in js
    # narx formulasi Mini App'da takrorlanmaydi: tarif maydonlari bilan arifmetika va "billable" yo'q
    assert "billable" not in js
    for field in ("extra_per_km", "base_km"):
        for op in (" *", " +", " -", "* ", "+ ", "- "):
            assert f"{field}{op}" not in js.replace(f"tf.{field}", "").replace(f"d.{field}", ""), (field, op)
    assert "/api/delivery-quote" in js and "/api/config" in js


# ------------------------------------------------------------ ish vaqti va yetkazish narxi

def test_business_hours_boundaries():
    from datetime import time as t

    from business import is_open_at

    cases = {t(9, 59): False, t(10, 0): True, t(2, 59): True, t(3, 0): False, t(23, 30): True, t(0, 30): True,
             t(23, 59): True, t(0, 0): True, t(5, 0): False}
    for when, expected in cases.items():
        assert is_open_at(when) is expected, f"{when}: {expected} kutilgan edi"


def test_business_hours_uses_samarkand_time():
    from datetime import datetime, timezone

    import business
    import config

    # 04:30 UTC = 09:30 Samarqand (yopiq); 05:00 UTC = 10:00 (ochiq)
    assert config.LOCAL_TZ.utcoffset(datetime(2026, 11, 16)).total_seconds() == 5 * 3600
    for utc_hm, expected in (((4, 30), False), ((5, 0), True), ((21, 59), True), ((22, 0), False)):
        local = datetime(2026, 11, 16, *utc_hm, tzinfo=timezone.utc).astimezone(config.LOCAL_TZ)
        assert business.is_open_at(local.time()) is expected, utc_hm


def test_delivery_fee_added_and_client_sums_ignored():
    order = validate_order(good_payload(total=1, items_total=1, delivery_fee=0), TEST_MENU, delivery_fee=10000)
    # (yangi buyurtmalarda delivery_fee serverda masofadan - process_order / delivery.py)
    assert order.items_total == 110000
    assert order.delivery_fee == 10000
    assert order.total == 120000


def test_business_config_matches_miniapp():
    import re as _re

    import config

    js = (BOT_DIR.parent / "miniapp" / "app.js").read_text(encoding="utf-8")
    m = _re.search(r"BUSINESS = \{ deliveryFee: (\d+), openFrom: '(\d\d:\d\d)', closeAt: '(\d\d:\d\d)', utcOffsetHours: (\d+) \}", js)
    assert m, "app.js da BUSINESS topilmadi"
    assert int(m.group(1)) == config.DEFAULT_DELIVERY_BASE_FEE  # faqat "...dan" matni uchun zaxira
    assert m.group(2) == f"{config.OPEN_FROM:%H:%M}"
    assert m.group(3) == f"{config.CLOSE_AT:%H:%M}"
    assert int(m.group(4)) == config.UTC_OFFSET_HOURS


# ------------------------------------------------------------ ixtiyoriy tarkib ("items") maydoni

def test_item_contents_optional_and_price_unchanged():
    menu = parse_menu({
        "categories": [{"id": "kombo", "name": "Kombo"}],
        "items": [
            {"id": "kombo-1", "category": "kombo", "name": "Kombo", "price": 80000,
             "items": ["1 kg Chicken", "2 ta fri (100 gr)", "2 ta sous"]},
            {"id": "kombo-2", "category": "kombo", "name": "Oddiy", "price": 50000},
        ],
    })
    assert menu.items["kombo-1"].contents == ("1 kg Chicken", "2 ta fri (100 gr)", "2 ta sous")
    assert menu.items["kombo-2"].contents == ()
    data = good_payload(items=[{"id": "kombo-1", "qty": 2}])
    assert validate_order(data, menu).total == 160000  # narx faqat price dan


def test_item_contents_bad_type_rejected():
    for bad in ["1 kg Chicken", [1, 2], [""], {"a": 1}]:
        try:
            parse_menu({"categories": [{"id": "c", "name": "C"}],
                        "items": [{"id": "x", "category": "c", "name": "X", "price": 1, "items": bad}]})
        except MenuError:
            continue
        raise AssertionError(f"MenuError kutilgan edi: {bad!r}")


def test_item_group_variant_optional():
    menu = parse_menu({
        "categories": [{"id": "c", "name": "C"}],
        "items": [
            {"id": "w-1", "category": "c", "name": "Wings (6 dona)", "price": 35000, "group": "Wings", "variant": "6 dona"},
            {"id": "w-2", "category": "c", "name": "Wings (1 kg)", "price": 85000, "group": "Wings", "variant": "1 kg"},
            {"id": "x", "category": "c", "name": "Oddiy", "price": 1000},
        ],
    })
    assert (menu.items["w-2"].group, menu.items["w-2"].variant) == ("Wings", "1 kg")
    assert menu.items["x"].group == ""
    # Narx har variantning o'z ID si va price idan
    assert validate_order(good_payload(items=[{"id": "w-1", "qty": 1}, {"id": "w-2", "qty": 1}]), menu).items_total == 120000
    for bad in ({"group": "W"}, {"variant": "6 dona"}, {"group": 1, "variant": "x"}):
        try:
            parse_menu({"categories": [{"id": "c", "name": "C"}],
                        "items": [{"id": "x", "category": "c", "name": "X", "price": 1, **bad}]})
        except MenuError:
            continue
        raise AssertionError(f"MenuError kutilgan edi: {bad}")


def test_real_menu_chicken_groups():
    menu = load_menu_file(BOT_DIR.parent / "miniapp" / "menu.json")
    chicken = [i for i in menu.items.values() if i.category == "chicken"]
    groups = {i.group for i in chicken if i.group}
    assert len(chicken) == 22 and len(groups) == 8, (len(chicken), groups)
    assert menu.items["chicken-22"].group == ""  # Butun tovuq - yakka


def test_real_menu_kombo_contents():
    menu = load_menu_file(BOT_DIR.parent / "miniapp" / "menu.json")
    for item_id in ("kombo-1", "kombo-2", "kombo-3"):
        assert menu.items[item_id].contents, item_id


def test_real_menu_json_is_valid():
    menu = load_menu_file(BOT_DIR.parent / "miniapp" / "menu.json")
    assert len(menu.categories) >= 7 and len(menu.items) >= 56


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
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
