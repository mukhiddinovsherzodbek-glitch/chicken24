# Bazani Neon'dan Supabase'ga ko'chirish — qadam-baqadam yo'riqnoma

Bu yo'riqnoma loyiha egasi uchun. Ko'chirishning o'zi **restoran yopiq paytda (03:00–10:00)** bajariladi va
taxminan 20–30 daqiqa oladi. Tayyorgarlikni (A bo'lim) istalgan kuni, kunduzi qilsa bo'ladi.

**Qisqacha:** bot vaqtincha to'xtatiladi → Neon'dagi hamma ma'lumot Supabase'ga ko'chiriladi va tekshiriladi →
Render'da `DATABASE_URL` Supabase manziliga almashtiriladi → bot yoqiladi. Neon **14 kun o'chirilmaydi** —
muammo bo'lsa unga qaytish mumkin.

> ⚠️ Baza manzillarida (`DATABASE_URL`) parol bor. Ularni chatga, skrinshotga, git'ga, Word/Telegram'ga
> yozmang. Faqat parol menejeri, Render paneli, `bot/.env.test` fayli va pastdagi yashirin kiritish.

---

## A. Tayyorgarlik (istalgan kuni)

### A-1. Supabase sozlamalarini tekshirish
Ikkala loyihada (`chicken24` — asosiy, `chicken24-test` — testlar uchun):
1. **Region:** Central EU (Frankfurt).
2. **Data API o'chiq:** Project Settings → **Data API** → «Enable Data API» o'chirilgan bo'lsin.
   (Shunda bazaga internetdan REST API orqali kirib bo'lmaydi. Qo'shimcha himoya: bot har jadvalda
   Row Level Security'ni o'zi yoqadi.)
3. **Parol kuchli:** Database Settings → parolni «Generate» bilan yarating (harf va raqamlardan iborat bo'lsin —
   `@ : / ? # %` belgilari manzilni buzadi). Parol menejerida saqlang.
4. **Ulanish manzili:** yuqoridagi **Connect** → **Session pooler** → URI ni oling. U shunday ko'rinadi:
   `postgresql://postgres.<ref>:<parol>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres`
   (Supabase'da `<parol>` o'rnida `[YOUR-PASSWORD]` yozilgan bo'ladi — **qavslar bilan birga** almashtiring:
   `...:MeningParolim@...`, `...:[MeningParolim]@...` emas. Xost `aws-0-...` yoki `aws-1-...` bo'lishi
   mumkin — Supabase qanday bersa, shunday qoldiring.)
   - Port **5432** (Session pooler) bo'lsin, 6543 (Transaction pooler) emas.
   - «Direct connection» (`db.<ref>.supabase.co`) ishlatmang: bepul rejada u faqat IPv6, Render'dan ulanmaydi.
   - Manzil oxiridagi parametrlarni (bo'lsa `?sslmode=require` ni ham) **shunga almashtiring**:
     `?sslmode=verify-full&sslrootcert=certs/supabase-ca.crt`
     Faqat `sslmode=require` bo'lsa, bot ulanmaydi: `self-signed certificate in certificate chain`
     (2026-11-26 da test bazasida tekshirilgan).

### A-2. SSL sertifikatini yuklab olish
Supabase server sertifikatini o'z CA'si bilan imzolaydi (umumiy CA'lar ro'yxatida yo'q). Bot uni shu fayl
bilan tekshiradi, aks holda ulanishda `SSLCertVerificationError` chiqadi.
1. Supabase → loyiha → **Database Settings → SSL Configuration → Download Certificate**.
2. Faylni `C:\chicken24\bot\certs\supabase-ca.crt` nomi bilan saqlang. Windows kengaytmani yashirishi mumkin —
   nomi `supabase-ca.crt.crt` bo'lib qolmasin (Explorer → View → «File name extensions» ni yoqib tekshiring).
3. Bu ochiq sertifikat (parol yoki kalit emas) — git'ga qo'shiladi (A-4). Render serveri ham faylni repo'dan
   oladi: `sslrootcert=certs/supabase-ca.crt` yo'li `bot/` papkasiga nisbatan.
4. **Ikkala loyiha uchun bitta fayl:** ikkala loyiha bir xil pooler xostida bo'lsa (masalan
   `aws-1-eu-central-1.pooler.supabase.com`), server bitta sertifikat ko'rsatadi, chunki TLS loyiha
   aniqlanishidan oldin o'rnatiladi. Aniq bilish uchun ikkala loyihadan yuklangan faylning SHA-256 sini
   solishtiring (mazmuni ko'rinmaydi, faqat xesh):
   ```powershell
   Get-FileHash C:\chicken24\bot\certs\supabase-ca.crt, $HOME\Downloads\prod-ca-2021.crt
   ```
   Xeshlar bir xil bo'lsa — bitta fayl yetadi. Yakuniy isbot — A-5 dry-run: u asosiy loyihaga shu fayl bilan
   ulanadi; mos kelmasa `SSLCertVerificationError` chiqadi (hech narsa yozilmaydi).

### A-3. Testlarni Supabase test bazasida o'tkazish
1. `C:\chicken24\bot\.env.test` faylini yarating (Notepad, UTF-8). Ichida **bitta qator**, `chicken24-test`
   loyihasining manzili bilan (asosiy loyiha EMAS):
   ```
   TEST_DATABASE_URL=postgresql://postgres.<test-ref>:<parol>@aws-1-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=verify-full&sslrootcert=certs/supabase-ca.crt
   ```
   (Xost — Supabase bergani: `aws-0-...` yoki `aws-1-...`.)
   Fayl git'ga tushmaydi (`.gitignore` → `.env.*`). Testlar faqat vaqtinchalik `c24test_*` sxemalarda
   ishlaydi va oxirida ularni o'chiradi — `public` ga tegmaydi.
2. Testlarni ishga tushiring (README.md → «Testlar» dagi buyruq):
   ```powershell
   cd C:\chicken24\bot
   Get-ChildItem tests\test_*.py | ForEach-Object { .\venv\Scripts\python.exe $_.FullName | Select-Object -Last 1 }
   ```
   `test_postgres.py` va `test_migrate_pg.py` da **SKIP yo'q** va hammasi `N/N test o'tdi` bo'lishi kerak.

### A-4. Yangi kodni ishchi botga qo'shish (hali Neon bilan)
Ishchi bot (`main`) Supabase'ga ulanishi uchun `bot/db.py` ning yangi versiyasi (sslrootcert, RLS) va
sertifikat fayli `main` da bo'lishi kerak. Claude Code'ga ayting: **«Supabase'ga tayyor commitni va
sertifikatni main'ga qo'sh»** — u testlarni o'tkazib, `main` ga qo'shadi, `git push` ni esa siz tasdiqlaysiz.
Push'dan keyin Render bir necha daqiqada yangi kodni joylaydi. Render → **Logs** da (bir marta):
```
Row Level Security yoqildi (barcha jadvallar)
Ishga tushdi: rejim=webhook, baza=postgres
```
Telegram'da `/hisobot` ishlashini tekshiring. Bot hali Neon'da — mijozlar hech narsani sezmaydi.

### A-5. Sinov (dry-run) — hech narsa yozmaydi
1. PowerShell oching va manzillarni **yashirin** kiriting (yozganingiz ekranda ham, PowerShell tarixida ham
   qolmaydi). Har qatorni alohida bajaring, so'ralganda manzilni joylang (Ctrl+V) va Enter bosing:
   ```powershell
   cd C:\chicken24
   ```
   ```powershell
   $env:SOURCE_DATABASE_URL = [System.Net.NetworkCredential]::new('', (Read-Host 'Neon manzili' -AsSecureString)).Password
   ```
   ```powershell
   $env:TARGET_DATABASE_URL = [System.Net.NetworkCredential]::new('', (Read-Host 'Supabase manzili' -AsSecureString)).Password
   ```
   - Neon manzili — Render → `chicken24-bot` → Environment → `DATABASE_URL` dagi qiymat (hozirgi).
   - Supabase manzili — A-1.4 dagi manzil (`sslrootcert=...` bilan).
2. Sinov:
   ```powershell
   bot\venv\Scripts\python.exe tools\migrate_pg_to_pg.py
   ```
3. Natija — faqat sonlar (ism, telefon, manzil, parol chiqmaydi):
   ```
   Yo'nalish: neon.tech -> supabase.com
   jadval               manba  maqsad (hozir)
   orders                 412     jadval yo'q
   order_items           1180     jadval yo'q
   ...
   orders.total yig'indisi (manba): 41250000
   Sinov (dry-run): hech narsa yozilmadi. Ko'chirish uchun: --apply
   ```
   Tekshiring: yo'nalish aynan **neon.tech -> supabase.com**, maqsadda «jadval yo'q» yoki `0`.
4. Oynani yoping (manzillar shu oyna bilan birga o'chadi).

---

## B. Ko'chirish (restoran yopiq: 03:00–10:00)

1. **Zaxira:** Telegram'da botga `/backup` → kelgan `.db` faylni kompyuterga saqlang.
2. **UptimeRobot:** monitor `chicken24-bot` → **Pause** (aks holda bot to'xtaganda «Down» xatlari keladi).
3. **Botni to'xtatish:** Render → `chicken24-bot` → **Settings → Suspend Service**.
   Nima uchun: ko'chirish paytida bot Neon'ga yangi yozuv (til, telefon, rad etilgan buyurtma) qo'shsa, u
   Supabase'ga o'tmay qoladi. Telegram xabarlari bu vaqtda yo'qolmaydi — bot yoqilgach keladi.
4. **Ko'chirish:** A-5.1 dagidek manzillarni yashirin kiriting, keyin:
   ```powershell
   bot\venv\Scripts\python.exe tools\migrate_pg_to_pg.py --apply
   ```
   Skript yo'nalish va sonlarni ko'rsatadi va so'raydi: `Ko'chirish uchun KOCHIR deb yozing`.
   Yo'nalish **neon.tech -> supabase.com** ekanini tekshiring va katta harflar bilan `KOCHIR` deb yozing.
   Boshqa har qanday javob — bekor, hech narsa yozilmaydi.
5. **Natija:** har jadval qatorida `OK`, `orders.total yig'indisi: manba X, maqsad X  OK` va oxirida
   `Tayyor: ko'chirildi va tekshirildi.` Agar `DIQQAT: manba ... o'zgargan` chiqsa — bot to'xtamagan:
   Supabase'dagi hamma jadvalni o'chirib (Claude Code yordam beradi) qayta ko'chirish kerak.
   Xato bo'lsa — hammasi bekor qilinadi (tranzaksiya), C-1 ga qarang.
6. **Render'da manzilni almashtirish:** Render → `chicken24-bot` → **Environment** → `DATABASE_URL` →
   **Edit** → asosiy (`chicken24`) loyihaning Session pooler manzili, oxiri aynan shunday:
   ```
   postgresql://postgres.<ref>:<parol>@aws-1-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=verify-full&sslrootcert=certs/supabase-ca.crt
   ```
   → **Save Changes**. `certs/supabase-ca.crt` — Render'dagi kod ichidagi fayl (`bot/certs/`), shuning uchun
   A-4 (sertifikat `main` da) bajarilgan bo'lishi **shart**. Aks holda bot ishga tushmaydi va Logs'da
   `sslrootcert fayli topilmadi: supabase-ca.crt` chiqadi (C-2 bo'yicha qayting).
   Eski Neon manzilini parol menejerida saqlab qo'ying (qaytish uchun kerak).
7. **Botni yoqish:** Render → **Resume Service**. **Logs** da kuting:
   ```
   Baza ulandi: PostgreSQL (aws-0-eu-central-1.pooler.supabase.com/postgres)
   Webhook o'rnatildi (/tg/...)
   Ishga tushdi: rejim=webhook, baza=postgres
   ```
8. **UptimeRobot:** monitor → **Resume** (yashil «Up» bo'lishi kerak).
9. PowerShell oynasini yoping.

### B-1. Tekshirish (ko'chirishdan darhol keyin)
1. Brauzerda `https://chicken24-bot.onrender.com/health` → `{"ok": true}`.
2. Telegram: `/start` → til → salomlashuv keladi.
3. `/hisobot` — raqamlar ko'chirishdan oldingi bilan **bir xil** (bugun / hafta / oy).
4. `/backup` — fayl keladi, «Buyurtmalar: N ta» soni dry-run'dagi `orders` soniga teng.
5. **Test buyurtma:** Mini App'dan buyurtma bering → tasdiq va chek keladi, raqami (#N) avvalgilardan katta →
   5 daqiqa ichida «Bekor qilish» ni bosing (bekor qilingan buyurtma hisobotga kirmaydi).
6. Restoran ochilgandan keyingi birinchi soatda haqiqiy buyurtmalar kelayotganini kuzating.

---

## C. Muammo bo'lsa — Neon'ga qaytish

### C-1. Ko'chirish xato bilan tugadi (B-4/B-5)
Maqsadga ma'lumot yozilmagan (hammasi bitta tranzaksiyada). Render'da `DATABASE_URL` ni o'zgartirmang →
**Resume Service** → UptimeRobot **Resume**. Bot Neon bilan avvalgidek ishlaydi. Xato matnini (unda parol
yo'q) Claude Code'ga ko'rsating.
- `Ulanib bo'lmadi: SSLCertVerificationError` — manzilda `sslrootcert=certs/supabase-ca.crt` yo'q
  (faqat `sslmode=require`), fayl nomi noto'g'ri (`.crt.crt`) yoki boshqa loyihaning sertifikati (A-2).
- `RAD ETILDI ... maqsad bo'sh emas` — Supabase'da allaqachon ma'lumot bor; tekshirmasdan o'chirmang.

### C-2. Ko'chirildi, lekin bot Supabase bilan ishlamayapti
1. Render → **Suspend Service**.
2. **Environment** → `DATABASE_URL` → eski **Neon** manzili → **Save Changes**.
3. **Resume Service** → Logs'da `Baza ulandi: PostgreSQL (ep-....neon.tech/...)`.
4. Diqqat: Supabase'ga ulangan vaqtda kelgan buyurtmalar Neon'da **yo'q**. Qaytishdan oldin admin chatidagi
   cheklarni ko'rib chiqing (yoki `/excel_bugun` oling). Iloji boricha birinchi soat ichida qaror qiling.

---

## D. Keyin

- **Neon 14 kun o'chirilmaydi.** Ko'chirish sanasiga 14 kun qo'shib yozib qo'ying. Shu muddatda hammasi
  joyida bo'lsa — Neon loyihasini o'chiring (Neon → Project settings → Delete). Undan oldin oxirgi `/backup`.
- **Zaxira:** Supabase bepul rejasida avtomatik zaxira yo'q. Kunlik `/backup` fayllari (admin chatida) —
  yagona nusxa; oyda bir marta kompyuterga ham saqlang. Kunlik zaxira vazifasi bazaga har kuni murojaat
  qiladi — bu bazani faol ushlab turadi (bepul loyiha uzoq faolsiz qolsa to'xtatiladi).
- **Parollar:** Supabase parolini almashtirsangiz (Database Settings → Reset password) — Render'dagi
  `DATABASE_URL` ni ham, `bot/.env.test` ni ham yangilang.

---

## E. Render Starter va `render.yaml` (Blueprint)

**Savol:** `render.yaml` dagi `plan` o'zgarishi Blueprint sync orqali Render rejasini o'zi o'zgartirib
yuborishi mumkinmi? **Ha, mumkin edi** — shuning uchun `render.yaml` da `plan` umuman yo'q.

Render hujjatlari bo'yicha (render.com/docs/infrastructure-as-code va /docs/blueprint-spec, 2026-11-26):
- Blueprint **Auto Sync standart yoqilgan**: `render.yaml` o'zgargan har commit Blueprint ulangan branch'ga
  (bizda `main`) tushsa, Render yangi sozlamani mavjud servisga qo'llaydi va qayta joylaydi.
- Panelda qo'lda qilingan o'zgarish `render.yaml` bilan zid bo'lsa, **keyingi sync uni yaml qiymati bilan
  ustidan yozadi**. Ya'ni yaml'da `plan: free` qolib, panelda Starter tanlansa — keyingi sync Free'ga qaytarardi.
- `plan` yozilmasa — «Render mavjud servisning joriy rejasini saqlaydi». Yangi servis yaratilsa — standart
  `0.5c-512mb` (bu Starter, $7/oy).
- Hujjatdagi rejalar ro'yxatida `starter` degan qiymat yo'q (`free`, `0.5c-512mb`, `1c-2g`, ...). Shuning
  uchun SPEC'dagi `plan: starter` o'rniga `plan` butunlay olib tashlandi (egasi qarori).
- `kurer` branch'idagi commitlar Render'ga hech narsa qilmaydi — sync faqat ulangan branch'dan (`main`).
- Hujjatlarda yozilmagan: to'lov kartasi yo'q akkauntda pullik reja qo'llansa nima bo'lishi.

**Tartib:**
1. Render → `chicken24-bot` → **Settings → Instance Type → Starter** (karta kerak) → saqlang.
   Logs'da xizmat qayta ishga tushadi.
2. Shundan keyingina Claude Code'ga ayting: **«Render Starter commitini main'ga qo'sh»** (self-ping va
   keep-alive olib tashlanadi; `render.yaml` da `plan` yo'qligi — rejaga tegmaydi). Push'ni siz tasdiqlaysiz.
3. Logs'da: `Ishga tushdi: rejim=webhook, baza=postgres` (self-ping so'zi yo'q).
4. GitHub → Settings → Secrets and variables → Actions → Variables → `RENDER_HEALTH_URL` ni o'chirsa bo'ladi.
5. Ixtiyoriy qo'shimcha himoya: Blueprint sahifasi → **Settings → Auto Sync → No**. Unda `render.yaml`
   o'zgarishi faqat **Manual sync** bosilganda qo'llanadi.
