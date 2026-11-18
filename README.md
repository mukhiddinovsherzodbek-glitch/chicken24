# Chicken24 — Telegram bot + Mini App

> ⚠️ **NARX VA NOMLARNI O'ZGARTIRING.** `miniapp/menu.json` — menyuning yagona manbasi.
> Hozir `miniapp/images/` da faqat 6 ta kategoriya rasmi bor, taom rasmlari yo'q, shuning uchun
> `items` bo'sh va Mini App'da barcha kategoriyalar **"Tez orada"** bo'lib ko'rinadi.

## Arxitektura

```
Telegram foydalanuvchisi
   │ /start  →  reply-klaviatura: "Buyurtma berish" (KeyboardButton + WebAppInfo)
   ▼
Mini App (miniapp/, statik HTML/CSS/JS, HTTPS)
   │ fetch menu.json → savat (localStorage) → forma → Telegram.WebApp.sendData(JSON)
   │ yuboriladi: name, phone, address_type, address_text, lat, lon, items[{id, qty}]  (narx YO'Q)
   ▼
Bot (bot/, aiogram 3, polling)
   │ F.web_app_data → spam limiti → JSON parse → validatsiya → narxni menu.json dan hisoblash
   │ → SQLite (orders, order_items) → ADMIN_ID ga HTML chek (+ lokatsiya) → mijozga tasdiq
```

| Fayl | Vazifasi |
|---|---|
| `bot/main.py` | Kirish nuqtasi: logging, baza, Dispatcher, polling |
| `bot/config.py` | `.env` ni o'qish va tekshirish, doimiylar |
| `bot/handlers.py` | `/start`, `web_app_data`, admin cheki, spam limiti, xato handleri |
| `bot/validators.py` | Buyurtmani tekshirish va summani menyudan hisoblash |
| `bot/menu.py` | `menu.json` ni o'qish; fayl o'zgarsa avtomatik qayta yuklash |
| `bot/db.py` | SQLite (aiosqlite), parametrlangan so'rovlar |
| `bot/check_menu.py` | `menu.json` va rasmlarni tekshirish, yangi rasmlarni menyuga qo'shish |
| `bot/tests/test_validation.py` | Validatsiya va summa testlari |
| `miniapp/index.html`, `style.css`, `app.js` | Mini App |
| `miniapp/menu.json` | Menyu (kategoriyalar + taomlar) |

## Menyuni to'ldirish

1. Taom rasmini `miniapp/images/<kategoriya>-<raqam>.webp` nomi bilan qo'ying (masalan `burger-1.webp`, `pizza-3.webp`).
2. `bot/` papkasida (venv faol holda):
   ```bash
   python check_menu.py --add-missing
   ```
   Har bir yangi rasm uchun namunaviy yozuv qo'shiladi (`"Burger №1"`, `"Tavsifni o'zgartiring"`, `25000`).
3. `menu.json` da `name`, `desc`, `price` ni haqiqiy qiymatlarga almashtiring. Narx — butun son (so'm).
4. Tekshiruv: `python check_menu.py`. Botni qayta ishga tushirish shart emas — keyingi buyurtmada yangi menyu o'qiladi.

Taom yozuvi namunasi:
```json
{ "id": "burger-1", "category": "burger", "name": "Klassik burger", "desc": "Mol go'shti, pishloq, sous", "price": 25000, "image": "images/burger-1.webp" }
```
`id` ni keyin o'zgartirmang: savat va "Buyurtmalarim" shu ID ga bog'langan.

## Ishga tushirish

### Bot (Windows PowerShell)
```powershell
cd bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # keyin .env ni to'ldiring
python main.py
```
Linux/macOS: `python3 -m venv venv && source venv/bin/activate`, `cp .env.example .env`.

### Mini App (lokal + HTTPS tunnel)
```powershell
cd miniapp
python -m http.server 8080
```
Alohida terminalda:
```powershell
cloudflared tunnel --url http://localhost:8080
```
Chiqqan `https://....trycloudflare.com` havolani `bot/.env` dagi `WEBAPP_URL` ga yozing va botni qayta ishga tushiring.
Foydalanuvchilar yangi tugmani olishi uchun botga qayta `/start` yozishi kerak (tugma URL'i klaviaturada saqlanadi).

**Doimiy HTTPS (bepul):** `miniapp/` papkasini Cloudflare Pages yoki GitHub Pages'ga joylang.
Bot `menu.json` ni lokal fayldan o'qiydi — narxni o'zgartirsangiz, ikkala joyda ham bir xil fayl bo'lishi kerak
(hosting'ga qayta yuklang yoki `.env` dagi `MENU_PATH` ni sinxron nusxaga yo'naltiring).

Brauzerda Telegram'siz sinash: `http://localhost:8080/?debug=1` — `sendData` o'rniga payload konsolga yoziladi.

## BotFather va ADMIN_ID
- `@BotFather` → `/newbot` → nom va username → berilgan tokenni `BOT_TOKEN` ga yozing.
- `ADMIN_ID`: o'z Telegram ID raqamingiz. Masalan `@userinfobot` ga yozing — u ID ni qaytaradi.
- **Admin botga avval `/start` yozishi shart**, aks holda bot unga xabar yubora olmaydi.

## Testlar
```powershell
cd bot
python -m py_compile main.py config.py db.py handlers.py menu.py validators.py check_menu.py
python tests/test_validation.py      # yoki: pip install pytest; python -m pytest tests -q
python check_menu.py
```

## Cheklovlar
- Spam limiti (30 s) xotirada saqlanadi — bot qayta ishga tushsa nollanadi.
- "Buyurtmalarim" faqat shu qurilmaning `localStorage` ida. Quick tunnel URL'i har ishga tushirishda o'zgaradi —
  yangi domen = bo'sh tarix va savat. Doimiy hosting'da bu muammo yo'q.
- Admin'ga chek yetib bormasa, buyurtma bazada `notified = 0` bilan qoladi va mijozga xato ko'rsatiladi.
- Hozircha yo'q: admin buyruqlari, statuslar, yetkazib berish narxi, to'lov, ish vaqti.
