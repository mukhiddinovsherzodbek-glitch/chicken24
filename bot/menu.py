"""Menyu: asosiy manba - Pages'dagi menu.json (Mini App bilan bir xil fayl),
zaxira - lokal menu.json. Lokal fayl o'zgarsa avtomatik qayta yuklanadi."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_REMOTE_MENU_BYTES = 1_000_000


class MenuError(Exception):
    """menu.json o'qilmadi yoki tuzilmasi noto'g'ri."""


@dataclass(frozen=True)
class MenuItem:
    id: str
    category: str
    name: str
    price: int  # so'mda, butun son
    image: str = ""
    # Ixtiyoriy tarkib ro'yxati (masalan kombo: ["1 kg Chicken", "2 ta fri"]).
    # Faqat ko'rsatish uchun - narx hisobiga ta'sir qilmaydi.
    contents: tuple[str, ...] = ()
    # Ixtiyoriy guruh (Mini App'da bitta karta) va variant ("6 dona", "1 kg"). Faqat ko'rsatish uchun.
    group: str = ""
    variant: str = ""


@dataclass(frozen=True)
class Menu:
    categories: dict[str, str]  # id -> nom
    items: dict[str, MenuItem]  # id -> taom


def _is_int(value: Any) -> bool:
    # bool ham int'ning vorisi, uni rad etamiz
    return isinstance(value, int) and not isinstance(value, bool)


def parse_menu(data: Any) -> Menu:
    """JSON obyektidan Menu yasaydi va tuzilmani qat'iy tekshiradi."""
    if not isinstance(data, dict):
        raise MenuError("menu.json ildizi obyekt bo'lishi kerak")

    raw_categories = data.get("categories")
    raw_items = data.get("items")
    if not isinstance(raw_categories, list) or not isinstance(raw_items, list):
        raise MenuError("menu.json da 'categories' va 'items' massivlari bo'lishi kerak")

    categories: dict[str, str] = {}
    for index, cat in enumerate(raw_categories):
        if not isinstance(cat, dict):
            raise MenuError(f"categories[{index}] obyekt emas")
        cat_id, name = cat.get("id"), cat.get("name")
        if not isinstance(cat_id, str) or not cat_id.strip():
            raise MenuError(f"categories[{index}].id bo'sh yoki matn emas")
        if not isinstance(name, str) or not name.strip():
            raise MenuError(f"categories[{index}].name bo'sh yoki matn emas")
        if cat_id in categories:
            raise MenuError(f"Kategoriya ID takrorlangan: {cat_id}")
        categories[cat_id] = name.strip()

    items: dict[str, MenuItem] = {}
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise MenuError(f"items[{index}] obyekt emas")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise MenuError(f"items[{index}].id bo'sh yoki matn emas")
        if item_id in items:
            raise MenuError(f"Taom ID takrorlangan: {item_id}")
        category = item.get("category")
        if category not in categories:
            raise MenuError(f"{item_id}: kategoriya '{category}' categories ichida yo'q")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise MenuError(f"{item_id}: name bo'sh")
        price = item.get("price")
        if not _is_int(price) or price <= 0:
            raise MenuError(f"{item_id}: price musbat butun son bo'lishi kerak")
        image = item.get("image", "")
        contents = item.get("items", [])  # ixtiyoriy tarkib ro'yxati
        if contents is None:
            contents = []
        if not isinstance(contents, list) or not all(isinstance(c, str) and c.strip() for c in contents):
            raise MenuError(f"{item_id}: items bo'sh bo'lmagan matnlar ro'yxati bo'lishi kerak")
        group, variant = item.get("group", ""), item.get("variant", "")
        for field, value in (("group", group), ("variant", variant)):
            if not isinstance(value, str):
                raise MenuError(f"{item_id}: {field} matn bo'lishi kerak")
        if bool(group.strip()) != bool(variant.strip()):
            raise MenuError(f"{item_id}: group va variant birga bo'lishi kerak")
        items[item_id] = MenuItem(
            id=item_id,
            category=category,
            name=name.strip(),
            price=price,
            image=image if isinstance(image, str) else "",
            contents=tuple(c.strip() for c in contents),
            group=group.strip(),
            variant=variant.strip(),
        )

    return Menu(categories=categories, items=items)


def load_menu_file(path: Path) -> Menu:
    try:
        # utf-8-sig: Windows muharrirlari qo'shadigan BOM ham muammo qilmaydi
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise MenuError(f"menu.json o'qilmadi: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MenuError(f"menu.json JSON xatosi: {exc}") from exc
    return parse_menu(data)


class MenuStore:
    """Menyuni keshlaydi va fayl o'zgarganini (mtime/hajm) sezsa qayta o'qiydi.

    Shu sababli narxni o'zgartirgach botni qayta ishga tushirish shart emas.
    Yangi fayl buzuq bo'lsa, oxirgi to'g'ri menyu ishlatilishda davom etadi.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._menu: Menu | None = None
        self._signature: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Menu:
        try:
            stat = self._path.stat()
        except OSError as exc:
            if self._menu is not None:
                logger.error("menu.json topilmadi (%s), eski menyu ishlatiladi", exc)
                return self._menu
            raise MenuError(f"menu.json topilmadi: {self._path}") from exc

        signature = (stat.st_mtime_ns, stat.st_size)
        if self._menu is None or signature != self._signature:
            try:
                menu = load_menu_file(self._path)
            except MenuError:
                if self._menu is not None:
                    logger.exception("Yangilangan menu.json buzuq, eski menyu ishlatiladi")
                    return self._menu
                raise
            self._menu = menu
            self._signature = signature
            logger.info(
                "Menyu yuklandi: %d kategoriya, %d taom (%s)",
                len(menu.categories),
                len(menu.items),
                self._path,
            )
        return self._menu


# ---------------------------------------------------------------- Pages'dagi menyu

def menu_diff(local: Menu, remote: Menu) -> list[str]:
    """Narx hisobiga ta'sir qiladigan farqlar (kategoriya, nom, narx, taom bor-yo'qligi)."""
    diffs: list[str] = []
    for cat_id in sorted(set(local.categories) | set(remote.categories)):
        if local.categories.get(cat_id) != remote.categories.get(cat_id):
            diffs.append(f"kategoriya {cat_id}: lokal={local.categories.get(cat_id)!r}, Pages={remote.categories.get(cat_id)!r}")
    for item_id in sorted(set(local.items) | set(remote.items)):
        a, b = local.items.get(item_id), remote.items.get(item_id)
        if a is None:
            diffs.append(f"{item_id}: faqat Pages'da bor")
        elif b is None:
            diffs.append(f"{item_id}: faqat lokal nusxada bor")
        elif (a.name, a.price, a.category) != (b.name, b.price, b.category):
            diffs.append(f"{item_id}: lokal {a.name!r} {a.price} / Pages {b.name!r} {b.price}")
    return diffs


async def fetch_json_url(url: str, timeout: float = 10.0) -> Any:
    """URL dan JSON oladi (standart kutubxona, alohida oqimda - event loop bloklanmaydi)."""

    def _get() -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": "chicken24-bot", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (URL sozlamadan)
            body = resp.read(MAX_REMOTE_MENU_BYTES + 1)
        if len(body) > MAX_REMOTE_MENU_BYTES:
            raise MenuError("Pages'dagi menu.json juda katta")
        return json.loads(body.decode("utf-8-sig"))

    return await asyncio.to_thread(_get)


class MenuProvider:
    """Menyu manbai: avval Pages (WEBAPP_URL/menu.json), bo'lmasa lokal nusxa.

    - refresh() Pages'dan yangilaydi (main.py har 5 daqiqada chaqiradi).
    - get() narx hisoblash uchun menyu qaytaradi:
        Pages nusxasi oxirgi muvaffaqiyatli yangilanishdan beri `max_age` soniyadan
        oshmagan bo'lsa - Pages, aks holda lokal menu.json.
      Ya'ni bitta tasodifiy tarmoq uzilishida eski lokal narxlarga sakrab o'tilmaydi,
      lekin Pages uzoq vaqt olinmasa lokal nusxaga qaytiladi.
    - Pages va lokal nusxa farq qilsa, logga ogohlantirish yoziladi (farq o'zgargandagina).
    """

    def __init__(
        self,
        local: MenuStore,
        remote_url: str | None,
        *,
        max_age: float = 900.0,
        fetch: Callable[[str], Awaitable[Any]] = fetch_json_url,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._local = local
        self._url = remote_url
        self._max_age = max_age
        self._fetch = fetch
        self._clock = clock
        self._remote: Menu | None = None
        self._remote_at: float | None = None
        self._last_diff: tuple[str, ...] | None = None
        self._using_local_logged = False

    @property
    def remote_url(self) -> str | None:
        return self._url

    @property
    def source(self) -> str:
        """Hozir narx qaysi manbadan olinadi: 'pages' yoki 'local'."""
        return "pages" if self._remote_fresh() else "local"

    def _remote_fresh(self) -> bool:
        return (
            self._remote is not None
            and self._remote_at is not None
            and self._clock() - self._remote_at <= self._max_age
        )

    async def refresh(self) -> bool:
        """Pages'dan menyuni yangilaydi. Muvaffaqiyatli bo'lsa True."""
        if not self._url:
            return False
        try:
            remote = parse_menu(await self._fetch(self._url))
        except Exception as exc:  # tarmoq, JSON yoki tuzilma xatosi
            fallback = "oxirgi Pages nusxasi" if self._remote_fresh() else "lokal menu.json"
            logger.warning("Pages menyusi olinmadi (%s): %s — %s ishlatiladi", self._url, exc, fallback)
            return False
        first = self._remote is None
        self._remote, self._remote_at = remote, self._clock()
        self._using_local_logged = False
        if first:
            logger.info("Pages menyusi olindi: %d kategoriya, %d taom (%s)",
                        len(remote.categories), len(remote.items), self._url)
        self._compare_with_local(remote)
        return True

    def _compare_with_local(self, remote: Menu) -> None:
        try:
            local = self._local.get()
        except MenuError as exc:
            logger.warning("Lokal menu.json o'qilmadi, solishtirib bo'lmadi: %s", exc)
            return
        diffs = tuple(menu_diff(local, remote))
        if diffs and diffs != self._last_diff:
            logger.warning(
                "Pages va lokal menu.json farq qiladi (%d ta farq, narx Pages'dan olinadi): %s%s",
                len(diffs), "; ".join(diffs[:5]), " ..." if len(diffs) > 5 else "",
            )
        elif not diffs and self._last_diff:
            logger.info("Pages va lokal menu.json endi bir xil")
        self._last_diff = diffs

    def get(self) -> Menu:
        if self._remote_fresh():
            return self._remote  # type: ignore[return-value]
        if self._url and not self._using_local_logged:
            logger.warning("Pages menyusi yo'q yoki eskirgan — narx lokal menu.json dan hisoblanadi")
            self._using_local_logged = True
        try:
            return self._local.get()
        except MenuError:
            # Lokal ham bo'lmasa, eskirgan bo'lsa ham oxirgi Pages nusxasi ishlatiladi
            if self._remote is not None:
                logger.error("Lokal menu.json o'qilmadi - eskirgan Pages nusxasi ishlatiladi")
                return self._remote
            raise
