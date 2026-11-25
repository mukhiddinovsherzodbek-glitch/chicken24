# Baza uchun SSL sertifikatlari (ochiq, sir emas)

`supabase-ca.crt` — Supabase'ning CA sertifikati. Supabase server sertifikatini o'z CA'si bilan imzolaydi,
shuning uchun bot uni shu fayl bilan tekshiradi (`DATABASE_URL` oxirida `sslrootcert=certs/supabase-ca.crt`).

Olish: Supabase → loyiha → **Database Settings → SSL Configuration → Download Certificate** →
faylni shu papkaga `supabase-ca.crt` nomi bilan saqlang. Batafsil: `docs/kurer/MIGRATSIYA.md`.

Bu fayl ochiq sertifikat (parol yoki kalit emas) — git'ga qo'shiladi, Render ham shu yerdan o'qiydi.
Shu papkaga hech qachon `.key` (maxfiy kalit) yoki parol qo'ymang.
