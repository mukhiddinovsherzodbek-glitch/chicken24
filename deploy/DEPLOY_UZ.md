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
5. **Rasmlar keshi:** `miniapp/_headers` fayli bo'yicha taom va kategoriya rasmlari mijoz telefonida **1 kun**
   saqlanadi (Mini App qayta ochilganda tez yuklanadi). Shuning uchun rasmni almashtirsangiz **fayl nomini ham
   o'zgartiring** (masalan `burger_v2.webp`) va `menu.json` dagi `image` yo'lini yangilang — aks holda mijozlar
   eski rasmni 1 kungacha ko'rishi mumkin. Narx, nom va kod (`menu.json`, `app.js`) keshlanmaydi — darhol yangilanadi.
6. `index.html` dagi `?v=N` raqamini har kod o'zgarishida oshiring (hamma joyda bir xil bo'lsin — test tekshiradi).

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
- Yetkazish **masofaga qarab** (restorandan yo'l bo'yicha): 2.5 km gacha 10 000 so'm, keyin har km +2 500 so'm
  (batafsil: H bo'lim). Minimal buyurtma yo'q. Jami = taomlar + yetkazish (bot hisoblaydi).
- To'lov: faqat naqd (kuryerga).
- Ish vaqtini o'zgartirish: `config.py` dagi `OPEN_FROM`, `CLOSE_AT` va xuddi shu qiymatlarni
  `miniapp/app.js` dagi `BUSINESS` ga yozing (test ikkalasi bir xilligini tekshiradi), keyin ikkalasini ham joylang.
  Yetkazish narxi muhit o'zgaruvchilari bilan o'zgaradi (H bo'lim) — Mini App'ni qayta joylash shart emas.
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
Hudud cheklovi **yo'q** — istalgan joyga buyurtma beriladi, narx masofaga qarab (H bo'lim).
Yangi buyurtmada **joriy lokatsiya shart** (uy / qavat / mo'ljal — qo'shimcha izoh maydonida).

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
Render bepul xizmati 15 daqiqa so'rovsiz qolsa uxlaydi — bunga qarshi bot o'ziga har 8 daqiqada so'rov yuboradi
(self-ping), qo'shimcha ravishda UptimeRobot va GitHub Actions `/health` ni chaqiradi (E-6, E-10).

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
   HTTP server ochildi: port 10000 (/health, webhook /tg/...)
   Baza ulandi: PostgreSQL (ep-....neon.tech/neondb)
   Webhook o'rnatildi (/tg/...)
   Ishga tushdi: rejim=webhook, baza=postgres, self-ping=yoqilgan (har 8 daqiqa)
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
  jami **15 daqiqagacha** so'raydi (faqat "kutilmoqda" yoki "yetib bormadi" buyurtma bor bo'lsa; javob kelgach to'xtaydi).
- Natija **indamasdan** qo'llanadi (`result.js`): qabul qilinsa savat tozalanadi va "Qabul qilindi · #N";
  rad etilsa savat saqlanadi; bekor qilingan bo'lsa "Bekor qilindi". Toast/banner yo'q.
- Tarmoq xatosi, 429 yoki 503 bo'lsa jim qayta uriniladi — mijozga xato ko'rsatilmaydi.
- **Qotib qolmaydi:** server **10 daqiqadan ortiq ketma-ket aniq `unknown`** qaytarsa (tarmoq xatosi/503 hisoblanmaydi),
  buyurtma "⚠️ Botga yetib bormadi" holatiga o'tadi: savat saqlanadi, savat ustida
  "Buyurtma botga yetib bormadi. Qayta yuboring." qatori chiqadi va mijoz qayta yubora oladi.
  Agar keyin server shu buyurtma uchun `accepted` qaytarsa (bot baribir olgan) — savat tozalanadi, takroriy
  buyurtma bo'lmaydi.

**Xavfsizlik (server, `bot/orderapi.py`):**
- **Buyurtma belgisi (ref) — sir.** Yangi ref'lar 32 belgi (128 bit, `crypto.getRandomValues`), taxmin qilib bo'lmaydi.
  Holat faqat ref bo'yicha beriladi.
- **initData shart emas.** Pastdagi "Buyurtma berish" (klaviatura) tugmasidan ochilgan Mini App'da Telegram
  initData bermaydi — shuning uchun avvalgi versiya har doim `401` olardi. Endi initData bo'lsa (masalan, menyu
  tugmasidan ochilganda) Mini App uni ixtiyoriy yuboradi; imzosi to'g'ri bo'lsa (HMAC-SHA256, constant-time,
  24 soat) buyurtma shu foydalanuvchiniki ekani ham tekshiriladi, boshqasiniki bo'lsa `unknown`.
  Noto'g'ri initData e'tiborsiz qoldiriladi (rad etilmaydi).
- Javob faqat `{"status": "accepted"|"rejected"|"cancelled"|"unknown", "n": raqam, "reason": sabab kodi}` —
  ism, telefon, manzil, Telegram ID yo'q. `unknown` — bot bu buyurtmani olmagan.
- **So'rov cheklovi:** har ref'ga 2 soniyada 1 ta; IP boshiga daqiqasiga 120 ta; eski **qisqa** ref'lar
  (16 belgidan kam — avvalgi versiyadagi buyurtmalar) uchun IP boshiga **daqiqasiga 30 ta** — taxmin qilib
  topish amalda imkonsiz (429). IP — Cloudflare'ning `CF-Connecting-IP` sarlavhasidan.
- CORS faqat `GET` va faqat `WEBAPP_URL` origin'i (`https://chicken24.pages.dev`) uchun.
- Baza ulanmagan / bot hali ishga tushayotgan bo'lsa — `503` (`/health` bunga bog'liq emas).

**Buyurtma yo'qolmasligi (bot, `handlers.py`):** tartib — bazaga saqlash → mijozga tasdiq → bekor qilish tugmasi →
admin'ga chek. Admin'ga yuborish xato bersa (`ADMIN_ID` noto'g'ri, admin botga `/start` bosmagan, Telegram xatosi)
buyurtma bekor bo'lmaydi: mijoz tasdiqni baribir oladi (keyin "Qayta buyurtma bermang" eslatmasi), xato logga
yoziladi, `notified=0` qoladi va bot uni har 2 daqiqada / qayta ishga tushganda qayta yuboradi. Webhook
yangilanishni fonda qayta ishlaydi — handlerdagi xato Telegram'ga `500` qaytarmaydi (cheksiz qayta urinish yo'q).

**Sozlama:** server manzili `miniapp/config.js` dagi `API_BASE` da (hozir `https://chicken24-bot.onrender.com`).
Render manzili o'zgarsa: `config.js` dagi `API_BASE` **va** `miniapp/index.html` dagi CSP `connect-src` ni
yangilang, `?v=` raqamini oshiring va Mini App'ni qayta joylang (A-bo'lim). `API_BASE` bo'sh bo'lsa so'rash
o'chadi va natija avvalgidek faqat bot tugmasi havolasidan keladi. Lokal (polling) rejimda bot serveri yo'q —
shu holda ham havola ishlayveradi.

**Sinash:** Telegram'da buyurtma bering → Mini App yopiladi → bot javobini kutmasdan **darhol** "Buyurtma
berish" tugmasi bilan Mini App'ni qayta oching → savatda "bot javobini kutmoqda" yozuvi ko'rinadi → bir necha
soniyada (bot buyurtmani saqlagach) yozuv yo'qoladi, savat bo'shaydi, "Buyurtmalarim" da "Qabul qilindi · #N".
Mini App'ni yopmasdan boshqa ilovaga o'tib qaytsangiz ham darhol tekshiradi.
Avvalgi versiyada qotib qolgan eski buyurtmalar ham shu yo'l bilan yechiladi: Mini App'ni oching — bot qabul qilgan
bo'lsa savat bir necha soniyada tozalanadi; olmagan bo'lsa 10 daqiqadan keyin "Botga yetib bormadi" chiqadi.

### E-12. "Buyurtma berish" — inline tugma va HTTP buyurtma (`POST /api/orders`)
**Nima o'zgardi (mijoz ko'radigan):**
- Pastdagi doimiy (reply) klaviatura **olib tashlandi**. "Buyurtma berish / Сделать заказ" endi xabar **ostidagi
  inline tugma**: salomlashuv rasmi ostida, `/til` tasdig'i, buyurtma qabul qilindi / rad etildi / bekor qilindi
  xabarlari ostida. "❌ Buyurtmani bekor qilish" tugmasi alohida xabarda qoladi (5 daqiqa).
- Matn maydoni yonidagi **menyu tugmasi** — buyruqlar ro'yxati (`/start`, `/til`, `/taklif`). Avval u yerda
  "Buyurtma" / "Заказ" (Mini App) bor edi — I bo'limiga qarang.
- **Eski mijozlar:** botga istalgan matn yozsa yoki eski pastki tugma orqali buyurtma bersa — bot pastki
  klaviaturani olib tashlaydi, menyu tugmasini buyruqlar ro'yxatiga almashtiradi va inline tugma yuboradi.
  Klaviatura ham, menyu tugmasi ham har mijoz uchun **bir marta** (chatda qisqa "⌛" ko'rinib-yo'qoladi). Bot qayta ishga tushsa, har mijozda yana bir martadan takrorlanadi.

**Nega HTTP:** inline/menyu tugmasidan ochilgan Mini App'da `sendData()` ishlamaydi, lekin Telegram **initData**
beradi. Shuning uchun Mini App buyurtmani bot serveriga yuboradi: `POST https://chicken24-bot.onrender.com/api/orders`.
- Server initData imzosini tekshiradi (HMAC-SHA256, "WebAppData" + bot tokeni, constant-time, 24 soat).
  **user_id faqat initData dan** — tanadagi user_id e'tiborsiz. initData bo'sh/noto'g'ri — `401`.
- Mantiq `sendData` bilan **bir xil funksiya** (`handlers.process_order`): narxlar menu.json dan, ish vaqti,
  lokatsiya, yetkazish narxi masofadan, 30 soniyalik himoya, admin cheki, mijozga tasdiq + bekor qilish tugmasi.
- **Takror yo'q:** bitta `(user_id, ref)` — bitta buyurtma (bazada unique indeks, SQLite va Postgres). Takror so'rov
  avvalgi natijani qaytaradi va mijozga/admin'ga qayta xabar yubormaydi. (Eski bazada takror bo'lsa indeks
  yaratilmaydi — logda ogohlantirish, bot ishlayveradi.)
- Javob: `{"status": "accepted"|"rejected", "n": raqam, "reason": kod}` — shaxsiy ma'lumotsiz.
- Cheklovlar: tana 20 KB, buyurtma ma'lumoti 4 KB, 30 tagacha taom, har biri 50 tagacha; foydalanuvchiga daqiqasiga
  6 so'rov; noto'g'ri JSON — `400`. CORS: faqat `POST`/`OPTIONS`, faqat `https://chicken24.pages.dev`.

**Mini App'da:**
- Yuborish paytida tugma o'chiq ("Yuborilmoqda…"), kutish 45 soniyagacha (Render uyg'onishi uchun).
- `accepted` — savat tozalanadi, "Buyurtmalarim" ga yoziladi, Mini App **yopiladi** (tasdiq bot chatida).
- `rejected` — Mini App ochiq qoladi, savat saqlanadi, savat ustida qisqa sabab (bot chatiga ham xabar boradi).
- Timeout / tarmoq xatosi — avval `/api/order-status` tekshiriladi: qabul qilingan bo'lsa yopiladi; `unknown`
  bo'lsa "Buyurtma yuborilmadi" va qayta yuborish mumkin; javob umuman bo'lmasa "Javob kelmadi" — qayta yuborish
  **o'sha ref bilan** (server ikkinchi buyurtma yaratmaydi).
- **Zaxira:** initData bo'sh bo'lsa (eski pastki tugmadan ochilgan) — avvalgi `sendData` yo'li, `?ok=` havolasi va
  holat so'rash ishlayveradi.
- **Serverni oldindan uyg'otish:** Mini App ochilishi bilan `/health` ga bitta so'rov yuboradi. Render uxlab qolgan
  bo'lsa, mijoz taom tanlaguncha uyg'onadi va buyurtma kutmasdan o'tadi.
- **Bekor qilingan buyurtma:** mijoz buyurtmani bot chatida bekor qilib, keyin Mini App'ni menyu tugmasidan ochsa,
  Mini App oxirgi "Qabul qilindi" buyurtmani serverdan **bir marta** so'raydi va tarixda "Bekor qilindi" ko'rsatadi.
  Bekor qilish muddati o'tgach (15 daqiqadan keyin) holat yakuniy hisoblanadi va boshqa so'ralmaydi.

**Telegram'da sinash:**
1. `/start` → til → salomlashuv rasmi ostida "Buyurtma berish"; pastki klaviatura yo'qolgan; matn maydoni yonida buyruqlar ro'yxati.
2. Inline tugmadan buyurtma → Mini App yopiladi → bot chatida tasdiq + bekor qilish tugmasi; admin'ga chek.
3. Hududdan tashqari lokatsiya (yoki ish vaqtidan tashqari) → Mini App ochiq, savat ustida sabab, chatda ham xabar.
4. Menyu tugmasi (matn maydoni yonida) bosilganda buyruqlar ro'yxati chiqadi.
5. Eski chat (pastki tugma bor edi) → istalgan matn yozing → pastki tugma yo'qoladi, inline tugma keladi.

**Xavflar:**
- Menyu tugmasi har chat uchun alohida o'rnatiladi va bot eski versiyaga qaytarilsa ham **o'zi o'chmaydi**.
- Render uxlab qolgan bo'lsa birinchi buyurtma 30-60 soniya "Yuborilmoqda…" da turishi mumkin (45 s kutish).
  Javob kelmasa Mini App holatni tekshiradi — takror buyurtma bo'lmaydi.
- `API_BASE` (`miniapp/config.js`) noto'g'ri bo'lsa inline tugmadan buyurtma ishlamaydi (menyu tugmasi ham).

**Orqaga qaytish (rollback):**
1. **Bot:** `git revert <commit>` → `git push` (Render avtomatik qayta joylaydi). Yoki Render → `chicken24-bot` →
   **Events** → oldingi deploy → **Rollback**.
2. **Mini App:** Cloudflare dashboard → Workers & Pages → `chicken24` → **Deployments** → oldingi deploy →
   **⋯ → Rollback to this deployment** (yoki eski kod bilan `npx wrangler pages deploy ...`).
3. Ikkalasini **birga** qaytaring: eski Mini App menyu/inline tugmadan ochilganda `sendData` bilan yubora olmaydi.
4. Eski versiya `/start` da yana pastki klaviaturani yuboradi. Menyu tugmasini hammaga qaytarish uchun
   @BotFather → Bot Settings → Menu Button, yoki shu mijozda `/start` (eski versiya menyu tugmasiga tegmaydi —
   kerak bo'lsa alohida so'rang).

---

## F. Xavfsizlik

### F-1. Bot nimadan himoyalangan
| Xavf | Himoya |
|---|---|
| Narxni o'zgartirib buyurtma berish | Narx va jami summa **faqat** `menu.json` dan hisoblanadi; mijoz yuborgan narx e'tiborsiz |
| Boshqa odam nomidan buyurtma | `POST /api/orders` Telegram **initData** imzosini tekshiradi (HMAC-SHA256, 24 soat); user_id faqat undan |
| Soxta Telegram xabari | Webhook manzili sirdan hisoblanadi + `X-Telegram-Bot-Api-Secret-Token` (mos kelmasa `401`) |
| Begona saytdan so'rov | CORS faqat `WEBAPP_URL` origin'i; Mini App'da qat'iy CSP (skript faqat o'zimizdan va telegram.org) |
| Takroriy buyurtma | `(user_id, ref)` — bazada unique indeks; takror so'rov avvalgi natijani qaytaradi |
| Buyurtma spami | Mijozga 30 soniyada 1 ta buyurtma; API'ga daqiqasiga 6 so'rov |
| Xabar flood'i | Bitta foydalanuvchidan 10 soniyada 15 tadan ortiq xabar/tugma **jim tashlab yuboriladi** (admin va buyurtma mustasno) |
| Admin chatini to'ldirish | Ogohlantirishlar cheklangan: bir xil sabab + mijoz 5 daqiqada 1 marta, jami 10 daqiqada 20 ta. Buyurtma **cheklari cheklanmaydi** |
| Excel faylda formula | Mijoz ism/manzil/izohga `=...` yozsa ham Excel'da oddiy matn bo'lib chiqadi (formula bajarilmaydi) |
| Guruhga qo'shib qo'yish | Bot faqat **shaxsiy chatda** ishlaydi; guruh/kanalda umuman javob bermaydi (admin buyruqlari ham) |
| Ko'rinmas belgilar bilan chalg'itish | Matn yo'nalishini o'zgartiruvchi va nol kenglikdagi belgilar ism/manzil/izohdan olib tashlanadi |
| SQL in'eksiya | Hamma so'rov parametrlangan |
| HTML in'eksiya (chek, xabarlar) | Mijoz matni har doim `html.escape` qilinadi; Mini App'da faqat `textContent` |
| Ma'lumot loglarga tushishi | Telefon `+998 ** *** ** 67`, token `<BOT_TOKEN>`, baza paroli `***`; server versiyasi javoblarda ko'rinmaydi |
| Admin buyruqlarini begona ishlatishi | Har buyruq `ADMIN_ID` ni tekshiradi; begonaga bot umuman javob bermaydi |

Testlar: `bot/tests/test_security.py` (har bir himoya uchun) va qolgan `tests/test_*.py`.

### F-2. Mijozga topshirishdan oldin (bir marta)
1. **@BotFather → `/mybots` → bot → Bot Settings:**
   - **Allow Groups? → Turn off** (botni guruhga qo'shib bo'lmaydi);
   - **Group Privacy → Turn on** (odatda yoqiq).
2. **`ADMIN_ID`** — restoran egasi (yoki operator) Telegram ID si bo'lsin. U botga `/start` yozgan bo'lishi shart.
   Render → Environment da almashtiriladi.
3. **Sirlarni yangilang** (ishlab chiqish paytida ular sizning kompyuteringizdan o'tgan):
   - `BOT_TOKEN`: @BotFather → bot → **API Token → Revoke current token** → yangisini Render'ga yozing;
   - `WEBHOOK_SECRET`: E-4.1 dagi buyruq bilan yangisini yarating → Render'da almashtiring;
   - `DATABASE_URL`: Neon → **Roles → Reset password** → yangi manzilni Render'ga yozing.
   Har almashtirishdan keyin Render xizmatni o'zi qayta ishga tushiradi; `/health` va sinov buyurtma bilan tekshiring.
4. **Kompyuteringizdagi nusxalarni tozalang:** `bot/.env` (eski token), `bot/chicken24.db` va `bot.log`
   (mijozlar ismi, telefoni, manzili), admin chatingizdagi `.db` zaxira fayllari.
5. **Akkauntlar kimniki:** Render, Neon, Cloudflare, GitHub — restoran egasining emailida bo'lgani ma'qul
   (yoki unga kirish bering). Aks holda bot ishlashi sizning akkauntingizga bog'liq bo'lib qoladi.
6. **Ikki bosqichli tasdiqlash (2FA)** — shu to'rt akkauntda va admin'ning Telegram akkauntida yoqilsin:
   kimdir admin Telegram'iga kirsa, hamma mijozlar ma'lumotini (`/excel`, `/backup`) yuklab oladi.

### F-3. Doimiy qoidalar
- Token, `DATABASE_URL`, `WEBHOOK_SECRET` ni chatga, skrinshotga, git'ga qo'ymang. Oshkor bo'lsa — F-2.3 bo'yicha darhol almashtiring.
- `/excel` va `/backup` fayllarida mijozlar ma'lumoti bor — boshqalarga yubormang, keraksizlarini o'chiring.
- GitHub repo **public** bo'lsa kod hammaga ko'rinadi (unda sir yo'q — git tarixi tekshirilgan). UptimeRobot ulangach
  repo'ni **private** qilib, `keep-alive` workflow'ini o'chirish mumkin (private repo'da u bepul daqiqalardan oshadi).
- Kodni o'zgartirgach `git push` dan **oldin** testlarni o'tkazing — Render `main` ga tushgan har o'zgarishni darhol joylaydi:
  ```
  cd bot
  Get-ChildItem tests\test_*.py | ForEach-Object { .\venv\Scripts\python.exe $_.FullName | Select-Object -Last 1 }
  ```

---

## G. Buyurtmalar guruhi (cheklar xodimlar guruhiga)

Sozlansa, buyurtma cheklari (yangi buyurtma, lokatsiya, "mijoz bekor qildi") admin'ning shaxsiy chatiga emas,
**Telegram guruhiga** keladi — oshxona/kuryerlar birga ko'radi. Admin buyruqlari (`/excel`, `/hisobot`, `/backup`)
baribir **faqat admin'ning bot bilan shaxsiy chatida** ishlaydi.

> ⚠️ **Guruh a'zolari mijozlarning ismi, telefoni va manzilini ko'radi.** Guruhga faqat ishonchli xodimlarni qo'shing,
> guruhni yopiq (private) qiling, taklif havolasini tarqatmang. Xodim ishdan ketsa — guruhdan chiqaring.

### G-1. Sozlash
1. Telegram'da yangi guruh oching (masalan, "Chicken24 buyurtmalar") va xodimlarni qo'shing.
2. Botni guruhga **admin akkauntidan (ADMIN_ID)** qo'shing. Boshqa odam qo'shsa, bot darhol chiqib ketadi.
3. Guruhda admin akkauntidan yozing: `/guruh_id` → bot guruh ID sini yuboradi (manfiy son, masalan `-1001234567890`).
   Bu buyruq faqat guruhda va faqat admin uchun ishlaydi; boshqalarga bot javob bermaydi.
4. Render → `chicken24-bot` → **Environment** → **Add Environment Variable**:
   - `ORDERS_CHAT_ID` = 3-qadamdagi son (minus belgisi bilan)
   - (ixtiyoriy) `ORDERS_ALSO_ADMIN` = `1` — chek guruhdan tashqari admin'ga ham alohida kelsin
   → **Save Changes** (bot qayta ishga tushadi, 1-3 daqiqa).
5. Logda `Ishga tushdi: ...` qatori chiqqach sinang (G-2).

### G-2. Sinash
1. Mini App'dan sinov buyurtma bering → chek va lokatsiya **guruhga** keladi (admin'ga emas).
2. 5 daqiqa ichida "Bekor qilish" ni bosing → guruhda chekka javob: "Mijoz buyurtma #N ni bekor qildi".
3. Guruhda `/excel`, `/hisobot`, `/backup`, `/start` yozing (admin akkauntidan ham) → bot **javob bermaydi**.
   Shaxsiy chatda admin uchun `/hisobot` ishlaydi.
4. Boshqa akkaunt botni boshqa guruhga qo'shsa → bot o'zi chiqib ketadi.

### G-3. Muammo bo'lsa
- **Chek admin'ga keldi va "⚠️ Guruhga yuborib bo'lmadi" yozuvi** — bot guruhdan chiqarilgan yoki yozish huquqi yo'q,
  yoki `ORDERS_CHAT_ID` noto'g'ri. Buyurtma yo'qolmaydi (shaxsiy chatga zaxira sifatida keladi). Botni guruhga
  admin akkauntidan qayta qo'shing, `/guruh_id` bilan ID ni tekshiring.
- **"Guruh supergruppaga ko'chgan. Yangi ID: ..."** — Telegram guruhni supergruppaga aylantirganda ID o'zgaradi.
  Xabardagi yangi ID ni Render'da `ORDERS_CHAT_ID` ga yozing.
- Guruhga ham, admin'ga ham yetmasa — buyurtma bazada `notified=0` qoladi va har 2 daqiqada (yoki bot qayta
  yonganda) qayta yuboriladi.
- Admin botni **boshqa** guruhga qo'shsa (ORDERS_CHAT_ID boshqa) — bot chiqib ketadi va shaxsiy chatda o'sha
  guruh ID sini yozadi (shu guruhni ishlatmoqchi bo'lsangiz — ORDERS_CHAT_ID ni almashtiring).
- Loglarda chat ID lar yashirilgan: `-100*******890`.

### G-4. Orqaga qaytish
Render → Environment → `ORDERS_CHAT_ID` ni **o'chiring** → Save Changes. Cheklar yana faqat admin'ning shaxsiy
chatiga keladi (kod o'zgarmaydi). Guruhga yuborilgan eski cheklarni bekor qilish xabari baribir o'sha guruhga boradi
(guruh ishlamasa — admin'ga). Botni guruhdan chiqarib yuborishingiz mumkin.

---

## H. Yetkazish narxi masofaga qarab

**Qoida:** restorandan mijoz lokatsiyasigacha **avtomobil yo'li** masofasi (0.1 km gacha yaxlitlanadi).
| Masofa | Yetkazish |
|---|---|
| 0 – 2.5 km | 10 000 so'm |
| 2.6 – 3.5 km | 12 500 so'm |
| 3.6 – 4.5 km | 15 000 so'm |
| 4.6 – 5.5 km | 17 500 so'm |
| ... | har km uchun +2 500 so'm (maksimal chegara yo'q) |

Narx **faqat serverda** hisoblanadi (Mini App faqat ko'rsatadi; mijoz yuborgan summa e'tiborsiz). Lokatsiya aniq
bo'lgach savatda "Yetkazish (3.4 km): 12 500 so'm" va jami ko'rinadi; narx olinmasa — "10 000 so'mdan, masofaga
qarab", yakuniy summani bot hisoblaydi va tasdiq xabarida ko'rsatadi. Admin chekida masofa ham bor; taxminiy
bo'lsa "(taxminiy masofa)". `/excel` da "Masofa (km)" ustuni.

**Masofa manbai:** [OpenRouteService](https://openrouteservice.org) (bepul rejada kunlik limit bor). Kalit bo'lmasa,
limit tugasa yoki xizmat javob bermasa — **taxminiy**: to'g'ri chiziq × `ROAD_FACTOR` (buyurtma hech qachon to'xtamaydi).
Bir xil lokatsiya 24 soat keshlanadi; bot kuniga 1800 tadan ortiq so'rov yubormaydi.

### H-1. Render'da sozlash (Render → `chicken24-bot` → Environment → Add Environment Variable)
| Nomi | Majburiy | Qiymat |
|---|---|---|
| `RESTAURANT_LAT` | **ha** | Restoran kengligi, masalan `39.654812` |
| `RESTAURANT_LON` | **ha** | Restoran uzunligi, masalan `66.975733` |
| `ORS_API_KEY` | tavsiya | OpenRouteService kaliti (maxfiy!) — bo'lmasa masofa taxminiy |
| `DELIVERY_BASE_FEE` | yo'q | Boshlang'ich narx, standart `10000` |
| `DELIVERY_BASE_KM` | yo'q | Boshlang'ich narxdagi km, standart `2` |
| `DELIVERY_EXTRA_PER_KM` | yo'q | Har qo'shimcha km, standart `2500` |
| `ROAD_FACTOR` | yo'q | Taxminiy masofa koeffitsienti, standart `1.3` |

→ **Save Changes** (bot qayta ishga tushadi). `RESTAURANT_LAT/LON` berilmasa masofa VAQTINCHA Samarqand markazidan
o'lchanadi va logda ogohlantirish chiqadi: `RESTAURANT_LAT / RESTAURANT_LON berilmagan ...`.

**Restoran koordinatasini olish:** Google Maps'da restoran ustiga bosing → pastda chiqqan `39.65..., 66.97...`
raqamlarini nusxalang (birinchisi LAT, ikkinchisi LON).

**ORS kalitini olish:** https://openrouteservice.org saytida bepul ro'yxatdan o'ting (sayt hozir HeiGIT akkaunt
sahifasiga yo'naltiradi) va shaxsiy kabinetda API kalit (token) yarating → Render'da `ORS_API_KEY` ga qo'ying.
Kabinetda bepul rejaning kunlik limitini ko'ring: bot o'zi kuniga 1800 tadan ortiq so'rov yubormaydi (limit
kichikroq bo'lsa, yetishmagan so'rovlar uchun masofa taxminiy hisoblanadi). Kalitni chatga, git'ga yoki Mini App'ga
yozmang — u faqat serverda ishlatiladi, loglarga chiqmaydi.
Bot ishlatadigan manzil: `https://api.heigit.org/openrouteservice/v2/directions/driving-car/json`
(`bot/delivery.py` → `ORS_DIRECTIONS_URL`).

Logda tekshiring: `Yetkazish: 10000 so'm (2 km gacha) + 2500 so'm/km; masofa: OpenRouteService (yo'l)`.
Kalit bo'lmasa: `masofa: taxminiy (to'g'ri chiziq x 1.3) - ORS_API_KEY berilmagan`.

### H-2. Sinash
1. Telegram'da inline "Buyurtma berish" → savat → **📍 Joriy manzilni yuborish** → bir soniyada
   "Yetkazish (X.X km): ... so'm" va "Jami" chiqadi.
2. Buyurtma bering → bot tasdiq xabarida "🚚 Yetkazish (X.X km): ..." ; chekda masofa (guruh/admin).
3. Restorandan uzoqroq (masalan 5 km) nuqtadan sinab, narx jadvaldagidek oshishini tekshiring.
4. `/excel` → "Masofa (km)" ustuni.
5. ORS kalitisiz (o'chirib qo'ysangiz) chekda "(taxminiy masofa)" chiqadi — buyurtma baribir qabul qilinadi.

### H-3. Orqaga qaytish
Narxni avvalgidek qat'iy 10 000 so'm qilish kerak bo'lsa: `DELIVERY_EXTRA_PER_KM` = `0` (masofa qancha bo'lsa ham
10 000 so'm). Kodni to'liq qaytarish: `git revert <commit>` → `git push` va Mini App'ni oldingi deploy'ga qaytarish
(Cloudflare → Deployments → Rollback) — ikkalasini birga.

## I. "Taklif va shikoyat" va guruh chekidagi @mention

- Salomlashuv rasmi ostida "Buyurtma berish" yonida **Taklif va shikoyat** (ruschada "Предложения и жалобы")
  tugmasi bor — `https://t.me/Chicken24_uz` ni ochadi. `/taklif` buyrug'i ham shu tugmani chiqaradi.
- Guruhga (`ORDERS_CHAT_ID`) keladigan har bir **yangi** chekning eng oxirgi qatori `@Chicken24_uz` — Telegram shu
  akkauntni belgilaydi (bildirishnoma boradi). Bekor qilish xabarida va admin'ning shaxsiy nusxasida yo'q.
- Boshqa akkaunt kerak bo'lsa: Render → `chicken24-bot` → Environment → `FEEDBACK_USERNAME` = `YangiUsername`
  (`@` bilan yoki `@` siz; 5–32 belgi: harf, raqam, `_`). Berilmasa — `Chicken24_uz`. Saqlang → Render qayta ishga
  tushiradi. Noto'g'ri qiymatda bot ishga tushmaydi va logda `FEEDBACK_USERNAME` xatosi chiqadi.
- Mention bildirishnomasi borishi uchun o'sha akkaunt guruh a'zosi bo'lishi kerak.
- Matn maydoni yonidagi tugma endi **buyruqlar ro'yxati** ("Buyurtma" Mini App tugmasi olib tashlandi; buyurtma —
  xabar ostidagi inline "Buyurtma berish"dan). Bot ishga tushganda standart tugma buyruqlar ro'yxatiga qo'yiladi
  (@BotFather'dagi Menu Button sozlamasi ham shu bilan almashtiriladi). Avval "Buyurtma" o'rnatilgan eski
  mijozlarda u keyingi `/start` → til tanlashda (yoki `/til`, yoki botga istalgan matn yozganda) almashadi.
