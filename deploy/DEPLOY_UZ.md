# Chicken24 — serverga joylash yo'riqnomasi

Ikki qism bor:
- **Mini App** (sayt) — Cloudflare Pages'da turadi (`https://....pages.dev`).
- **Bot** — uch xil joyda ishlay oladi (bir vaqtda faqat BITTASIDA):
  - **kompyuter** (lokal): SQLite + polling — `python main.py`, hech narsa sozlash shart emas;
  - **bot-hosting paneli** (B bo'lim): SQLite + polling;
  - **Render + Neon** (E bo'lim, bepul): PostgreSQL + webhook.

Rejimni muhit o'zgaruvchilari tanlaydi, kod bir xil:

| O'zgaruvchi | Berilsa | Berilmasa |
|---|---|---|
| `DATABASE_URL` | PostgreSQL (Neon) | SQLite (`bot/chicken24.db`) |
| `WEBHOOK_URL` + `WEBHOOK_SECRET` | webhook (HTTP server, `/health`) | polling |

> Hech qachon tokenni, `DATABASE_URL` ni (ichida parol bor) va `WEBHOOK_SECRET` ni chatga, skrinshotga
> yoki git'ga qo'ymang. Kompyuterdagi `bot/.env` ga `DATABASE_URL` / `WEBHOOK_*` YOZMANG — aks holda lokal bot
> ham Neon'ga ulanadi yoki webhook rejimiga o'tadi.

---

## A. Mini App (Cloudflare Pages)

1. `chicken24` papkasida terminal oching.
2. Joylang: `npx wrangler pages deploy miniapp --project-name chicken24 --branch main`
3. Chiqqan `https://chicken24.pages.dev` (yoki shunga o'xshash) havolani yozib oling — bu **WEBAPP_URL**.
4. Menyu yoki dizayn o'zgarsa, 2-qadamni qayta bajaring.

---

## B. Bot (bot-hosting paneli)

**Kerak:** `deploy/bot_deploy.zip` va 3 ta qiymat: bot tokeni, admin ID, WEBAPP_URL.

1. Panelda **Python** turidagi server yarating (iloji bo'lsa **Python 3.12**; 3.11 ham bo'ladi).
2. **Files** bo'limiga kiring → **Upload** → `bot_deploy.zip` ni yuklang.
3. Zip ustida **Unarchive** (Extract) ni bosing, keyin zipning o'zini o'chiring.
   `main.py` asosiy papkada (`/home/container`) turishi kerak, ichki papkada emas.
4. **Startup** bo'limida:
   - Asosiy fayl (`App py file` / `PY_FILE`): `main.py`
   - Requirements fayli (`REQUIREMENTS_FILE`): `requirements.txt`
   - Agar start buyrug'ini o'zingiz yozsangiz: `pip install -r requirements.txt && python main.py`
     (paneldagi tayyor Python shablonida odatda `pip install` o'zi bajariladi, unda faqat `python main.py`).
5. Muhit o'zgaruvchilarini kiriting (**Startup → Variables** yoki **Environment**):

   | Nomi | Majburiy | Qiymat |
   |---|---|---|
   | `BOT_TOKEN` | ha | @BotFather bergan token |
   | `ADMIN_ID` | ha | Buyurtma cheklari keladigan Telegram ID (raqam) |
   | `WEBAPP_URL` | ha | A-bo'limdagi `https://....pages.dev` havola |
   | `DB_PATH` | yo'q | Baza fayli, masalan `/home/container/data/chicken24.db`. Berilmasa: `chicken24.db` (bot yonida) |
   | `MENU_URL` | yo'q | Menyu manzili. Berilmasa: `WEBAPP_URL/menu.json` (Pages). `off` — faqat lokal fayl |
   | `MENU_PATH` | yo'q | Zaxira menyu fayli. Odatda kerak emas (zip ichidagi `menu.json`) |

   **Panelda o'zgaruvchi qo'shish joyi bo'lmasa:** Files → **New File** → nomi `.env` → ichiga yozing:
   ```
   BOT_TOKEN=...
   ADMIN_ID=...
   WEBAPP_URL=https://....pages.dev
   ```
   Bot `.env` ni ham o'qiydi. Bu faylni hech kimga yubormang.
6. **Start** ni bosing. Konsolda `Bot ishga tushdi: @...` chiqishi kerak.
7. Admin akkauntidan botga `/start` yozing (aks holda bot admin'ga chek yubora olmaydi).
8. Mijozlar yangi tugmani olishi uchun botga qayta `/start` yozishi kerak (WEBAPP_URL o'zgarganda ham).

### Menyu narxini o'zgartirish
1. Kompyuterda `miniapp/menu.json` ni tahrirlang.
2. Mini App'ni qayta joylang (A-bo'lim, 2-qadam).
3. Tamom. Bot menyuni Pages'dan (`WEBAPP_URL/menu.json`) har 5 daqiqada o'zi oladi va narxni shundan hisoblaydi.
   Botni qayta ishga tushirish shart emas.

Zip ichidagi `menu.json` — faqat **zaxira**: Pages 15 daqiqadan ko'proq olinmasa, bot shunga qaytadi.
Vaqti-vaqti bilan uni ham yangilab qo'ying (yangi zip yoki Files → `menu.json`).
Pages va zaxira farq qilsa, konsolda `Pages va lokal menu.json farq qiladi` ogohlantirishi chiqadi.

### Ish vaqti, yetkazish narxi, to'lov
- Ish vaqti **10:00 – 03:00** (Samarqand vaqti). Yopiq vaqtda bot buyurtmani qabul qilmaydi,
  Mini App yuborish tugmasini o'chiradi va tepada ogohlantirish ko'rsatadi.
- Yetkazish **10 000 so'm** (qat'iy), minimal buyurtma yo'q. Jami = taomlar + yetkazish (bot hisoblaydi).
- To'lov: faqat naqd (kuryerga).
- O'zgartirish: `config.py` dagi `OPEN_FROM`, `CLOSE_AT`, `DELIVERY_FEE` va xuddi shu qiymatlarni
  `miniapp/app.js` dagi `BUSINESS` ga yozing (test ikkalasi bir xilligini tekshiradi), keyin ikkalasini ham joylang.
- Vaqt zonasi uchun `tzdata` kutubxonasi kerak — u `requirements.txt` da bor.

### Admin buyruqlari (faqat `ADMIN_ID` uchun ishlaydi)
- **`/hisobot`** — bugun / shu hafta / shu oy: buyurtmalar soni, tushum (taomlar + yetkazish),
  o'rtacha chek, bekor qilinganlar va shu oyning eng ko'p sotilgan 5 taomi.
  Ish kuni **03:00** da almashadi (yarim tundan keyingi buyurtmalar oldingi kunga qo'shiladi).
- **`/excel`** — buyurtmalar Excel faylda (`chicken24_buyurtmalar_YYYY-MM-DD.xlsx`):
  - `/excel` — oxirgi 30 kun; `/excel_bugun`, `/excel_hafta`, `/excel_oy`, `/excel_hammasi`
    (menyudan bir bosishda). Eski yozuv ham ishlaydi: `/excel bugun`, `/excel hafta` va h.k.;
  - varaqlar: **Buyurtmalar** (har buyurtma bitta qator, lokatsiya — bosiladigan havola, bekor qilinganlar kulrang),
    **Taomlar** (har taom qatori alohida), **Xulosa** (soni, bekor qilinganlar, tushum, o'rtacha chek, top-10);
  - davrlar `/hisobot` dagi kabi: ish kuni 03:00 da almashadi, vaqtlar Samarqand vaqtida;
  - davrda buyurtma bo'lmasa, fayl emas, "Bu davrda buyurtma yo'q" xabari keladi;
  - bu fayl ko'rish/hisob uchun. **Tiklash uchun `/backup` dagi `.db` fayl kerak** — Excel'dan bazani tiklab bo'lmaydi.
- **`/backup`** — bazaning zaxira nusxasi (C bo'limi).
Boshqa foydalanuvchi bu buyruqlarni qo'lda yozsa ham bot ularni bajarmaydi va **umuman javob bermaydi**.

**"/" buyruqlar menyusi** bot har ishga tushganda qayta o'rnatiladi:
- mijozlar menyusida: **/start — Boshlash / Начать** va **/til — Tilni o'zgartirish / Сменить язык**;
- admin menyusida: /start, /til, /excel, /excel_bugun, /excel_hafta, /excel_oy, /excel_hammasi, /hisobot, /backup.
Avvalgi (eski) buyruqlar menyusi o'chiriladi, shuning uchun mijozlarga admin buyruqlari ko'rinmaydi.
Admin menyusi faqat admin botga **/start** yozgandan keyin o'rnatiladi. Agar konsolda
`Admin buyruqlar menyusi o'rnatilmadi` chiqsa: admin akkauntidan /start yozing va botni qayta ishga tushiring.
Telegram menyuni darhol yangilamasa, bot chatini yopib qayta oching.
Menyuda yashirish himoya emas — har bir admin buyrug'i `ADMIN_ID` ni alohida tekshiradi.

### Mijoz buyurtmani bekor qilishi
Tasdiq xabaridan keyin mijozga "❌ Buyurtmani bekor qilish" tugmasi keladi — **5 daqiqa** ishlaydi.
Bekor qilinsa: admin'ga chekka javob sifatida "🚫 Mijoz buyurtma #N ni bekor qildi. Tayyorlamang." keladi,
mijozning Mini App tarixida "Bekor qilindi" bo'ladi, hisobot summasiga kirmaydi.
Muddatni o'zgartirish: `config.py` → `CANCEL_WINDOW_SECONDS`.

### Telefon raqami
Mini App'da "📱 Telegram raqamimni qo'yish" tugmasi bor (Telegram 6.9+). Mijoz ruxsat bersa, raqam o'zi yoziladi.
Raqam mijozning Telegram raqami bilan bir xil bo'lsa, admin chekida "✅ (Telegram raqami)" belgisi chiqadi.

### Buyurtma natijasi Mini App'ga qanday yetadi
- Mini App buyurtmani yuborgach yopiladi va bot unga to'g'ridan-to'g'ri javob bera olmaydi. Shuning uchun
  "Buyurtma berish" tugmasi havolasiga mijozning **eng oxirgi buyurtmasi natijasi** qo'yiladi:
  `?lang=..&ok=<ref>&n=<raqam>` (qabul), `?rej=<ref>&r=<sabab>` (rad), `?cx=<ref>` (bekor qilindi).
- Natija **bazadan** olinadi (`orders` va `last_rejections` jadvallari), havola esa bitta joyda yasaladi
  (`handlers.py` → `build_webapp_url`). Shu sababli klaviatura qayta yuborilganda
  (/start, /til, til tanlash) yoki bot qayta ishga tushganda natija yo'qolmaydi.
- Mini App natijani **indamasdan** qo'llaydi (xabar bot chatida keladi): qabul qilinsa savat tozalanadi
  va "Buyurtmalarim" da "Qabul qilindi #N" bo'ladi; rad etilsa savat saqlanadi. Bir xil natija har
  ochilishda kelsa ham faqat birinchi marta qo'llanadi (`miniapp/result.js`).
- **Mini App ochiq turganda** (Render/webhook rejimida) natija havolasiz ham keladi: "tasdiq kutilmoqda"
  buyurtma bo'lsa Mini App holatni bot serveridan o'zi so'raydi — `GET /api/order-status?ref=...`
  (batafsil: E-11). Havola (`?ok=` / `?rej=` / `?cx=`) zaxira yo'l sifatida qoladi.

### Til (o'zbekcha / ruscha)
- **`/start` har doim avval tilni so'raydi** (til avval saqlangan bo'lsa ham, admin uchun ham):
  "Tilni tanlang / Выберите язык" + "🇺🇿 O'zbekcha" / "🇷🇺 Русский" tugmalari.
  Tanlangach til bazada (`users` jadvali) yangisiga almashtiriladi va shu tilda salomlashuv
  (rasm + matn + "Buyurtma berish" tugmasi) keladi. Til tanlanguncha chatdagi eski Mini App tugmasi ishlayveradi.
- Tili saqlangan mijoz boshqa xabar yozsa yoki tugma bossa, til qayta so'ralmaydi.
  Tili umuman saqlanmagan (eski) mijoz esa istalgan xabarida avval til tanlaydi.
  Istisno: Mini App'dan kelgan buyurtma **hech qachon to'xtatilmaydi** — tili Mini App tilidan
  (yoki Telegram tilidan) olinadi va saqlanadi.
- **`/til`** — tilni istalgan payt almashtirish. Bot yangi tilda qisqa tasdiq va yangi tugmani yuboradi
  (salomlashuv rasmisiz).
- Bot mijozga hamma javobni (buyurtma qabul, rad, bekor qilish, eslatmalar) mijoz tilida yozadi.
  **Admin xabarlari** (chek, /hisobot, /excel, /backup) har doim o'zbekcha.
- Bot matnlari: `i18n.py`. Mini App matnlari: `miniapp/i18n.js`. Ikkalasida uz va ru kalitlari
  bir xil bo'lishi kerak (`tests/test_i18n.py` tekshiradi).
- Mini App tili: tugma havolasidagi `?lang=uz|ru` dan, bo'lmasa Telegram tilidan (`ru` → ruscha, qolgani o'zbekcha).
- Menyu ruschasi `menu.json` da: kategoriyada `name_ru`, taomda `desc_ru`, `items_ru` (kombo tarkibi),
  `variant_ru` (porsiya tugmasi). Taom **nomlari** tarjima qilinmaydi. `*_ru` bo'lmasa o'zbekchasi ko'rinadi.
  Tarjimalarni ko'rib chiqish: `deploy/tarjima_ru_tekshiruv.md`.

### Salomlashuv rasmi
Til tanlangandan keyin rasm yuboriladi: o'zbekcha — `assets/welcome.jpg`, ruscha — `assets/welcome_ru.jpg` (ikkalasi zip ichida bor).
`welcome_ru.jpg` topilmasa, o'zbekcha rasm yuboriladi (konsolda ogohlantirish chiqadi).
Rasmni almashtirish: Files → kerakli rasmni yangisi bilan almashtiring va botni qayta ishga tushiring.
Rasm umuman topilmasa, bot oddiy matnli salomlashuv yuboradi.

### Yetkazish hududi
Joriy lokatsiya faqat Samarqand shahri ichida (markazdan 12 km) qabul qilinadi.
O'zgartirish: `config.py` dagi `DELIVERY_CENTER_LAT`, `DELIVERY_CENTER_LON`, `DELIVERY_RADIUS_KM`
va xuddi shu qiymatlarni `miniapp/app.js` dagi `DELIVERY_AREA` ga yozing (test ikkalasi bir xilligini tekshiradi).

### Muhim: bot faqat BITTA joyda ishlasin
Bot serverda ishlayotganda uni kompyuteringizda ham `python main.py` bilan yoqmang —
Telegram ikki nusxaga xabar bermaydi, buyurtmalar yo'qolishi yoki kechikishi mumkin.
Ayniqsa Render (webhook) ishlab turganda: lokal bot polling uchun webhook'ni **o'chiradi**
va Render'dagi bot Render qayta ishga tushmaguncha xabar olmay qoladi.

### Kodni yangilash
1. **Stop** → yangi `bot_deploy.zip` ni yuklang → **Unarchive** (eski fayllar ustidan yoziladi) → zipni o'chiring → **Start**.
2. Baza (`chicken24.db`) zip ichida yo'q, shuning uchun buyurtmalar saqlanib qoladi.

---

## C. Bazadan zaxira olish (DB backup)

**Avtomatik (bot o'zi):** har kuni **04:00** da (Samarqand vaqti) bot bazaning nusxasini admin chatiga
fayl qilib yuboradi, masalan `chicken24_2026-11-17_0400.db`. Kun davomida yangi buyurtma bo'lmasa — yuborilmaydi.
Bot 04:00 da o'chiq bo'lgan bo'lsa, yoqilganda (oxirgi zaxiradan 24 soat o'tgan bo'lsa) darhol yuboradi.
Istalgan payt: botga **`/backup`** yozing (faqat admin).
Faylda mijozlar ismi, telefoni va manzili bor — boshqalarga yubormang. Oyda bir marta kompyuterga ham saqlang.

**Zaxiradan tiklash:**
1. Admin chatidan oxirgi `.db` faylni yuklab oling.
2. Panelda **Stop** bosing.
3. Files → eski `chicken24.db` (va bor bo'lsa `chicken24.db-wal`, `chicken24.db-shm`) ni o'chiring.
4. Yuklab olingan faylni `chicken24.db` nomi bilan yuklang (yoki `DB_PATH` dagi yo'lga).
5. **Start** bosing.

**Qo'lda (panel orqali):**
1. **Stop** bosing (bot to'xtasa, baza to'liq yoziladi).
2. Files → `chicken24.db` (yoki `DB_PATH` dagi fayl) → **Download**.
   Yonida `chicken24.db-wal` / `chicken24.db-shm` bo'lsa, ularni ham yuklab oling.
3. **Start** bosing.
4. Faylni sanasi bilan saqlang, masalan `chicken24_2026-11-15.db`. Haftada kamida 1 marta qiling.

**SFTP orqali (botni to'xtatmasdan nusxa olish uchun ham qulay):**
1. Panelda **Settings → SFTP Details** dan manzil, port va login'ni oling (parol — panel paroli).
2. WinSCP yoki FileZilla bilan ulaning va `.db` fayl(lar)ni kompyuterga ko'chiring.
   Bot ishlab turgan bo'lsa, `.db`, `.db-wal`, `.db-shm` uchalasini birga ko'chiring.

Zaxirani ko'rish: bepul **DB Browser for SQLite** dasturi bilan oching (`orders`, `order_items` jadvallari).

**Render + Neon (PostgreSQL) rejimida ham** `/backup` va kunlik zaxira xuddi shunday **SQLite `.db` fayl**
yuboradi: bot Postgres'dagi hamma jadvalni bitta izchil holatda o'qib, undan `.db` nusxa yasaydi.
Bu faylni:
- kompyuterdagi lokal botga `bot/chicken24.db` qilib qo'yish mumkin (E-8 «Orqaga qaytish»);
- yoki ko'chirish skripti bilan bo'sh Neon bazasiga qaytarish mumkin:
  `bot\venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py --sqlite yuklangan_fayl.db`.
Telegram 50 MB cheklovi shu rejimda ham tekshiriladi (fayl 49 MB dan katta bo'lsa yuborilmaydi, admin'ga ogohlantirish keladi).

---

## D. Muammo bo'lsa

| Konsoldagi xabar | Sabab / yechim |
|---|---|
| `Quyidagi o'zgaruvchilar ... yo'q` | B-5 qadamdagi o'zgaruvchilar kiritilmagan |
| `BOT_TOKEN noto'g'ri yoki bekor qilingan (Telegram 401)` | Token xato yoki @BotFather'da yangilangan — yangisini kiriting |
| `WEBAPP_URL https:// bilan boshlanishi kerak` | Pages havolasini to'liq, `https://` bilan yozing |
| `Kutilmagan xato, bot 15 soniyadan keyin qayta ishga tushadi` | Odatda internet uzilishi; bot o'zi qayta ulanadi |
| Admin'ga chek kelmayapti | Admin botga `/start` yozmagan yoki `ADMIN_ID` xato. Tuzatilgach, yetmagan cheklar 2 daqiqa ichida (yoki bot qayta yonganda) avtomatik qayta yuboriladi |
| `Pages menyusi olinmadi` | Internet yoki Pages vaqtincha ishlamayapti; bot zaxira `menu.json` dan foydalanadi |
| Tugma bosilganda Mini App ochilmayapti | WEBAPP_URL xato; tuzatgach botni qayta ishga tushiring va `/start` yozing |
| `WEBHOOK_URL berilgan, lekin WEBHOOK_SECRET yo'q` | Render → Environment da `WEBHOOK_SECRET` ni qo'shing (E-4) |
| `WEBHOOK_SECRET 16-256 belgi bo'lsin...` | Sirni E-4 dagi buyruq bilan qayta yarating (faqat harf, raqam, `_`, `-`) |
| `DATABASE_URL postgresql:// ... bilan boshlanishi kerak` | Neon'dan connection string'ni to'liq ko'chiring |
| `PostgreSQL xatosi` / `InvalidPasswordError` | `DATABASE_URL` dagi parol eskirgan — Neon'da parolni yangilab, Render'da almashtiring |
| `Postgres ulanishi uzildi - qayta urinilmoqda` | Neon uxlab qolgandan keyin oddiy holat; bot o'zi qayta ulanadi |
| `Telegram'da webhook o'rnatilgan edi - polling uchun o'chirildi` | Lokal bot ishga tushdi — Render'dagi bot to'xtatilganiga ishonch hosil qiling (E-8) |
| Render: `Port scan timeout` / deploy yiqildi | `WEBHOOK_URL` yoki `WEBHOOK_SECRET` berilmagan (bot polling'da qoldi va port ochmadi) |

---

## E. Bepul server: Render + Neon (24/7, webhook)

**Nima uchun ikkalasi:** Render bepul rejasida disk vaqtinchalik — har qayta ishga tushishda (deploy,
uxlash, Render'ning o'zi qayta yoqishi) `chicken24.db` o'chib ketadi. Shuning uchun baza Neon'da (bepul PostgreSQL).
Render bepul xizmati 15 daqiqa so'rovsiz qolsa uxlaydi — bunga qarshi GitHub Actions har 10 daqiqada `/health` ni chaqiradi.

Loyihaga qo'shilgan fayllar: `render.yaml` (Render sozlamasi), `requirements.txt` va `.python-version` (ildizda),
`.github/workflows/keep-alive.yml`, `tools/migrate_sqlite_to_postgres.py`, `bot/webhook.py`, `bot/logsafe.py`.

### E-1. Neon (baza)
1. https://neon.tech da ro'yxatdan o'ting (karta so'ramaydi) → **New Project**.
2. **Region:** `AWS Europe Central 1 (Frankfurt)` — Render ham Frankfurt'da, kechikish kam.
3. Loyiha ochilgach **Connect** tugmasi → connection string'ni ko'chiring. U shunday ko'rinadi:
   `postgresql://<user>:<parol>@ep-....eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
   Bu — **DATABASE_URL**. Uni hech qayerga (chat, git, skrinshot) qo'ymang; faqat Render paneliga va ko'chirish skriptiga.
   - «Connection pooling» yoqilgan (`-pooler` li manzil) ham, oddiy manzil ham ishlaydi.
   - `sslmode`/`channel_binding` parametrlari bot uchun muammo emas: bot SSL'ni o'zi yoqadi va Neon sertifikatini tekshiradi.
4. Jadvallarni qo'lda yaratish shart emas — bot (yoki ko'chirish skripti) birinchi ulanishda o'zi yaratadi.

### E-2. Eski buyurtmalarni Neon'ga ko'chirish (bir marta)
1. Kompyuterdagi botni **to'xtating** (Ctrl+C), shunda `chicken24.db` o'zgarmaydi.
2. `chicken24` papkasida PowerShell oching va ishga tushiring:
   ```
   bot\venv\Scripts\python.exe tools\migrate_sqlite_to_postgres.py
   ```
   Skript `Neon DATABASE_URL (yozganingiz ko'rinmaydi):` deb so'raydi — E-1 dagi manzilni qo'ying (Enter).
   (Manzil PowerShell tarixiga yozilmaydi; `bot/.env` o'qilmaydi.)
3. Natija — faqat sonlar (telefon/ism chiqmaydi), masalan:
   ```
   jadval            sqlite  qo`shildi  bor edi  postgres
   orders                18         18        0        18
   order_items           59         59        0        59
   ...
   ```
   `sqlite` va `postgres` ustunlari teng bo'lsa — hammasi ko'chdi.
4. Qayta ishga tushirsangiz zarari yo'q: mavjud qatorlar `bor edi` ustuniga tushadi, ikkilanmaydi.
   Agar Neon'da shu raqamli **boshqa** buyurtma bo'lsa, skript hech narsa yozmasdan to'xtaydi (`TO'XTATILDI`).
5. Asl `chicken24.db` ga tegilmaydi (skript vaqtinchalik nusxadan o'qiydi). Uni zaxira sifatida saqlab qo'ying.

### E-3. GitHub repo
Render kodni GitHub'dan oladi. Kompyuterda git o'rnatilgan bo'lsin (https://git-scm.com).
1. GitHub'da yangi repo yarating (masalan `chicken24`). **Public yoki private** — pastdagi «Xavflar»ni o'qing
   (private repo'da keep-alive bepul Actions daqiqalariga sig'maydi).
2. `chicken24` papkasida:
   ```
   git init
   git add .
   git status
   ```
   **`git status` ro'yxatini tekshiring:** unda `bot/.env`, `*.db`, `bot.log`, `venv`, `deploy/bot_deploy.zip`
   **bo'lmasligi** kerak (`.gitignore` ularni chiqarib tashlaydi). Bo'lsa — davom etmang.
3. ```
   git commit -m "Chicken24: Render + Neon"
   git branch -M main
   git remote add origin https://github.com/<login>/chicken24.git
   git push -u origin main
   ```

### E-4. Render (bot)
1. **Webhook siri** yarating (bir marta, kompyuterda):
   ```
   bot\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Chiqqan qator — **WEBHOOK_SECRET** (43 belgi: harf, raqam, `-`, `_`). Uni ham hech kimga bermang.
2. https://render.com → GitHub bilan kiring → **New → Blueprint** → `chicken24` repo'ni tanlang.
   Render `render.yaml` ni o'qiydi: xizmat `chicken24-bot`, **Free**, **Frankfurt**, Python **3.12.10**,
   `pip install -r requirements.txt`, start: `python bot/main.py`, health check: `/health`.
3. Render maxfiy qiymatlarni so'raydi (ular `render.yaml` da yo'q) — shu yerga kiriting:

   | Nomi | Qiymat |
   |---|---|
   | `BOT_TOKEN` | @BotFather bergan token |
   | `ADMIN_ID` | admin Telegram ID (raqam) |
   | `DATABASE_URL` | E-1 dagi Neon manzili |
   | `WEBHOOK_URL` | `https://chicken24-bot.onrender.com` (xizmat manzili, oxirida `/` siz) |
   | `WEBHOOK_SECRET` | E-4.1 dagi sir |

   `WEBAPP_URL` (`https://chicken24.pages.dev`) va `PYTHON_VERSION` yaml'da allaqachon bor. `PORT` ni Render o'zi beradi.
   Keyin o'zgartirish kerak bo'lsa: Render → `chicken24-bot` → **Environment** → qiymat → **Save Changes** (xizmat qayta ishga tushadi).
4. **Apply** → deploy boshlanadi (2-5 daqiqa). Xizmat sahifasining tepasidagi manzilni tekshiring:
   agar u `https://chicken24-bot.onrender.com` dan farq qilsa (nom band bo'lsa Render qo'shimcha qo'shadi),
   **Environment** da `WEBHOOK_URL` ni aynan shu manzilga to'g'rilang.
5. **Logs** da shular chiqishi kerak:
   ```
   Baza ulandi: PostgreSQL (ep-....neon.tech/neondb)
   Rejim: webhook (port 10000)
   Webhook o'rnatildi: https://chicken24-bot.onrender.com/tg/...
   ```

**setWebhook** — qo'lda qilish shart emas: bot har ishga tushganda Telegram'ga webhook manzilini va sirni o'zi
o'rnatadi (`setWebhook`, kutib turgan xabarlar o'chirilmaydi). Webhook manzili `.../tg/<sirdan hisoblangan 40 belgi>` —
tasodifiy ko'rinadi, sirning o'zi manzilda ham, loglarda ham yo'q. Telegram har so'rovda
`X-Telegram-Bot-Api-Secret-Token` sarlavhasida sirni yuboradi; mos kelmasa bot `401` qaytaradi.
Sirni almashtirish: yangi sir yarating → Render Environment'da `WEBHOOK_SECRET` ni almashtiring → bot qayta yonganda yangi manzil o'rnatiladi.

### E-5. Tekshirish
1. Brauzerda `https://chicken24-bot.onrender.com/health` → `{"ok": true}` (bazaga ulanmaydi, tez javob beradi).
2. Telegram'da botga `/start` → til → salomlashuv. Sinov buyurtma bering → admin'ga chek keladi.
3. Admin: `/hisobot` va `/backup` — eski (ko'chirilgan) buyurtmalar ham ko'rinishi kerak.

### E-6. Keep-alive (uxlamasligi uchun)
1. GitHub repo → **Settings → Secrets and variables → Actions → Variables → New repository variable**:
   - Name: `RENDER_HEALTH_URL`
   - Value: `https://chicken24-bot.onrender.com/health`
2. **Actions** → `keep-alive` → **Run workflow** — yashil belgi chiqsa ishlayapti. Jadval: har 5 daqiqa
   (GitHub buni kechiktiradi — pastdagi E-10 ga qarang). Manzil kodda yozilmagan — faqat shu o'zgaruvchida.
   O'zgaruvchi bo'lmasa yoki 3 urinishda ham javob kelmasa, workflow **qizil** bo'ladi.

Uxlamaslikning uch qatlami (hammasi bir vaqtda ishlaydi):
1. **Botning o'zi (asosiy)** — har 8 daqiqada o'zining ochiq manziliga `/health` so'rov yuboradi (`selfping.py`).
   Render manzilni `RENDER_EXTERNAL_URL` orqali o'zi beradi — hech narsa sozlash shart emas. Logda:
   `Ishga tushdi: rejim=webhook, baza=postgres, self-ping=yoqilgan (har 8 daqiqa)`.
2. **UptimeRobot (tavsiya)** — tashqi monitor, har 5 daqiqa (E-10). Bot uxlab qolsa ham uni uyg'otadi.
3. **GitHub Actions** — qo'shimcha zaxira.

### E-7. Kodni yangilash
`git add .` → `git status` (yana `.env`/`.db` yo'qligini tekshiring) → `git commit -m "..."` → `git push`.
Render avtomatik qayta deploy qiladi. Baza Neon'da — buyurtmalar yo'qolmaydi.

### E-8. Orqaga qaytish (kompyuterda polling)
1. Telegram'da admin: **`/backup`** → oxirgi `.db` faylni yuklab oling (Neon'dagi hamma ma'lumot shu faylda).
2. Render → `chicken24-bot` → **Settings → Suspend Service** (bot to'xtaydi; keep-alive uni uyg'otmaydi).
   GitHub → Actions → `keep-alive` → **⋯ → Disable workflow** (ixtiyoriy).
3. Kompyuterda: eski `bot/chicken24.db` (va `-wal`, `-shm`) ni boshqa nom bilan saqlang, yuklangan faylni
   `bot/chicken24.db` qilib qo'ying.
4. `bot/.env` da `DATABASE_URL` va `WEBHOOK_*` **yo'qligiga** ishonch hosil qiling → `python main.py`.
   Logda `Rejim: polling` va bir marta `Telegram'da webhook o'rnatilgan edi - polling uchun o'chirildi` chiqadi —
   bot webhook'ni o'zi o'chiradi (kutib turgan xabarlar saqlanadi). Qo'lda `deleteWebhook` qilish shart emas.
5. Yana Render'ga qaytish: lokal botni to'xtating → E-2 skriptini qayta ishga tushiring (faqat yangi buyurtmalar
   qo'shiladi) → Render → **Resume Service**.

### E-9. Bepul rejaning cheklovlari va xavflar
- **Render 750 soat/oy — butun akkaunt (workspace) uchun umumiy.** Bitta xizmat 24/7 ≈ 720-744 soat.
  Agar shu akkauntda **boshqa bot ham uxlamasdan** ishlasa, ikkalasi limitdan oshadi va Render oy oxirigacha
  **hamma bepul xizmatlarni to'xtatadi**. Chicken24 uchun alohida Render akkaunti yoki boshqa botni uxlatish kerak.
- **GitHub cron ishonchsiz:** tekshiruvda `*/10` jadvali 40 soatda atigi **6 marta** (6-9 soat oraliq bilan) ishlagan —
  shu sabab bot uxlab qolgan. Shuning uchun asosiy himoya endi botning o'zida (self-ping) va UptimeRobot'da (E-10).
  Uxlagan bot birinchi xabarda ~1 daqiqada uyg'onadi (Telegram webhook'ni qayta yuboradi, xabar yo'qolmaydi),
  lekin uxlab yotganda fon ishlari (kunlik zaxira 04:00, yetmagan cheklarni qayta yuborish) bajarilmaydi.
  Repo **60 kun** o'zgarmasa, GitHub jadvalli workflow'ni o'chiradi — Actions'da qayta yoqing.
- **Private repo:** har 10 daqiqada ≈ 4 400 ishga tushish/oy, har biri kamida 1 daqiqa hisoblanadi — bepul
  2 000 daqiqadan oshadi. Private repo kerak bo'lsa, keep-alive uchun tashqi bepul ping xizmatidan
  (masalan cron-job.org, UptimeRobot) `https://.../health` ni chaqiring. Public repo'da kod hammaga ko'rinadi
  (maxfiy ma'lumot yo'q — `.env` git'ga tushmaydi).
- **Neon bepul: oyiga 100 CU-soat** (eng kichik o'lchamda ≈ 400 soat faol ishlash). Baza 5 daqiqa so'rovsiz
  qolsa uxlaydi va soat sarflanmaydi. Shuning uchun bot bo'sh paytda bazaga so'rov yubormaydi (yetmagan cheklarni
  qidirish faqat yangi buyurtma yoki xatodan keyin), `/health` ham bazaga ulanmaydi. Limit tugasa Neon bazani oy
  oxirigacha to'xtatadi — buyurtmalar saqlanmaydi (mijozga xato xabari, savati saqlanadi). Neon panelida
  **Usage** ni oyda bir ko'rib turing.
- **Render istalgan payt qayta ishga tushirishi mumkin** (bepul rejada). Ma'lumot Neon'da, yo'qolmaydi.
  Deploy paytida eski va yangi nusxa bir necha soniya birga ishlashi mumkin — juda kam hollarda admin'ga bitta
  chek ikki marta kelishi mumkin.
- **Zaxira:** yagona tashqi nusxa — admin chatiga keladigan `.db` fayllar. Neon bepul rejasida tiklash tarixi
  qisqa — `/backup` fayllarini vaqti-vaqti bilan kompyuterga ham saqlang.
- **Sir oshkor bo'lsa** (`WEBHOOK_SECRET`) — kimdir botga soxta xabar yuborishi mumkin: sirni darhol almashtiring (E-4).
  **`DATABASE_URL` oshkor bo'lsa** — Neon'da parolni yangilang (Roles → Reset password) va Render'da almashtiring.
- Loglarda mijoz telefoni `+998 ** *** ** 67` ko'rinishida, token `<BOT_TOKEN>`, baza paroli `***` bo'lib chiqadi.

### E-10. Bot uxlab qolsa nima qilish
**Belgisi:** `/start` ga javob ~1 daqiqa kechikib keladi. Render bepul xizmati **15 daqiqa kiruvchi so'rov**
bo'lmasa uxlaydi; birinchi so'rovda ~1 daqiqada uyg'onadi (Telegram xabarni qayta yuboradi — yo'qolmaydi).
Uxlab yotganda fon ishlari (04:00 zaxira, yetmagan cheklarni qayta yuborish) bajarilmaydi.

**1. UptimeRobot monitor qo'shing (bepul, 5 daqiqada 1 marta) — eng ishonchli tashqi himoya:**
1. https://uptimerobot.com → **Register for FREE** (email bilan).
2. **+ New monitor** → **Monitor Type:** `HTTP(s)`.
3. **URL:** `https://chicken24-bot.onrender.com/health` · **Monitoring interval:** `5 minutes`.
4. **Alert contacts:** emailingiz (bot ishlamay qolsa xat keladi) → **Create monitor**.
5. Bir necha daqiqadan keyin monitor yashil **Up** bo'lishi kerak. UptimeRobot `HEAD` so'rov ishlatsa ham `/health` 200 qaytaradi.

**2. Render Logs'da nimaga qarash** (Render → `chicken24-bot` → **Logs**):
| Log qatori | Ma'nosi |
|---|---|
| `Ishga tushdi: rejim=webhook, baza=postgres, self-ping=yoqilgan (har 8 daqiqa)` | Hammasi to'g'ri sozlangan |
| `self-ping=o'chiq` | `RENDER_EXTERNAL_URL` topilmadi — Environment'ga `PUBLIC_URL=https://chicken24-bot.onrender.com` qo'shing |
| `Self-ping muvaffaqiyatsiz (...), ketma-ket N-marta` | Bir-ikki marta — tarmoq; ko'p marta takrorlansa UptimeRobot'ga tayaning |
| `==> Detected service running on port ...` / `Your service is live` | Xizmat (qayta) ishga tushdi |
| Uzoq vaqt hech qanday yangi qator yo'q, keyin ishga tushish qatorlari | Xizmat uxlagan va uyg'ongan |
| `Bot hali tayyor emas - ... (503)` | Ishga tushishda baza (Neon) sekin uyg'ondi; Telegram xabarni keyin qayta yuboradi |

**3. 750 soat limiti:** Render bepul soatlari butun akkaunt (workspace) uchun **oyiga 750 soat**. Uxlamaydigan
bitta xizmat ≈ 720-744 soat sarflaydi — ya'ni shu akkauntda **boshqa uxlamaydigan bepul xizmat bo'lmasligi** kerak
(`sertifikat-backend` kabi ishlatilmaydiganlarini **Suspend** qiling; statik saytlar soat sarflamaydi).
Sarfni ko'rish: Render → workspace **Billing** (Free instance hours). Limit tugasa Render oy oxirigacha
hamma bepul xizmatlarni to'xtatadi.

**4. Qo'lda uyg'otish:** brauzerda `https://chicken24-bot.onrender.com/health` ni oching — 1 daqiqagacha kuting,
`{"ok": true}` chiqsa bot uyg'ondi. Yoki GitHub → Actions → `keep-alive` → **Run workflow**.
Bot umuman javob bermasa: Render → `chicken24-bot` → **Manual Deploy → Restart service** (yoki **Deploy latest commit**).

### E-11. Mini App buyurtma holatini serverdan so'raydi (`/api/order-status`)
**Nima uchun:** buyurtma yuborilgach Mini App yopiladi. Mijoz uni qayta ochsa (masalan, menyu tugmasidan),
bot unga natija yubora olmaydi — avval "bot javobini kutmoqda" yozuvi qolib ketardi. Endi Mini App
"kutilmoqda" buyurtma bo'lsa holatni bot serveridan o'zi so'raydi.

**Qanday ishlaydi:**
- Mini App ochilganda va ekranga qaytganda darhol, keyin **har 4 soniyada**, 2 daqiqadan keyin **har 10 soniyada**,
  jami **15 daqiqagacha** so'raydi (faqat "kutilmoqda" buyurtma bor bo'lsa; javob kelgach to'xtaydi).
- Natija **indamasdan** qo'llanadi (`result.js`): qabul qilinsa savat tozalanadi va "Qabul qilindi · #N";
  rad etilsa savat saqlanadi; bekor qilingan bo'lsa "Bekor qilindi". Toast/banner yo'q.
- Tarmoq xatosi, 429 yoki 503 bo'lsa jim qayta uriniladi — mijozga xato ko'rsatilmaydi.

**Xavfsizlik (server, `bot/orderapi.py`):**
- Mijoz Telegram **initData** bilan aniqlanadi (`Authorization: tma <initData>`): imzo bot tokeni bilan
  tekshiriladi (HMAC-SHA256, constant-time), 24 soatdan eski initData rad etiladi. Foydalanuvchi ID faqat
  initData dan olinadi — har kim faqat **o'z** buyurtmasini ko'radi.
- Javob faqat `{"status": "accepted"|"rejected"|"cancelled"|"unknown", "n": raqam, "reason": sabab kodi}` —
  ism, telefon, manzil yo'q. `unknown` — bot buyurtmani hali olmagan (Mini App kutishda davom etadi).
- CORS faqat `WEBAPP_URL` origin'i (`https://chicken24.pages.dev`) uchun. Har foydalanuvchiga 2 soniyada 1 so'rov.
- Baza ulanmagan / bot hali ishga tushayotgan bo'lsa — `503` (`/health` bunga bog'liq emas).

**Sozlama:** server manzili `miniapp/config.js` dagi `API_BASE` da (hozir `https://chicken24-bot.onrender.com`).
Render manzili o'zgarsa: `config.js` dagi `API_BASE` **va** `miniapp/index.html` dagi CSP `connect-src` ni
yangilang, `?v=` raqamini oshiring va Mini App'ni qayta joylang (A-bo'lim). `API_BASE` bo'sh bo'lsa so'rash
o'chadi va natija avvalgidek faqat bot tugmasi havolasidan keladi. Lokal (polling) rejimda bot serveri yo'q —
shu holda ham havola ishlayveradi.

**Sinash:** Telegram'da buyurtma bering → Mini App yopiladi → bot javobini kutmasdan **darhol** "Buyurtma
berish" tugmasi bilan Mini App'ni qayta oching → savatda "bot javobini kutmoqda" yozuvi ko'rinadi → bir necha
soniyada (bot buyurtmani saqlagach) yozuv yo'qoladi, savat bo'shaydi, "Buyurtmalarim" da "Qabul qilindi · #N".
Mini App'ni yopmasdan boshqa ilovaga o'tib qaytsangiz ham darhol tekshiradi.
