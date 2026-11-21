/*
 * Chicken24 — bot natijasini (?ok= / ?rej= / ?cx=) lokal buyurtmalarga qo'llash.
 * Sof funksiyalar (DOM va localStorage yo'q) — bot/tests/test_miniapp_result.py node orqali tekshiradi.
 *
 * Bot tugma havolasiga mijozning ENG OXIRGI buyurtmasi natijasini bazadan qo'yadi, shuning uchun bir xil
 * natija har ochilishda qayta keladi. Qo'llash IDEMPOTENT: buyurtma allaqachon shu holatda bo'lsa yoki
 * bunday ref lokal tarixda yo'q bo'lsa - hech narsa o'zgarmaydi (null qaytadi).
 */
(function (root) {
  'use strict';

  // Bot qaytaradigan sabab kodlari (handlers.py): matni lug'atda - reject.<kod>
  // 'area' - eski buyurtmalar uchun (hudud cheklovi endi yo'q); 'location' - lokatsiyasiz buyurtma
  const REJECT_CODES = ['limit', 'menu', 'area', 'location', 'data', 'server', 'closed'];

  /** URL query -> { kind: 'ok'|'rej'|'cx', ref, orderNo?, code? } yoki null. */
  function parseResult(search) {
    const p = new URLSearchParams(search || '');
    const cx = p.get('cx');
    if (cx) return { kind: 'cx', ref: cx };
    const ok = p.get('ok');
    if (ok) return { kind: 'ok', ref: ok, orderNo: /^\d{1,10}$/.test(p.get('n') || '') ? p.get('n') : '' };
    const rej = p.get('rej');
    if (rej) return { kind: 'rej', ref: rej, code: REJECT_CODES.includes(p.get('r')) ? p.get('r') : 'data' };
    return null;
  }

  /**
   * Natijani buyurtmalar ro'yxatiga qo'llaydi (ro'yxat o'zgartirilmaydi - yangisi qaytadi).
   * Qaytadi: null (o'zgarish yo'q) yoki
   *   { orders: yangi ro'yxat, order: o'zgarishdan OLDINGI yozuv, accepted: bool }
   * accepted = bot buyurtmani qabul qilgani endi ma'lum bo'ldi (savat va forma tozalanadi):
   *   - ok: kutilayotgan buyurtma qabul qilindi;
   *   - cx: kutilayotgan buyurtma (ok hali qo'llanmagan) qabul qilinib, keyin bekor qilingan.
   * Rad etilganda (rej) savat saqlanadi - mijoz qaytadan tanlamasligi uchun.
   */
  function applyResult(orders, result) {
    if (!result || !result.ref || !Array.isArray(orders)) return null;
    const order = orders.find((o) => o && o.localId === result.ref);
    if (!order) return null; // eski / boshqa qurilmadagi natija - e'tibor berilmaydi

    // 'lost' - "botga yetib bormadi" deb belgilangan, lekin bot keyin baribir olgan bo'lishi mumkin:
    // natija kelsa kutilayotgan buyurtma kabi qo'llanadi (qabul qilingan bo'lsa savat tozalanadi - takror yo'q)
    const waiting = order.status === 'pending' || order.status === 'lost';
    let patch = null;
    let accepted = false;
    if (result.kind === 'cx') {
      if (waiting || order.status === 'ok') {
        patch = { status: 'cancelled', reason: '' };
        accepted = waiting;
      }
    } else if (waiting) {
      if (result.kind === 'ok') {
        patch = { status: 'ok', orderNo: result.orderNo || '', reason: '' };
        accepted = true;
      } else if (result.kind === 'rej') {
        patch = { status: 'rejected', reasonCode: result.code || 'data', reason: '' };
      }
    }
    if (!patch) return null;
    return {
      order,
      accepted,
      orders: orders.map((o) => (o === order ? Object.assign({}, o, patch) : o)),
    };
  }

  /**
   * Bot serveridan kelgan holat (GET /api/order-status) -> applyResult uchun natija.
   * accepted -> ok, cancelled -> cx, rejected -> rej. unknown/pending/buzuq javob -> null (kutish davom etadi).
   */
  function statusToResult(ref, data) {
    if (!ref || !data || typeof data !== 'object') return null;
    if (data.status === 'accepted') {
      const n = Number.isInteger(data.n) && data.n > 0 && data.n < 1e10 ? String(data.n) : '';
      return { kind: 'ok', ref, orderNo: n };
    }
    if (data.status === 'cancelled') return { kind: 'cx', ref };
    if (data.status === 'rejected') {
      return { kind: 'rej', ref, code: REJECT_CODES.includes(data.reason) ? data.reason : 'data' };
    }
    return null;
  }

  // Holatni so'rash jadvali: dastlabki 2 daqiqa - har 4 s, keyin har 10 s, 15 daqiqadan keyin to'xtaydi
  const POLL = { fastMs: 4000, slowMs: 10000, fastForMs: 2 * 60 * 1000, maxMs: 15 * 60 * 1000 };

  /** So'rash boshlanganidan beri o'tgan vaqt -> keyingi so'rovgacha kutish (ms) yoki null (to'xtash). */
  function pollDelay(elapsedMs) {
    const t = Number.isFinite(elapsedMs) && elapsedMs > 0 ? elapsedMs : 0;
    if (t >= POLL.maxMs) return null;
    return t < POLL.fastForMs ? POLL.fastMs : POLL.slowMs;
  }

  // Server shuncha vaqt ketma-ket aniq "unknown" desa - buyurtma botga yetib bormagan deb hisoblanadi
  const LOST_AFTER_MS = 10 * 60 * 1000;

  /**
   * Server "unknown" (bot bu buyurtmani olmagan) qaytardi. Birinchi marta - vaqti eslab qolinadi
   * (unknownSince); LOST_AFTER_MS o'tgach - holat 'lost' ("botga yetib bormadi": savat saqlanadi,
   * qayta yuborish mumkin). Tarmoq xatosi va 503 bu yerga kelmaydi (ular hisobga olinmaydi).
   * Qaytadi: null (o'zgarish yo'q) yoki { orders, lost: bool }.
   */
  function noteUnknown(orders, ref, nowMs) {
    if (!Array.isArray(orders) || !ref || !Number.isFinite(nowMs)) return null;
    const order = orders.find((o) => o && o.localId === ref);
    if (!order || order.status !== 'pending') return null;
    const since = Date.parse(order.unknownSince || '');
    let patch;
    if (Number.isNaN(since)) patch = { unknownSince: new Date(nowMs).toISOString() };
    else if (nowMs - since >= LOST_AFTER_MS) patch = { status: 'lost' };
    else return null;
    return {
      lost: patch.status === 'lost',
      orders: orders.map((o) => (o === order ? Object.assign({}, o, patch) : o)),
    };
  }

  root.CHICKEN24_RESULT = {
    REJECT_CODES, POLL, LOST_AFTER_MS, parseResult, applyResult, statusToResult, pollDelay, noteUnknown,
  };
})(typeof window !== 'undefined' ? window : globalThis);
