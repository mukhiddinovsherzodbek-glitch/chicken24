"""Pages'dagi menyu (MenuProvider) testlari: tarmoqsiz, soxta fetch va soat bilan."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_DIR))

from config import menu_url_from_webapp  # noqa: E402
from menu import MenuProvider, MenuStore, menu_diff, parse_menu  # noqa: E402


def menu_dict(price: int = 25000, extra: bool = False) -> dict:
    items = [{"id": "burger-1", "category": "burger", "name": "Burger", "price": price}]
    if extra:
        items.append({"id": "burger-2", "category": "burger", "name": "Cheese", "price": 30000})
    return {"categories": [{"id": "burger", "name": "Burger"}], "items": items}


def local_store(price: int = 25000) -> MenuStore:
    path = Path(tempfile.mkdtemp()) / "menu.json"
    path.write_text(json.dumps(menu_dict(price)), encoding="utf-8")
    return MenuStore(path)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Fetcher:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self, url):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record) -> None:
        self.messages.append(record.getMessage())


def capture_logs() -> LogCapture:
    handler = LogCapture()
    logging.getLogger("menu").addHandler(handler)
    return handler


def test_menu_url_from_webapp():
    assert menu_url_from_webapp("https://chicken24.pages.dev") == "https://chicken24.pages.dev/menu.json"
    assert menu_url_from_webapp("https://x.dev/?ok=1#h") == "https://x.dev/menu.json"


def test_pages_menu_is_used_for_prices():
    fetch = Fetcher(menu_dict(price=27000))
    provider = MenuProvider(local_store(25000), "https://x/menu.json", fetch=fetch, clock=FakeClock())
    assert asyncio.run(provider.refresh()) is True
    assert provider.source == "pages"
    assert provider.get().items["burger-1"].price == 27000


def test_falls_back_to_local_when_pages_unavailable():
    provider = MenuProvider(local_store(25000), "https://x/menu.json", fetch=Fetcher(OSError("tarmoq yo'q")), clock=FakeClock())
    assert asyncio.run(provider.refresh()) is False
    assert provider.source == "local"
    assert provider.get().items["burger-1"].price == 25000


def test_broken_pages_json_falls_back_to_local():
    provider = MenuProvider(local_store(25000), "https://x/menu.json", fetch=Fetcher({"categories": "buzuq"}), clock=FakeClock())
    assert asyncio.run(provider.refresh()) is False
    assert provider.get().items["burger-1"].price == 25000


def test_short_outage_keeps_pages_then_local_after_max_age():
    clock = FakeClock()
    fetch = Fetcher(menu_dict(price=27000))
    provider = MenuProvider(local_store(25000), "https://x/menu.json", fetch=fetch, clock=clock, max_age=900)
    asyncio.run(provider.refresh())
    fetch.result = OSError("uzilish")
    clock.t += 300
    asyncio.run(provider.refresh())
    assert provider.get().items["burger-1"].price == 27000, "qisqa uzilishda Pages nusxasi qolishi kerak"
    clock.t += 700  # oxirgi muvaffaqiyatdan 1000 s > 900 s
    assert provider.source == "local"
    assert provider.get().items["burger-1"].price == 25000


def test_difference_is_logged_once():
    logs = capture_logs()
    fetch = Fetcher(menu_dict(price=27000, extra=True))
    provider = MenuProvider(local_store(25000), "https://x/menu.json", fetch=fetch, clock=FakeClock())
    asyncio.run(provider.refresh())
    asyncio.run(provider.refresh())
    warnings = [m for m in logs.messages if "farq qiladi" in m]
    assert len(warnings) == 1, logs.messages
    assert "burger-1" in warnings[0] and "burger-2: faqat Pages'da bor" in warnings[0]


def test_menu_diff_ignores_images():
    a = parse_menu({"categories": [{"id": "c", "name": "C"}], "items": [{"id": "x", "category": "c", "name": "X", "price": 1, "image": "a.png"}]})
    b = parse_menu({"categories": [{"id": "c", "name": "C"}], "items": [{"id": "x", "category": "c", "name": "X", "price": 1, "image": "a.webp"}]})
    assert menu_diff(a, b) == []


def test_no_url_means_local_only():
    fetch = Fetcher(menu_dict(price=99999))
    provider = MenuProvider(local_store(25000), None, fetch=fetch, clock=FakeClock())
    assert asyncio.run(provider.refresh()) is False and fetch.calls == 0
    assert provider.get().items["burger-1"].price == 25000


if __name__ == "__main__":
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
