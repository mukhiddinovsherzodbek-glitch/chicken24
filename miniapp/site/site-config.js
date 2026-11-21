/*
 * Sayt sozlamalari (chicken24.uz). Kod emas - joylashga qarab o'zgartiriladi.
 * API_BASE - bot serveri (Render). O'zgartirsangiz site/index.html dagi CSP connect-src ni ham yangilang.
 * TURNSTILE_SITEKEY - Cloudflare Turnstile "site key" (ochiq kalit). Bo'sh bo'lsa CAPTCHA ko'rsatilmaydi;
 *   botda TURNSTILE_SECRET berilgan bo'lsa, sitekey ham SHART (aks holda buyurtmalar rad etiladi).
 * DELIVERY_FEE - faqat ko'rsatish uchun; haqiqiy summani bot menu.json va sozlamadan o'zi hisoblaydi.
 */
window.SITE_CONFIG = Object.freeze({
  API_BASE: 'https://chicken24-bot.onrender.com',
  TURNSTILE_SITEKEY: '',
  DELIVERY_FEE: 10000,
});
