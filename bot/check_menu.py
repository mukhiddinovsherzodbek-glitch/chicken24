"""menu.json ni tekshirish va images/ dagi yangi taom rasmlarini menyuga qo'shish.

    python check_menu.py               # tekshiruv: tuzilma, rasm fayllari, menyuga kirmagan rasmlar
    python check_menu.py --add-missing # images/<kategoriya>-<raqam>.webp uchun namunaviy yozuv qo'shadi

--add-missing qo'shgan nom/tavsif/narx VAQTINCHALIK - ularni albatta o'zgartiring!
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from config import DEFAULT_MENU_PATH
from menu import MenuError, parse_menu

ITEM_IMAGE_RE = re.compile(r"^([a-z0-9_]+)-(\d+)\.(webp|jpg|jpeg|png)$", re.IGNORECASE)
PLACEHOLDER_PRICE = 25000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--menu", type=Path, default=DEFAULT_MENU_PATH, help="menu.json yo'li")
    parser.add_argument("--add-missing", action="store_true", help="menyuda yo'q rasmlar uchun namunaviy taom qo'shish")
    args = parser.parse_args()

    menu_path: Path = args.menu.resolve()
    base_dir = menu_path.parent
    data = json.loads(menu_path.read_text(encoding="utf-8-sig"))

    try:
        menu = parse_menu(data)
    except MenuError as exc:
        print(f"[XATO] menu.json tuzilmasi: {exc}")
        return 1
    print(f"[OK] menu.json: {len(menu.categories)} kategoriya, {len(menu.items)} taom")

    problems = 0
    # 1) Har bir kategoriya va taom rasmi mavjudmi
    for cat in data["categories"]:
        image = cat.get("image")
        if image and not (base_dir / image).is_file():
            problems += 1
            print(f"[YO'Q] kategoriya '{cat['id']}' rasmi: {image}")
    for item in data["items"]:
        image = item.get("image")
        if not image:
            problems += 1
            print(f"[YO'Q] taom '{item['id']}' uchun image ko'rsatilmagan")
        elif not (base_dir / image).is_file():
            problems += 1
            print(f"[YO'Q] taom '{item['id']}' rasmi: {image}")

    # 2) images/ da bor, lekin menyuda yo'q taom rasmlari
    used = {Path(i.get("image", "")).name for i in data["items"]}
    unregistered = []
    images_dir = base_dir / "images"
    for path in sorted(images_dir.iterdir() if images_dir.is_dir() else []):
        match = ITEM_IMAGE_RE.match(path.name)
        if not match or path.name in used or path.name.startswith("cat-"):
            continue
        category, number = match.group(1).lower(), int(match.group(2))
        if category not in menu.categories:
            print(f"[OGOHLANTIRISH] {path.name}: '{category}' degan kategoriya yo'q")
            continue
        unregistered.append((category, number, path.name))

    if unregistered:
        print(f"[INFO] Menyuga kiritilmagan {len(unregistered)} ta rasm: " + ", ".join(n for _, _, n in unregistered))
    if not data["items"]:
        print("[INFO] items bo'sh - barcha kategoriyalar Mini App'da 'Tez orada' ko'rinadi")

    if args.add_missing and unregistered:
        existing_ids = {i["id"] for i in data["items"]}
        added = 0
        for category, number, filename in unregistered:
            item_id = f"{category}-{number}"
            if item_id in existing_ids:
                print(f"[O'TKAZILDI] {item_id} ID band, {filename} qo'lda qo'shilsin")
                continue
            data["items"].append(
                {
                    "id": item_id,
                    "category": category,
                    "name": f"{menu.categories[category]} №{number}",
                    "desc": "Tavsifni o'zgartiring",
                    "price": PLACEHOLDER_PRICE,
                    "image": f"images/{filename}",
                }
            )
            added += 1
        menu_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] {added} ta namunaviy taom qo'shildi. NARX VA NOMLARNI O'ZGARTIRING!")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
