# Chicken24 — Telegram bot + Mini App

Fast-food restoran uchun buyurtma qabul qiluvchi Telegram bot va Mini App (o'zbekcha / ruscha).

- **Mijoz:** botda tilni tanlaydi → «Buyurtma berish» → Mini App'da taom tanlaydi, ism/telefon/manzil yozadi →
  bot tasdiq yuboradi (5 daqiqa ichida bekor qilish mumkin).
- **Admin (restoran):** har buyurtma uchun chek (taomlar, summa, telefon, xarita havolasi), `/hisobot`,
  `/excel`, `/backup`.

To'liq joylash, sozlash va xavfsizlik yo'riqnomasi: **[deploy/DEPLOY_UZ.md](deploy/DEPLOY_UZ.md)**.

## Arxitektura

```
Telegram foydalanuvchisi
   │ /start → til → xabar ostida inline "Buyurtma berish" + matn maydoni yonida "Buyurtma" menyu tugmasi
   ▼
Mini App (miniapp/, statik HTML/CSS/JS, Cloudflare Pages)
   │ menu.json → savat (localStorage) → forma
   │ POST {bot serveri}/api/orders   (Authorization: tma <Telegram initData>;  narx YUBORILMAYDI)
   ▼
Bot (bot/, Python 3.12, aiogram 3)
   │ initData imzosi → ish vaqti → 30 s cheklov → validatsiya → narx menu.json dan → hudud tekshiruvi
   │ → baza (orders, order_items) → mijozga tasdiq → admin'ga chek (+ lokatsiya)
   ▼
Baza: PostgreSQL (Neon) serverda, SQLite lokal kompyuterda
```

Bot ikki rejimda ishlaydi (muhit o'zgaruvchilariga qarab, kod bir xil):

| Rejim | Qachon | Baza | Telegram |
|---|---|---|---|
| Server (Render) | `WEBHOOK_URL` + `WEBHOOK_SECRET` + `DATABASE_URL` berilgan | PostgreSQL (Neon) | webhook, HTTP server (`/health`, `/api/*`) |
| Lokal | berilmagan | SQLite (`bot/chicken24.db`) | polling |

> Bir vaqtda faqat BITTA joyda ishlasin. Render ishlab turganda kompyuterda `python main.py` ni yoqmang.

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `bot/main.py` | Kirish nuqtasi: rejim tanlash, fon vazifalari, qayta ishga tushish |
| `bot/config.py` | Muhit o'zgaruvchilari va doimiylar (ish vaqti, yetkazish narxi, hudud) |
| `bot/handlers.py` | `/start`, `/til`, buyurtma (`process_order`), bekor qilish, admin buyruqlari, flood himoyasi |
| `bot/orderapi.py` | Mini App API: `POST /api/orders`, `GET /api/order-status` (initData, CORS, cheklovlar) |
| `bot/webhook.py` | Webhook HTTP serveri, `/health` |
| `bot/validators.py` | Buyurtmani tekshirish, summani menyudan hisoblash |
| `bot/db.py` | Baza: SQLite va PostgreSQL uchun bitta interfeys (parametrlangan so'rovlar) |
| `bot/menu.py` | Menyu: Pages'dagi `menu.json` (asosiy) va lokal nusxa (zaxira) |
| `bot/i18n.py` | Bot matnlari (uz / ru) |
| `bot/reports.py`, `bot/excel_export.py`, `bot/backup.py` | `/hisobot`, `/excel`, `/backup` va kunlik zaxira |
| `bot/selfping.py`, `bot/logsafe.py`, `bot/commands.py`, `bot/business.py` | Uxlamaslik, loglarni tozalash, buyruqlar menyusi, ish vaqti |
| `bot/tests/` | Testlar (Telegram'siz ishlaydi) |
| `miniapp/` | Mini App: `index.html`, `app.js`, `result.js`, `i18n.js`, `config.js`, `style.css`, `_headers` |
| `miniapp/menu.json` | **Menyuning yagona manbasi** (kategoriyalar, taomlar, narxlar) |
| `tools/migrate_sqlite_to_postgres.py` | SQLite → PostgreSQL bir martalik ko'chirish |
| `render.yaml`, `.github/workflows/keep-alive.yml` | Render sozlamasi, uxlamaslik uchun zaxira ping |

## Menyu va narxlarni o'zgartirish

1. `miniapp/menu.json` ni tahrirlang (narx — butun son, so'mda).
2. Tekshiring: `cd bot` → `python check_menu.py`.
3. Mini App'ni qayta joylang: `npx wrangler pages deploy miniapp --project-name chicken24 --branch main`.

Bot menyuni Pages'dan har 5 daqiqada o'zi oladi — qayta ishga tushirish shart emas.

Taom yozuvi namunasi:
```json
{ "id": "burger-1", "category": "burger", "name": "Klassik burger", "desc": "Mol go'shti, pishloq, sous",
  "desc_ru": "Говядина, сыр, соус", "price": 25000, "image": "assets/products/burgers/01.webp" }
```
- `id` ni keyin o'zgartirmang: savat va «Buyurtmalarim» shu ID ga bog'langan.
- Rasmni almashtirsangiz **fayl nomini ham o'zgartiring** — rasmlar mijoz telefonida 1 kun keshlanadi.
- Ish vaqti, yetkazish narxi, hudud: `bot/config.py` **va** `miniapp/app.js` (`BUSINESS`, `DELIVERY_AREA`) —
  ikkalasi bir xil bo'lishi shart (test tekshiradi).

## Lokal ishga tushirish (Windows PowerShell)

```powershell
cd bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # keyin .env ni to'ldiring: BOT_TOKEN, ADMIN_ID, WEBAPP_URL
python main.py
```

- `BOT_TOKEN` — @BotFather bergan token. `ADMIN_ID` — cheklar keladigan Telegram ID (raqam).
- **Admin botga avval `/start` yozishi shart**, aks holda bot unga xabar yubora olmaydi.
- Lokal `.env` ga `DATABASE_URL` / `WEBHOOK_*` yozmang (aks holda lokal bot ishchi bazaga ulanadi).

## Testlar

```powershell
cd bot
Get-ChildItem tests\test_*.py | ForEach-Object { .\venv\Scripts\python.exe $_.FullName | Select-Object -Last 1 }
python check_menu.py
```
Har fayl oxirida `N/N test o'tdi` chiqishi kerak. PostgreSQL testlari faqat `TEST_DATABASE_URL` (alohida test
bazasi) berilganda ishlaydi, aks holda o'tkazib yuboriladi. Kodni `git push` qilishdan oldin testlarni o'tkazing —
Render `main` ga tushgan har o'zgarishni darhol joylaydi.

## Xavfsizlik (qisqacha)

Narx faqat serverda hisoblanadi; buyurtma Telegram imzosi (initData) bilan tasdiqlanadi; webhook sir bilan
himoyalangan; flood va spam cheklovlari bor; bot faqat shaxsiy chatda ishlaydi; admin buyruqlari faqat `ADMIN_ID`
uchun; loglarda telefon va sirlar yashiriladi. Batafsil va topshirishdan oldingi ro'yxat: `deploy/DEPLOY_UZ.md`, **F bo'lim**.

## Cheklovlar

- Bepul reja (Render + Neon): xizmat qayta ishga tushishi yoki oylik limitga yetishi mumkin — `DEPLOY_UZ.md`, E-9.
- To'lov faqat naqd (kuryerga). Onlayn to'lov yo'q.
- Bitta admin (`ADMIN_ID`). Buyurtma holatlari (tayyorlanmoqda / yo'lda / yetkazildi) yo'q — mijoz bilan kuryer bog'lanadi.
- «Buyurtmalarim» tarixi faqat shu qurilmada (`localStorage`) saqlanadi.
- Spam cheklovlari xotirada — bot qayta ishga tushsa nollanadi.
