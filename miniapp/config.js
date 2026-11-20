/*
 * Chicken24 Mini App sozlamalari (kod emas - joylashga qarab o'zgartiriladi).
 * API_BASE - bot serveri (Render) manzili: Mini App ochiq turganda buyurtma holatini shu yerdan so'raydi
 * (GET /api/order-status). Bo'sh qoldirilsa so'rash o'chiq - natija avvalgidek bot tugmasi havolasidan
 * (?ok= / ?rej= / ?cx=) keladi. O'zgartirsangiz index.html dagi CSP connect-src ga ham shu manzilni yozing.
 */
window.CHICKEN24_CONFIG = Object.freeze({
  API_BASE: 'https://chicken24-bot.onrender.com',
});
