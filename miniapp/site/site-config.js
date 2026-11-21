/*
 * Sayt sozlamalari (chicken24.uz). Kod emas - joylashga qarab o'zgartiriladi.
 * API_BASE - bot serveri (Render). O'zgartirsangiz site/index.html dagi CSP connect-src ni ham yangilang.
 * TURNSTILE_SITEKEY - Cloudflare Turnstile "site key" (ochiq kalit). Bo'sh bo'lsa CAPTCHA ko'rsatilmaydi;
 *   botda TURNSTILE_SECRET berilgan bo'lsa, sitekey ham SHART (aks holda buyurtmalar rad etiladi).
 * TARIFF_FALLBACK - bot javob bermasa ko'rsatiladigan tarif (haqiqiysini bot /api/web-config beradi; narxni server masofadan hisoblaydi).
 */
window.SITE_CONFIG = Object.freeze({
  API_BASE: 'https://chicken24-bot.onrender.com',
  TURNSTILE_SITEKEY: '',
  TARIFF_FALLBACK: Object.freeze({ base_km: 2, base_fee: 10000, extra_per_km: 2500 }),
});
