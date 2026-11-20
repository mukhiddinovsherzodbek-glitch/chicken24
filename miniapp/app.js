/* =========================================================
   Chicken24 Mini App
   Vanilla JS, framework va build yo'q.
   Xavfsizlik: barcha matnlar textContent orqali chiqariladi (innerHTML yo'q).
   ========================================================= */
'use strict';

(() => {
  // ---------------------------------------------------------------- sozlamalar
  const MENU_URL = 'menu.json';
  const LIMITS = {
    nameMin: 2,
    nameMax: 50,
    phoneDigits: 9,
    addressMax: 300,
    detailsMax: 100, // uy / qavat / xonadon (bot: DETAILS_MAX)
    commentMax: 200, // izoh (bot: COMMENT_MAX)
    maxLines: 30,
    qtyMax: 50,
    payloadMaxBytes: 4096,
    ordersKeep: 20,
  };
  // draft - yuborilgan, lekin hali qabul qilinmagan forma; legacyProfile - eski versiyadagi ism/telefon (o'chiriladi)
  const STORAGE = { cart: 'cart', orders: 'orders', draft: 'checkoutDraft', legacyProfile: 'profile' };
  const ORDER_STATUSES = ['pending', 'ok', 'rejected', 'cancelled'];
  // Yetkazish hududi. ASOSIY sozlama bot/config.py da (DELIVERY_*), bu yerda uning nusxasi:
  // Mini App faqat mijozni darhol ogohlantiradi, haqiqiy tekshiruv botda.
  const DELIVERY_AREA = { name: 'Samarqand shahri', lat: 39.6542, lon: 66.9597, radiusKm: 12 };
  // Biznes qoidalari. ASOSIY sozlama bot/config.py da (DELIVERY_FEE, OPEN_FROM, CLOSE_AT, UTC_OFFSET_HOURS).
  // Narx va ish vaqti bot tomonida qayta tekshiriladi; bu yerda faqat mijozga ko'rsatish uchun.
  const BUSINESS = { deliveryFee: 10000, openFrom: '10:00', closeAt: '03:00', utcOffsetHours: 5 };
  // Bot bilan bir xil: shu oraliqda qayta yuborilgan buyurtmani bot rad etadi
  const ORDER_COOLDOWN_MS = 30 * 1000;
  // Shuncha vaqt javob bo'lmasa "javob olinmadi" deb ko'rsatiladi
  const PENDING_STALE_MS = 30 * 60 * 1000;
  // Bot natijasini qo'llash (result.js) va sabab kodlari (handlers.py): matni lug'atda - reject.<kod>
  const RESULT = window.CHICKEN24_RESULT;
  const REJECT_CODES = RESULT.REJECT_CODES;

  // ---------------------------------------------------------------- til (uz | ru)
  // Barcha matnlar i18n.js dagi lug'atdan. Til manbai: ?lang= (bot tugmasi havolasi),
  // bo'lmasa Telegram tili (ru -> ru, aks holda uz).
  const I18N = window.CHICKEN24_I18N || { uz: {}, ru: {} };
  const LANG = (() => {
    const fromUrl = new URLSearchParams(window.location.search).get('lang');
    if (fromUrl === 'uz' || fromUrl === 'ru') return fromUrl;
    try {
      const tgUser = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe
        && window.Telegram.WebApp.initDataUnsafe.user;
      const code = tgUser && typeof tgUser.language_code === 'string' ? tgUser.language_code.toLowerCase() : '';
      if (code.startsWith('ru')) return 'ru';
    } catch (e) { /* e'tiborsiz */ }
    return 'uz';
  })();

  /** Lug'atdan matn: T('kalit', { param: qiymat }). Ruscha topilmasa - o'zbekcha, u ham bo'lmasa - kalit. */
  function T(key, params) {
    const dict = I18N[LANG] || {};
    let text = dict[key] !== undefined ? dict[key] : (I18N.uz && I18N.uz[key] !== undefined ? I18N.uz[key] : key);
    const values = Object.assign({ area: (I18N[LANG] || {})['area.name'] || '' }, params || {});
    return String(text).replace(/\{(\w+)\}/g, (m, name) => (values[name] !== undefined ? String(values[name]) : m));
  }

  /** Ko'plik shakli (ruscha: 1 блюдо / 2 блюда / 5 блюд). O'zbekchada uchala shakl bir xil. */
  function TP(key, n, params) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    let form = 'many';
    if (mod10 === 1 && mod100 !== 11) form = 'one';
    else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) form = 'few';
    return T(`${key}_${form}`, Object.assign({ n }, params || {}));
  }

  /** menu.json maydoni tanlangan tilda: ruscha bo'lsa <maydon>_ru, bo'sh bo'lsa o'zbekchasi (fallback). */
  function localized(obj, field) {
    if (LANG === 'ru') {
      const ru = obj[`${field}_ru`];
      if (typeof ru === 'string' ? ru.trim() : Array.isArray(ru) && ru.length) return ru;
    }
    return obj[field];
  }

  function rejectReason(code) {
    return T(`reject.${REJECT_CODES.includes(code) ? code : 'data'}`, { open: BUSINESS.openFrom, close: BUSINESS.closeAt });
  }

  // ---------------------------------------------------------------- ish vaqti (Samarqand, UTC+5)
  function hhmmToMinutes(s) {
    const [h, m] = s.split(':').map(Number);
    return h * 60 + m;
  }

  /** Samarqand vaqti bo'yicha ochiqmi. Qurilma soat mintaqasiga bog'liq emas: UTC + 5 soat. */
  function isOpenAt(date) {
    const local = new Date(date.getTime() + BUSINESS.utcOffsetHours * 3600 * 1000);
    const t = local.getUTCHours() * 60 + local.getUTCMinutes();
    const from = hhmmToMinutes(BUSINESS.openFrom);
    const to = hhmmToMinutes(BUSINESS.closeAt);
    // Oraliq yarim tundan o'tsa (10:00-03:00): from dan keyin YOKI to dan oldin
    return from < to ? t >= from && t < to : t >= from || t < to;
  }

  function isOpenNow() {
    return isOpenAt(new Date(Date.now()));
  }
  // ?debug=1 - brauzerda (Telegram'siz) sinash: sendData o'rniga konsolga yoziladi
  const DEBUG = new URLSearchParams(window.location.search).has('debug');

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  // Telegram'dan tashqarida SDK platform = "unknown" bo'ladi
  const inTelegram = !!(tg && tg.platform && tg.platform !== 'unknown');

  function tgAtLeast(version) {
    try {
      return !!(inTelegram && tg.isVersionAtLeast && tg.isVersionAtLeast(version));
    } catch (e) {
      return false;
    }
  }
  const useTgBackButton = tgAtLeast('6.1') && !!tg.BackButton;

  // ---------------------------------------------------------------- DOM
  const $view = document.getElementById('view');
  const $topbar = document.getElementById('topbar');
  const $brand = document.getElementById('brand');
  const $title = document.getElementById('topbarTitle');
  const $backBtn = document.getElementById('backBtn');
  const $cartBar = document.getElementById('cartBar');
  const $cartBarBtn = document.getElementById('cartBarBtn');
  const $cartBarTotal = document.getElementById('cartBarTotal');
  const $toast = document.getElementById('toast');
  const $tabMenu = document.getElementById('tabMenu');
  const $tabOrders = document.getElementById('tabOrders');

  // ---------------------------------------------------------------- holat
  const state = {
    menu: null, // { categories, catById, byId, byCat }
    cart: new Map(), // id -> qty (tartib = qo'shilgan tartib)
    stack: [{ name: 'home' }],
    tab: 'menu', // faol yorliq: 'menu' | 'orders'
    variantSel: new Map(), // karta (guruh) -> tanlangan variant ID si
    entryByItem: new Map(), // joriy kategoriya: taom ID -> karta
    menuStack: null, // "Buyurtmalarim" ga o'tilganda menyudagi joy
    checkout: {
      name: '',
      phone: '', // faqat 9 ta raqam, +998 siz
      address: '',
      details: '', // uy / qavat / xonadon (ixtiyoriy, ikkala manzil turida ham)
      comment: '', // izoh (ixtiyoriy)
      loc: null, // { lat, lon }
      locStatus: 'idle', // idle | loading | ok | error
      locError: '',
      locCanOpenSettings: false,
      touched: { name: false, phone: false },
      submitting: false,
    },
  };

  // ---------------------------------------------------------------- yordamchilar
  /** Xavfsiz element yaratish: matnlar faqat textContent orqali. */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') el.className = value;
        else if (key === 'dataset') Object.assign(el.dataset, value);
        else if (key === 'value') el.value = value;
        else if (value === true) el.setAttribute(key, '');
        else el.setAttribute(key, String(value));
      }
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  /** 25000 -> "25 000 so'm" */
  function fmtPrice(amount) {
    return String(Math.round(amount)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ' + T('currency');
  }

  /** Belgilar soni (Python len() bilan bir xil - emoji 1 ta hisoblanadi) */
  function charLen(str) {
    return Array.from(str).length;
  }

  function fmtDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  let toastTimer = 0;
  function toast(message, isError) {
    $toast.textContent = message;
    $toast.classList.toggle('error', !!isError);
    $toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $toast.classList.remove('show'), 3200);
  }

  function haptic(kind) {
    if (!tgAtLeast('6.1') || !tg.HapticFeedback) return;
    try {
      if (kind === 'success' || kind === 'error' || kind === 'warning') tg.HapticFeedback.notificationOccurred(kind);
      else tg.HapticFeedback.impactOccurred(kind || 'light');
    } catch (e) { /* e'tiborsiz */ }
  }

  // ---------------------------------------------------------------- localStorage (xavfsiz)
  // localStorage ishlamasa (taqiqlangan, to'lgan) ilova xotirada ishlashda davom etadi.
  const memoryStore = Object.create(null);

  function storageGet(key) {
    if (key in memoryStore) return memoryStore[key];
    try {
      const raw = window.localStorage.getItem(key);
      const value = raw === null ? null : JSON.parse(raw);
      memoryStore[key] = value;
      return value;
    } catch (e) {
      console.warn('localStorage o\'qilmadi:', key, e);
      return null;
    }
  }

  function storageSet(key, value) {
    memoryStore[key] = value;
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (e) {
      console.warn('localStorage yozilmadi:', key, e);
      return false;
    }
  }

  function storageRemove(key) {
    delete memoryStore[key];
    try {
      window.localStorage.removeItem(key);
    } catch (e) {
      console.warn('localStorage dan o\'chirilmadi:', key, e);
    }
  }

  // ---------------------------------------------------------------- menyu
  async function loadMenu() {
    const res = await fetch(MENU_URL, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return normalizeMenu(await res.json());
  }

  function normalizeMenu(data) {
    const rawCats = data && Array.isArray(data.categories) ? data.categories : [];
    const rawItems = data && Array.isArray(data.items) ? data.items : [];
    const str = (v) => (typeof v === 'string' ? v : '');

    const strList = (v) => (Array.isArray(v) ? v.filter((c) => typeof c === 'string' && c.trim()).map((c) => c.trim()) : []);

    // Kategoriya nomi, tavsif, kombo tarkibi, variant - tanlangan tilda (ruscha maydon bo'lmasa o'zbekcha).
    // TAOM NOMI (name) tarjima qilinmaydi.
    const categories = rawCats
      .filter((c) => c && typeof c.id === 'string' && typeof c.name === 'string')
      .map((c) => ({ id: c.id, name: str(localized(c, 'name')) || c.name, emoji: str(c.emoji) || '🍽️', image: str(c.image) }));
    const catById = new Map(categories.map((c) => [c.id, c]));
    const byCat = new Map(categories.map((c) => [c.id, []]));
    const byId = new Map();

    for (const it of rawItems) {
      if (!it || typeof it.id !== 'string' || byId.has(it.id)) continue;
      if (!catById.has(it.category) || typeof it.name !== 'string') continue;
      if (!Number.isInteger(it.price) || it.price <= 0) continue;
      const item = {
        id: it.id,
        category: it.category,
        name: it.name,
        desc: str(localized(it, 'desc')),
        // Ixtiyoriy tarkib ro'yxati (kombo) - faqat ko'rsatish uchun
        contents: strList(localized(it, 'items')),
        // Ixtiyoriy: bitta kartadagi variantlar guruhi ("Original Wings") va variant ("6 dona")
        group: str(it.group).trim(),
        groupTitle: str(localized(it, 'group')).trim(),
        variant: str(it.variant).trim(),
        variantLabel: str(localized(it, 'variant')).trim(),
        price: it.price,
        image: str(it.image),
      };
      byId.set(item.id, item);
      byCat.get(item.category).push(item);
    }
    return { categories, catById, byCat, byId };
  }

  // ---------------------------------------------------------------- savat
  function loadCart() {
    const raw = storageGet(STORAGE.cart);
    const cart = new Map();
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      for (const [id, qty] of Object.entries(raw)) {
        if (Number.isInteger(qty) && qty >= 1) cart.set(id, Math.min(qty, LIMITS.qtyMax));
      }
    }
    return cart;
  }

  function saveCart() {
    storageSet(STORAGE.cart, Object.fromEntries(state.cart));
  }

  /** Menyuda yo'q taomlarni savatdan olib tashlaydi. */
  function pruneCart() {
    let removed = 0;
    for (const id of Array.from(state.cart.keys())) {
      if (!state.menu.byId.has(id)) {
        state.cart.delete(id);
        removed++;
      }
    }
    // Limitdan oshgan qatorlar (eski/buzilgan ma'lumot bo'lsa)
    for (const id of Array.from(state.cart.keys()).slice(LIMITS.maxLines)) {
      state.cart.delete(id);
      removed++;
    }
    if (removed) saveCart();
    return removed;
  }

  function cartTotal() {
    let total = 0;
    for (const [id, qty] of state.cart) {
      const item = state.menu.byId.get(id);
      if (item) total += item.price * qty;
    }
    return total;
  }

  /** Taomlar + yetkazish (bo'sh savatda 0). Bot ham xuddi shunday hisoblaydi. */
  function grandTotal() {
    return state.cart.size ? cartTotal() + BUSINESS.deliveryFee : 0;
  }

  function setQty(id, qty) {
    if (!state.menu.byId.has(id)) return;
    if (qty <= 0) {
      state.cart.delete(id);
    } else {
      if (!state.cart.has(id) && state.cart.size >= LIMITS.maxLines) {
        toast(T('toast.max_lines', { max: LIMITS.maxLines }), true);
        haptic('error');
        return;
      }
      if (qty > LIMITS.qtyMax) {
        toast(T('toast.max_qty', { max: LIMITS.qtyMax }), true);
        qty = LIMITS.qtyMax;
      }
      state.cart.set(id, qty);
    }
    saveCart();
    haptic('light');
    onCartChanged(id);
  }

  function onCartChanged(id) {
    updateCartBar(true);
    const view = currentView();
    if (view.name === 'category') {
      updateItemControl(id);
    } else if (view.name === 'cart') {
      if (state.cart.size === 0) render();
      else renderCartLines();
    }
  }

  // ---------------------------------------------------------------- forma qoralamasi
  // Buyurtma yuborilganda forma (ism, telefon, manzil, uy/qavat, izoh, lokatsiya) STORAGE.draft ga yoziladi.
  // Bot rad etsa yoki javob hali kelmasa - qayta ochilganda tiklanadi (mijoz qayta urina oladi).
  // Bot QABUL QILSA (ok) - qoralama o'chiriladi va keyingi buyurtmada hamma maydon bo'sh bo'ladi.
  const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : '');

  function saveDraft(ref) {
    const co = state.checkout;
    storageSet(STORAGE.draft, {
      ref,
      name: co.name,
      phone: co.phone,
      address: co.address,
      details: co.details,
      comment: co.comment,
      loc: co.locStatus === 'ok' && co.loc ? co.loc : null,
    });
  }

  function loadDraft() {
    // Eski versiyadagi doimiy ism/telefon kaliti endi ishlatilmaydi
    storageRemove(STORAGE.legacyProfile);
    const d = storageGet(STORAGE.draft);
    if (!d || typeof d !== 'object') return;
    const co = state.checkout;
    co.name = str(d.name, LIMITS.nameMax);
    co.phone = typeof d.phone === 'string' && /^\d{0,9}$/.test(d.phone) ? d.phone : '';
    co.address = str(d.address, LIMITS.addressMax);
    co.details = str(d.details, LIMITS.detailsMax);
    co.comment = str(d.comment, LIMITS.commentMax);
    const loc = d.loc;
    if (!co.address && loc && Number.isFinite(loc.lat) && Number.isFinite(loc.lon)
        && distanceKm(DELIVERY_AREA.lat, DELIVERY_AREA.lon, loc.lat, loc.lon) <= DELIVERY_AREA.radiusKm) {
      co.loc = { lat: loc.lat, lon: loc.lon };
      co.locStatus = 'ok';
    }
  }

  /** Bot qabul qilgandan keyin: forma maydonlari va ularning xotiradagi nusxasi tozalanadi. */
  function clearCheckout(ref) {
    const d = storageGet(STORAGE.draft);
    // Qoralama boshqa (keyinroq yuborilgan) buyurtmaga tegishli bo'lsa - tegilmaydi
    if (d && typeof d === 'object' && d.ref && d.ref !== ref) return;
    storageRemove(STORAGE.draft);
    storageRemove(STORAGE.legacyProfile);
    Object.assign(state.checkout, {
      name: '', phone: '', address: '', details: '', comment: '',
      loc: null, locStatus: 'idle', locError: '', locCanOpenSettings: false,
      touched: { name: false, phone: false },
    });
  }

  // ---------------------------------------------------------------- buyurtmalar tarixi
  /**
   * Buyurtmalar ro'yxati (yangidan eskiga, oxirgi 20 ta).
   * Keyinchalik API'ga o'tish uchun yagona kirish nuqtasi - shu funksiya.
   */
  /** Manfiy bo'lmagan son yoki null (eski/buzilgan yozuvlar uchun). */
  function num(v) {
    return Number.isFinite(v) && v >= 0 ? v : null;
  }

  function getOrders() {
    const raw = storageGet(STORAGE.orders);
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((o) => o && typeof o === 'object' && typeof o.localId === 'string' && Array.isArray(o.items))
      .map((o) => ({
        localId: o.localId,
        date: typeof o.date === 'string' ? o.date : '',
        // Yuborilgan paytdagi nusxa (snapshot): narxlar menu.json dan qayta hisoblanmaydi.
        // Eski yozuvlarda ba'zi maydonlar bo'lmasligi mumkin - ular null bo'lib qoladi va ko'rsatilmaydi.
        items: o.items
          .filter((i) => i && typeof i.id === 'string' && Number.isInteger(i.qty) && i.qty > 0)
          .map((i) => ({
            id: i.id,
            name: typeof i.name === 'string' ? i.name : i.id,
            qty: i.qty,
            price: num(i.price),
          })),
        itemsTotal: num(o.itemsTotal),
        deliveryFee: num(o.deliveryFee),
        total: num(o.total),
        payment: typeof o.payment === 'string' ? o.payment : '',
        addressType: o.addressType === 'location' ? 'location' : 'text',
        addressText: typeof o.addressText === 'string' ? o.addressText : '',
        // Holat: pending (bot javobi kutilmoqda) | ok (qabul qilindi) | rejected | legacy (eski yozuv)
        status: ORDER_STATUSES.includes(o.status) ? o.status : 'legacy',
        orderNo: typeof o.orderNo === 'string' && /^\d{1,10}$/.test(o.orderNo) ? o.orderNo : '',
        reason: typeof o.reason === 'string' ? o.reason : '', // eski versiya: tayyor matn (o'zbekcha)
        reasonCode: REJECT_CODES.includes(o.reasonCode) ? o.reasonCode : '',
      }))
      .sort((a, b) => (b.date > a.date ? 1 : b.date < a.date ? -1 : 0))
      .slice(0, LIMITS.ordersKeep);
  }

  function saveOrder(order) {
    const list = getOrders();
    list.unshift(order);
    return storageSet(STORAGE.orders, list.slice(0, LIMITS.ordersKeep));
  }

  function removeOrder(localId) {
    storageSet(STORAGE.orders, getOrders().filter((o) => o.localId !== localId));
  }


  /** Eng oxirgi "tasdiq kutilmoqda" holatidagi buyurtma (bo'lmasa null). */
  function lastPendingOrder() {
    return getOrders().find((o) => o.status === 'pending') || null;
  }

  function msSince(iso) {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? Infinity : Date.now() - t;
  }

  function cartMatchesOrder(order) {
    if (state.cart.size !== order.items.length) return false;
    return order.items.every((i) => state.cart.get(i.id) === i.qty);
  }

  /**
   * Bot javobini qo'llash. sendData() Mini App'ni yopadi, shuning uchun bot mijozning eng oxirgi
   * buyurtmasi natijasini tugma havolasiga bazadan qo'yadi:
   *   ?ok=<ref>&n=<raqam>  |  ?rej=<ref>&r=<sabab kodi>  |  ?cx=<ref>
   * INDAMASDAN qo'llanadi (toast/banner yo'q - xabar bot chatida keladi) va IDEMPOTENT (result.js):
   * bir xil natija har ochilishda kelsa ham faqat birinchi marta o'zgartiradi.
   */
  function applyBotResult() {
    applyResultObject(RESULT.parseResult(window.location.search));
  }

  /** Natijani (havoladan yoki serverdan) qo'llaydi. Nimadir o'zgarsa true. Indamasdan, idempotent. */
  function applyResultObject(result) {
    const res = RESULT.applyResult(getOrders(), result);
    if (!res) return false;
    storageSet(STORAGE.orders, res.orders);
    if (res.accepted) {
      // Savat faqat aynan shu buyurtma bilan bir xil bo'lsa tozalanadi (mijoz keyin o'zgartirgan bo'lishi mumkin)
      if (cartMatchesOrder(res.order)) {
        state.cart.clear();
        saveCart();
      }
      // Forma (ism, telefon, manzil...) faqat bot qabul qilgandan keyin tozalanadi
      clearCheckout(res.order.localId);
    }
    // Rad etilsa: holat "Rad etildi" (sabab kodi bilan), savat va forma saqlanadi
    return true;
  }

  // ---------------------------------------------------------------- buyurtma holatini serverdan so'rash
  /*
   * Mini App ochiq turganda bot unga natija yubora olmaydi, shuning uchun "tasdiq kutilmoqda" buyurtma
   * bo'lsa holat bot serveridan so'raladi: GET {API_BASE}/api/order-status?ref=..., foydalanuvchi
   * Telegram initData bilan aniqlanadi. Jadval result.js -> pollDelay (4 s, 2 daqiqadan keyin 10 s,
   * 15 daqiqagacha); ochilganda va ekranga qaytganda darhol. Natija indamasdan qo'llanadi.
   * Tarmoq xatosi, 429, 503 - jim, keyingi safar. API_BASE bo'lmasa - o'chiq (?ok= havolasi ishlayveradi).
   */
  const API_BASE = (() => {
    const cfg = window.CHICKEN24_CONFIG || {};
    const v = typeof cfg.API_BASE === 'string' ? cfg.API_BASE.trim().replace(/\/+$/, '') : '';
    return /^https:\/\/[a-z0-9.-]+(:\d{1,5})?$/i.test(v) ? v : '';
  })();
  const STATUS_MAX_ORDER_AGE_MS = 24 * 60 * 60 * 1000; // initData ham 24 soat amal qiladi
  const STATUS_FETCH_TIMEOUT_MS = 8000;
  const statusPoll = { timer: null, startedAt: 0, inFlight: false, stopped: false, rerender: false };

  function statusPollEnabled() {
    return !!API_BASE && inTelegram && !!(tg && tg.initData) && !statusPoll.stopped;
  }

  /** So'raladigan buyurtma: eng yangi "kutilmoqda" (24 soatdan yosh). */
  function statusPollTarget() {
    return getOrders().find((o) => o.status === 'pending' && msSince(o.date) < STATUS_MAX_ORDER_AGE_MS) || null;
  }

  /** Ochilganda / ekranga qaytganda: 15 daqiqalik jadval qaytadan, birinchi so'rov darhol. */
  function startStatusPolling() {
    if (!statusPollEnabled() || !statusPollTarget()) return;
    statusPoll.startedAt = Date.now();
    clearTimeout(statusPoll.timer);
    statusPoll.timer = null;
    pollStatusNow();
  }

  function scheduleStatusPoll() {
    clearTimeout(statusPoll.timer);
    statusPoll.timer = null;
    if (!statusPollEnabled() || document.hidden || !statusPollTarget()) return;
    const delay = RESULT.pollDelay(Date.now() - statusPoll.startedAt);
    if (delay !== null) statusPoll.timer = setTimeout(pollStatusNow, delay);
  }

  async function pollStatusNow() {
    if (statusPoll.inFlight) return; // javob kelgach o'zi rejalashtiradi
    const order = statusPollTarget();
    if (!order || !statusPollEnabled()) return;
    statusPoll.inFlight = true;
    try {
      const data = await fetchOrderStatus(order.localId);
      if (data && applyResultObject(RESULT.statusToResult(order.localId, data))) rerenderQuietly();
    } catch (e) {
      /* tarmoq xatosi - jim, keyingi safar */
    } finally {
      statusPoll.inFlight = false;
      scheduleStatusPoll();
    }
  }

  async function fetchOrderStatus(ref) {
    const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), STATUS_FETCH_TIMEOUT_MS) : null;
    try {
      const resp = await fetch(`${API_BASE}/api/order-status?ref=${encodeURIComponent(ref)}`, {
        method: 'GET',
        headers: { Authorization: `tma ${tg.initData}` },
        cache: 'no-store',
        credentials: 'omit',
        mode: 'cors',
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (resp.status === 401) { // initData yaroqsiz/eskirgan - qayta urinish foydasiz
        statusPoll.stopped = true;
        return null;
      }
      if (!resp.ok) return null; // 429 / 503 / 5xx - jim
      return await resp.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /** Holat o'zgargach ekranni yangilash; mijoz maydonga yozayotgan bo'lsa - fokus chiqqandan keyin. */
  function rerenderQuietly() {
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) {
      statusPoll.rerender = true;
      return;
    }
    statusPoll.rerender = false;
    render(false);
  }

  document.addEventListener('focusout', () => {
    if (!statusPoll.rerender) return;
    setTimeout(() => { if (statusPoll.rerender) rerenderQuietly(); }, 0);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(statusPoll.timer);
      statusPoll.timer = null;
    } else {
      startStatusPolling();
    }
  });

  function reorder(localId) {
    const order = getOrders().find((o) => o.localId === localId);
    if (!order) return;
    const missing = [];
    let added = 0;
    let limited = false;
    for (const it of order.items) {
      if (!state.menu.byId.has(it.id)) {
        missing.push(it.name);
        continue;
      }
      if (!state.cart.has(it.id) && state.cart.size >= LIMITS.maxLines) {
        limited = true;
        continue;
      }
      const qty = Math.min((state.cart.get(it.id) || 0) + it.qty, LIMITS.qtyMax);
      state.cart.set(it.id, qty);
      added++;
    }
    saveCart();
    updateCartBar(true);

    if (!added) {
      toast(missing.length ? T('reorder.none', { list: missing.join(', ') }) : T('reorder.nothing'), true);
      haptic('error');
      return;
    }
    haptic('success');
    openInMenu({ name: 'cart' });
    // Narxlar joriy menyudan olinadi - savatda ko'rsatiladi
    if (missing.length) toast(T('reorder.some_missing', { list: missing.join(', ') }), true);
    else if (limited) toast(T('reorder.limited', { max: LIMITS.maxLines }), true);
    else toast(T('reorder.ok'));
  }

  // ---------------------------------------------------------------- navigatsiya
  function currentView() {
    return state.stack[state.stack.length - 1];
  }

  function navigate(view) {
    state.stack.push(view);
    render(true);
  }

  function goBack() {
    if (state.stack.length > 1) {
      state.stack.pop();
      render(true);
    }
  }

  function goHome() {
    state.tab = 'menu';
    state.stack = [{ name: 'home' }];
    render(true);
  }

  // ---------------------------------------------------------------- bo'lim yorliqlari (Menu | Buyurtmalarim)
  // Har bo'limning o'z navigatsiya steki bor: "Buyurtmalarim" ga o'tib qaytganda menyudagi joy (kategoriya, savat) saqlanadi.
  function switchTab(tab) {
    if (tab === state.tab) {
      // Faol yorliq qayta bosilsa - bo'lim boshiga
      if (tab === 'menu') goHome();
      else { state.stack = [{ name: 'orders' }]; render(true); }
      return;
    }
    if (tab === 'orders') {
      state.menuStack = state.stack;
      state.tab = 'orders';
      state.stack = [{ name: 'orders' }];
    } else {
      state.tab = 'menu';
      state.stack = state.menuStack || [{ name: 'home' }];
    }
    render(true);
  }

  /** Menyu bo'limidagi sahifani ochish ("Buyurtmalarim" dan ham, masalan savat yoki qayta buyurtma). */
  function openInMenu(view) {
    state.tab = 'menu';
    state.stack = [{ name: 'home' }, view];
    render(true);
  }

  function updateTabs() {
    for (const [el, tab] of [[$tabMenu, 'menu'], [$tabOrders, 'orders']]) {
      const active = state.tab === tab;
      el.classList.toggle('active', active);
      el.setAttribute('aria-selected', String(active));
    }
  }

  function updateChrome(title) {
    const isHome = state.stack.length === 1; // bo'lim boshi (menyu yoki buyurtmalar)
    $topbar.classList.toggle('inner', !isHome);
    $title.hidden = isHome;
    $title.textContent = title || '';
    $brand.setAttribute('aria-hidden', String(!isHome));
    updateTabs();

    if (useTgBackButton) {
      $backBtn.hidden = true;
      if (isHome) tg.BackButton.hide();
      else tg.BackButton.show();
    } else {
      $backBtn.hidden = isHome;
    }
    document.title = isHome ? 'Chicken24' : `${title} · Chicken24`;
  }

  function updateCartBar(bump) {
    const name = currentView().name;
    const visible = state.cart.size > 0 && name !== 'cart' && !!state.menu;
    $cartBarTotal.textContent = state.menu ? fmtPrice(cartTotal()) : '';
    $cartBar.classList.toggle('show', visible);
    $cartBar.setAttribute('aria-hidden', String(!visible));
    $cartBarBtn.tabIndex = visible ? 0 : -1;
    document.body.classList.toggle('has-cart-bar', visible);
    if (bump && visible) {
      $cartBarBtn.classList.remove('bump');
      void $cartBarBtn.offsetWidth; // animatsiyani qayta boshlash
      $cartBarBtn.classList.add('bump');
    }
  }

  // ---------------------------------------------------------------- render
  function render(scrollTop) {
    const view = currentView();
    $view.replaceChildren();
    let title = '';
    if (view.name === 'home') renderHome();
    else if (view.name === 'category') title = renderCategory(view.id);
    else if (view.name === 'cart') title = renderCart();
    else if (view.name === 'orders') title = renderOrders();
    updateChrome(title);
    updateCartBar(false);
    if (scrollTop) window.scrollTo(0, 0);
    // Animatsiyani qayta ishga tushirish
    $view.style.animation = 'none';
    void $view.offsetWidth;
    $view.style.animation = '';
  }

  // Yuklanmagan rasm shuncha marta qayta so'raladi (kechikish, ms), keyingina emoji ko'rsatiladi
  const IMAGE_RETRY_DELAYS = [1000, 2500];

  /** Rasm + fallback: yuklanguncha gradient, 2 marta qayta urinishdan keyin ham bo'lmasa emoji. */
  // Kategoriya rasmlari 800x600 (4:3), taom rasmlari 600x600 (1:1)
  function media(src, emoji, alt, extraClass, width, height) {
    const wrap = h('div', { class: 'media ' + (extraClass || '') },
      h('span', { class: 'media-fallback', 'aria-hidden': 'true' }, emoji || '🍽️'));
    if (!src) {
      wrap.classList.add('failed');
      return wrap;
    }
    const img = h('img', { src, alt, loading: 'lazy', decoding: 'async', width: width || 800, height: height || 600 });
    let attempt = 0;
    img.addEventListener('load', () => wrap.classList.add('loaded'));
    img.addEventListener('error', () => {
      if (attempt < IMAGE_RETRY_DELAYS.length) {
        const delay = IMAGE_RETRY_DELAYS[attempt];
        attempt += 1;
        // Brauzer muvaffaqiyatsiz javobni keshdan qaytarmasligi uchun so'rov parametri qo'shiladi
        setTimeout(() => { img.src = `${src}${src.includes('?') ? '&' : '?'}retry=${attempt}`; }, delay);
        return;
      }
      img.remove();
      wrap.classList.add('failed');
    });
    wrap.append(img);
    return wrap;
  }

  function emptyState(emoji, title, text, button) {
    return h('div', { class: 'empty' },
      h('div', { class: 'empty-emoji', 'aria-hidden': 'true' }, emoji),
      h('p', { class: 'empty-title' }, title),
      text ? h('p', { class: 'empty-text' }, text) : null,
      button || null);
  }

  function renderHome() {
    const user = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.user;
    const greet = user && user.first_name ? T('home.hello', { name: user.first_name }) : T('home.welcome');

    const hero = h('section', { class: 'hero' },
      h('p', { class: 'hero-title' }, greet),
      h('p', { class: 'hero-sub' }, T('home.sub')),
      h('span', { class: 'hero-emoji', 'aria-hidden': 'true' }, '🍔'));

    const grid = h('div', { class: 'cat-grid' }, state.menu.categories.map((cat) => {
      // Variantlar bitta karta bo'lib ko'rinadi - kartalar soni ko'rsatiladi
      const count = buildEntries(state.menu.byCat.get(cat.id) || []).length;
      return h('button', { class: 'cat-card', type: 'button', dataset: { action: 'open-cat', id: cat.id } },
        media(cat.image, cat.emoji, cat.name, 'cat-media'),
        h('div', { class: 'cat-info' },
          h('span', { class: 'cat-name' }, `${cat.emoji} ${cat.name}`),
          count
            ? h('span', { class: 'cat-count' }, TP('home.count', count))
            : h('span', { class: 'badge-soon' }, T('soon'))));
    }));

    // "Buyurtmalarim" endi tepadagi yorliqda - bu yerda takrorlanmaydi
    $view.append(
      hero,
      h('h2', { class: 'section-title' }, T('home.menu')),
      state.menu.categories.length ? grid : emptyState('⏳', T('soon'), T('soon.menu')));
  }

  function itemControl(id) {
    const qty = state.cart.get(id) || 0;
    if (!qty) {
      return h('button', { class: 'btn add-btn', type: 'button', dataset: { action: 'add', id } }, T('btn.add'));
    }
    return stepper(id, qty);
  }

  function stepper(id, qty, compact) {
    return h('div', { class: 'stepper' + (compact ? ' compact' : '') },
      h('button', { class: 'icon-btn dec', type: 'button', dataset: { action: 'dec', id }, 'aria-label': T('aria.dec') }, '−'),
      h('span', { class: 'qty', 'aria-live': 'polite' }, String(qty)),
      h('button', {
        class: 'icon-btn inc', type: 'button', dataset: { action: 'inc', id },
        'aria-label': T('aria.inc'), disabled: qty >= LIMITS.qtyMax,
      }, '+'));
  }

  /** Savat o'zgarganda shu taom joylashgan kartani qayta chizadi (variant belgilari ham yangilanadi). */
  function updateItemControl(id) {
    const entry = state.entryByItem.get(id);
    if (!entry) return;
    const card = $view.querySelector(`.item-card[data-entry="${CSS.escape(entry.key)}"]`);
    if (card) card.replaceWith(renderItemCard(entry));
  }

  // ---------------------------------------------------------------- variantli kartalar
  // menu.json da "group" va "variant" bo'lgan taomlar bitta kartaga yig'iladi ("6 dona / 10 dona / 1 kg").
  // Har bir variant - alohida taom (o'z ID si va narxi bilan); bot narxni avvalgidek ID bo'yicha hisoblaydi.
  function buildEntries(items) {
    const entries = [];
    const byGroup = new Map();
    for (const it of items) {
      if (it.group && it.variant) {
        let entry = byGroup.get(it.group);
        if (!entry) {
          entry = { key: 'g:' + it.group, title: it.groupTitle || it.group, variants: [] };
          byGroup.set(it.group, entry);
          entries.push(entry);
        }
        entry.variants.push(it);
      } else {
        entries.push({ key: 'i:' + it.id, title: it.name, variants: [it] });
      }
    }
    return entries;
  }

  /** Tanlangan variant: avval tanlangani, bo'lmasa savatdagisi, bo'lmasa birinchisi. */
  function selectedVariant(entry) {
    const chosen = state.variantSel.get(entry.key);
    return entry.variants.find((v) => v.id === chosen)
      || entry.variants.find((v) => state.cart.has(v.id))
      || entry.variants[0];
  }

  function renderItemCard(entry) {
    const it = selectedVariant(entry);
    const cat = state.menu.catById.get(it.category);
    const grouped = entry.variants.length > 1;
    return h('article', { class: 'item-card', dataset: { entry: entry.key, id: it.id } },
      media(it.image, cat ? cat.emoji : '🍽️', it.name, 'item-media', 600, 600),
      // Narx faqat menu.json dagi price dan olinadi va rasm tagida, nom bilan birga ko'rsatiladi
      h('div', { class: 'item-body' },
        h('h3', { class: 'item-name' }, grouped ? entry.title : it.name),
        grouped ? h('div', { class: 'variant-chips', role: 'group', 'aria-label': T('aria.size') },
          entry.variants.map((v) => {
            const qty = state.cart.get(v.id) || 0;
            return h('button', {
              class: 'chip' + (v.id === it.id ? ' active' : ''), type: 'button',
              'aria-pressed': String(v.id === it.id),
              dataset: { action: 'variant', entry: entry.key, id: v.id },
            }, v.variantLabel || v.variant, qty ? h('span', { class: 'chip-qty' }, String(qty)) : null);
          })) : null,
        h('div', { class: 'item-price' }, fmtPrice(it.price)),
        it.desc ? h('p', { class: 'item-desc' }, it.desc) : null,
        it.contents.length ? h('ul', { class: 'item-contents', 'aria-label': T('aria.contents') },
          it.contents.map((c) => h('li', null, c))) : null),
      h('div', { class: 'item-action' }, itemControl(it.id)));
  }

  function selectVariant(entryKey, id) {
    state.variantSel.set(entryKey, id);
    const entry = state.entryByItem.get(id);
    if (entry) updateItemControl(id);
    haptic('light');
  }

  function renderCategory(catId) {
    const cat = state.menu.catById.get(catId);
    if (!cat) {
      $view.append(emptyState('🤷', T('cat.not_found'), null,
        h('button', { class: 'btn btn-secondary', type: 'button', dataset: { action: 'go-home' } }, T('btn.home'))));
      return T('cat.not_found_title');
    }
    const items = state.menu.byCat.get(cat.id) || [];
    if (!items.length) {
      $view.append(emptyState('⏳', T('soon'), T('cat.soon_text', { cat: cat.name }),
        h('button', { class: 'btn btn-secondary', type: 'button', dataset: { action: 'back' } }, T('btn.other_cats'))));
      return cat.name;
    }
    const entries = buildEntries(items);
    state.entryByItem = new Map();
    for (const entry of entries) for (const v of entry.variants) state.entryByItem.set(v.id, entry);
    $view.append(h('div', { class: 'item-grid' }, entries.map(renderItemCard)));
    return cat.name;
  }

  // ---------------------------------------------------------------- savat sahifasi
  function renderCart() {
    if (state.cart.size === 0) {
      $view.append(emptyState('🛒', T('cart.empty'), T('cart.empty_text'),
        h('button', { class: 'btn btn-primary', type: 'button', dataset: { action: 'go-home' } }, T('btn.to_menu'))));
      return T('cart.title');
    }
    const co = state.checkout;

    // Oldingi buyurtma holati haqida eslatma
    let notice = null;
    const pending = lastPendingOrder();
    if (pending && msSince(pending.date) < PENDING_STALE_MS) {
      notice = h('div', { class: 'notice info' }, T('notice.pending', { time: fmtDate(pending.date).slice(-5) }));
    }

    // a) Taomlar
    const linesSection = h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, T('cart.your')),
      h('div', { id: 'cartLines' }),
      h('div', { class: 'sum-row' }, h('span', null, T('sum.items')), h('span', { id: 'itemsTotal' })),
      h('div', { class: 'sum-row' }, h('span', null, T('sum.delivery')), h('span', null, fmtPrice(BUSINESS.deliveryFee))),
      h('div', { class: 'total-row' }, h('span', null, T('sum.total')), h('b', { id: 'cartTotal' })),
      h('p', { class: 'payment-note' }, T('pay.note')));

    // b) Ism
    const nameInput = h('input', {
      id: 'nameInput', class: 'input', type: 'text', maxlength: LIMITS.nameMax,
      autocomplete: 'name', enterkeyhint: 'next', placeholder: T('form.name_ph'), value: co.name,
    });
    nameInput.addEventListener('input', () => { co.name = nameInput.value; validateForm(); });
    nameInput.addEventListener('blur', () => { co.touched.name = true; validateForm(); });

    // c) Telefon: +998 o'zgarmas, faqat 9 ta raqam
    const phoneInput = h('input', {
      id: 'phoneInput', class: 'input', type: 'tel', inputmode: 'numeric', autocomplete: 'tel-national',
      maxlength: 12, placeholder: '90 123 45 67', enterkeyhint: 'next', 'aria-describedby': 'phoneError',
      value: formatPhone(co.phone),
    });
    bindPhoneInput(phoneInput);

    const contactSection = h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, T('form.your_data')),
      h('div', { class: 'field' },
        h('label', { class: 'label', for: 'nameInput' }, `${T('form.name')} `, h('span', { class: 'req' }, '*')),
        nameInput,
        h('p', { class: 'field-error', id: 'nameError', hidden: true })),
      h('div', { class: 'field' },
        h('label', { class: 'label', for: 'phoneInput' }, `${T('form.phone')} `, h('span', { class: 'req' }, '*')),
        h('div', { class: 'phone-field', id: 'phoneField' },
          h('span', { class: 'phone-prefix', 'aria-hidden': 'true' }, '+998'),
          phoneInput),
        h('p', { class: 'field-error', id: 'phoneError', hidden: true }),
        // Telegram'dagi raqamni bir bosishda olish (Telegram 6.9+). Qo'lda yozish ham ochiq qoladi.
        canRequestContact()
          ? h('button', { class: 'btn btn-ghost tg-phone-btn', type: 'button', dataset: { action: 'tg-phone' } },
            T('btn.tg_phone'))
          : null));

    // d) Manzil
    const addrInput = h('textarea', {
      id: 'addrInput', class: 'input textarea', rows: 3, maxlength: LIMITS.addressMax,
      placeholder: T('form.addr_ph'), value: co.address,
    });
    addrInput.addEventListener('input', () => {
      co.address = addrInput.value;
      updateAddressUI();
      validateForm();
    });

    // Uy / qavat / xonadon va izoh - joriy lokatsiya tanlansa ham yoziladi
    const detailsInput = h('input', {
      id: 'detailsInput', class: 'input', type: 'text', maxlength: LIMITS.detailsMax,
      placeholder: T('form.details_ph'), value: co.details,
    });
    detailsInput.addEventListener('input', () => { co.details = detailsInput.value; });
    const commentInput = h('textarea', {
      id: 'commentInput', class: 'input textarea textarea-sm', rows: 2, maxlength: LIMITS.commentMax,
      placeholder: T('form.comment_ph'), value: co.comment,
    });
    commentInput.addEventListener('input', () => { co.comment = commentInput.value; });

    const addressSection = h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, T('form.address_title')),
      h('p', { class: 'field-hint area-note' }, T('form.area_note')),
      h('button', { id: 'locBtn', class: 'btn btn-secondary btn-block', type: 'button', dataset: { action: 'loc-request' } },
        T('btn.send_loc')),
      h('div', { id: 'locStatus', class: 'loc-status', 'aria-live': 'polite' }),
      h('div', { class: 'divider' }, T('form.or')),
      h('div', { class: 'field' },
        h('label', { class: 'label', for: 'addrInput' }, T('form.addr_label'),
          h('span', { class: 'counter', id: 'addrCounter' })),
        addrInput,
        h('p', { class: 'field-hint', id: 'addrHint', hidden: true })),
      h('div', { class: 'field' },
        h('label', { class: 'label', for: 'detailsInput' }, T('form.details')),
        detailsInput),
      h('div', { class: 'field' },
        h('label', { class: 'label', for: 'commentInput' }, T('form.comment')),
        commentInput));

    // e) Tasdiqlash
    const footer = h('div', { class: 'checkout-bar' },
      h('p', { class: 'form-hint', id: 'formHint' }),
      h('button', { id: 'submitBtn', class: 'btn btn-primary btn-block btn-lg', type: 'button', dataset: { action: 'submit' }, disabled: true },
        T('btn.submit')));

    $view.append(...(notice ? [notice] : []), linesSection, contactSection, addressSection, footer);
    renderCartLines();
    updateAddressUI();
    validateForm();
    return T('cart.title');
  }

  function renderCartLines() {
    const $lines = document.getElementById('cartLines');
    if (!$lines) return;
    const rows = [];
    for (const [id, qty] of state.cart) {
      const it = state.menu.byId.get(id);
      if (!it) continue;
      rows.push(h('div', { class: 'line', dataset: { id } },
        h('div', { class: 'line-info' },
          h('div', { class: 'line-name' }, it.name),
          h('div', { class: 'line-price' }, `${fmtPrice(it.price)} × ${qty}`)),
        h('div', { class: 'line-total' }, fmtPrice(it.price * qty)),
        h('div', { class: 'line-controls' },
          stepper(id, qty, true),
          h('button', { class: 'icon-btn danger', type: 'button', dataset: { action: 'remove', id }, 'aria-label': T('aria.remove', { name: it.name }) }, '🗑'))));
    }
    $lines.replaceChildren(...rows);
    const $items = document.getElementById('itemsTotal');
    if ($items) $items.textContent = fmtPrice(cartTotal());
    const $total = document.getElementById('cartTotal');
    if ($total) $total.textContent = fmtPrice(grandTotal());
    validateForm();
  }

  // ---------------------------------------------------------------- telefon maydoni
  /** "901234567" -> "90 123 45 67" */
  function formatPhone(digits) {
    return [digits.slice(0, 2), digits.slice(2, 5), digits.slice(5, 7), digits.slice(7, 9)].filter(Boolean).join(' ');
  }

  /** Formatlangan matnda n-raqamdan keyingi kursor pozitsiyasi */
  function caretAfterDigits(formatted, n) {
    if (n <= 0) return 0;
    let seen = 0;
    for (let i = 0; i < formatted.length; i++) {
      if (/\d/.test(formatted[i]) && ++seen === n) return i + 1;
    }
    return formatted.length;
  }

  function bindPhoneInput(input) {
    const co = state.checkout;
    let prevFormatted = input.value;

    input.addEventListener('input', () => {
      const value = input.value;
      const caret = input.selectionStart === null ? value.length : input.selectionStart;
      let digits = value.replace(/\D/g, '');
      let before = value.slice(0, caret).replace(/\D/g, '').length;

      // +998 bilan birga joylashtirilgan raqam (masalan "+998 90 123 45 67")
      if (digits.length > LIMITS.phoneDigits && digits.startsWith('998')) {
        digits = digits.slice(3);
        before = Math.max(0, before - 3);
      }
      // Faqat bo'sh joy o'chirilgan bo'lsa - undan oldingi raqamni o'chiramiz
      if (digits === co.phone && value.length < prevFormatted.length && before > 0) {
        digits = digits.slice(0, before - 1) + digits.slice(before);
        before -= 1;
      }
      digits = digits.slice(0, LIMITS.phoneDigits);
      before = Math.min(before, digits.length);

      co.phone = digits;
      co.touched.phone = true;
      const formatted = formatPhone(digits);
      input.value = formatted;
      prevFormatted = formatted;
      const pos = caretAfterDigits(formatted, before);
      try { input.setSelectionRange(pos, pos); } catch (e) { /* ba'zi brauzerlar */ }
      validateForm();
    });
    input.addEventListener('blur', () => { co.touched.phone = true; validateForm(); });
  }

  // ---------------------------------------------------------------- telefonni Telegram'dan olish
  function canRequestContact() {
    return tgAtLeast('6.9') && typeof tg.requestContact === 'function';
  }

  /** requestContact javobidan telefon raqami (mijozlar turli Telegram versiyalarida). */
  function contactPhoneFrom(response) {
    try {
      const unsafe = response && response.responseUnsafe;
      if (unsafe && unsafe.contact && unsafe.contact.phone_number) return String(unsafe.contact.phone_number);
      if (response && typeof response.response === 'string') {
        const raw = new URLSearchParams(response.response).get('contact');
        if (raw) return String(JSON.parse(raw).phone_number || '');
      }
    } catch (e) {
      console.warn('Kontakt javobini o\'qib bo\'lmadi', e);
    }
    return '';
  }

  /** '+998 90 123 45 67' / '998901234567' -> '901234567'; O'zbekiston raqami bo'lmasa ''. */
  function uzLocalDigits(phone) {
    const d = String(phone || '').replace(/\D/g, '');
    return d.length === 12 && d.startsWith('998') ? d.slice(3) : '';
  }

  function setPhoneDigits(digits) {
    const $input = document.getElementById('phoneInput');
    state.checkout.phone = digits;
    state.checkout.touched.phone = true;
    if ($input) {
      $input.value = formatPhone(digits);
      $input.dispatchEvent(new Event('input', { bubbles: true })); // formatlash va tekshiruv bir joyda
    } else {
      validateForm();
    }
  }

  function requestTelegramPhone() {
    if (!canRequestContact()) return;
    try {
      tg.requestContact((shared, response) => {
        if (!shared) {
          toast(T('toast.phone_not_shared'), true);
          return;
        }
        const digits = uzLocalDigits(contactPhoneFrom(response));
        if (digits) {
          setPhoneDigits(digits);
          toast(T('toast.phone_set'));
          haptic('success');
        } else if (contactPhoneFrom(response)) {
          toast(T('toast.phone_not_uz'), true);
        } else {
          toast(T('toast.phone_unreadable'), true);
        }
      });
    } catch (e) {
      console.warn('requestContact xatosi', e);
      toast(T('toast.phone_failed'), true);
    }
  }

  // ---------------------------------------------------------------- manzil (joriy / qo'lda)
  function updateAddressUI() {
    const co = state.checkout;
    const $btn = document.getElementById('locBtn');
    const $status = document.getElementById('locStatus');
    const $addr = document.getElementById('addrInput');
    const $hint = document.getElementById('addrHint');
    const $counter = document.getElementById('addrCounter');
    if (!$btn || !$status || !$addr) return;

    const hasText = co.address.length > 0;
    const locActive = co.locStatus === 'ok' || co.locStatus === 'loading';

    // Qo'lda yozilgan bo'lsa - joriy manzil tugmasi bloklanadi
    $btn.hidden = co.locStatus === 'ok';
    $btn.disabled = hasText || co.locStatus === 'loading';
    // Joriy manzil olingan bo'lsa - qo'lda yozish bloklanadi
    $addr.disabled = locActive;
    $addr.placeholder = locActive ? T('loc.ph_active') : T('form.addr_ph');

    $counter.textContent = `${charLen(co.address)}/${LIMITS.addressMax}`;
    if (hasText && !locActive) {
      $hint.hidden = false;
      $hint.textContent = T('loc.hint_clear');
    } else if (co.locStatus === 'ok') {
      $hint.hidden = false;
      $hint.textContent = T('loc.hint_cancel');
    } else {
      $hint.hidden = true;
    }

    let box = null;
    if (co.locStatus === 'loading') {
      box = h('div', { class: 'loc-box info' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }),
        h('span', { class: 'grow' }, T('loc.loading')));
    } else if (co.locStatus === 'ok' && co.loc) {
      box = h('div', { class: 'loc-box ok' },
        h('span', { class: 'grow' }, T('loc.ok'),
          h('span', { class: 'loc-coords' }, `${co.loc.lat.toFixed(5)}, ${co.loc.lon.toFixed(5)}`)),
        h('button', { class: 'btn btn-ghost', type: 'button', dataset: { action: 'loc-cancel' } }, T('btn.cancel')));
    } else if (co.locStatus === 'error') {
      box = h('div', { class: 'loc-box err' },
        h('span', { class: 'grow' }, co.locError),
        co.locCanOpenSettings
          ? h('button', { class: 'btn btn-ghost', type: 'button', dataset: { action: 'loc-settings' } }, T('btn.settings'))
          : null);
    }
    $status.replaceChildren(...(box ? [box] : []));
  }

  function setLocation(status, extra) {
    Object.assign(state.checkout, { locStatus: status, locError: '', locCanOpenSettings: false }, extra || {});
    if (status !== 'ok') state.checkout.loc = null;
    updateAddressUI();
    validateForm();
  }

  function onLocationSuccess(lat, lon) {
    if (state.checkout.locStatus !== 'loading') return; // foydalanuvchi allaqachon bekor qilgan/yozgan
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      onLocationError(T('loc.bad'));
      return;
    }
    // Yetkazish hududi: shahardan tashqaridagi lokatsiya qabul qilinmaydi (bot ham tekshiradi)
    const km = distanceKm(DELIVERY_AREA.lat, DELIVERY_AREA.lon, lat, lon);
    if (km > DELIVERY_AREA.radiusKm) {
      onLocationError(T('loc.out_of_area', { km: Math.round(km) }));
      return;
    }
    state.checkout.loc = { lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lon * 1e6) / 1e6 };
    setLocation('ok', { loc: state.checkout.loc });
    haptic('success');
  }

  /** Ikki nuqta orasidagi masofa, km (haversine; botdagi bilan bir xil formula). */
  function distanceKm(lat1, lon1, lat2, lon2) {
    const rad = (d) => (d * Math.PI) / 180;
    const dp = rad(lat2 - lat1);
    const dl = rad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  function onLocationError(message, canOpenSettings) {
    if (state.checkout.locStatus !== 'loading') return;
    setLocation('error', { locError: message, locCanOpenSettings: !!canOpenSettings });
    haptic('error');
  }

  let locTimer = 0;
  function requestLocation() {
    const co = state.checkout;
    if (co.address.length > 0 || co.locStatus === 'loading') return;
    setLocation('loading');
    clearTimeout(locTimer);
    locTimer = setTimeout(() => onLocationError(T('loc.timeout')), 25000);

    const lm = tgAtLeast('8.0') ? tg.LocationManager : null;
    if (lm) {
      const ask = () => {
        if (!lm.isLocationAvailable) {
          browserGeolocation();
          return;
        }
        lm.getLocation((data) => {
          clearTimeout(locTimer);
          if (data && Number.isFinite(data.latitude) && Number.isFinite(data.longitude)) {
            onLocationSuccess(data.latitude, data.longitude);
          } else if (lm.isAccessRequested && !lm.isAccessGranted) {
            onLocationError(T('loc.denied_tg'), true);
          } else {
            onLocationError(T('loc.failed'));
          }
        });
      };
      try {
        if (lm.isInited) ask();
        else lm.init(ask);
      } catch (e) {
        console.warn('LocationManager xatosi', e);
        browserGeolocation();
      }
      return;
    }
    browserGeolocation();
  }

  function browserGeolocation() {
    if (!('geolocation' in navigator)) {
      clearTimeout(locTimer);
      onLocationError(T('loc.unsupported'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        clearTimeout(locTimer);
        onLocationSuccess(pos.coords.latitude, pos.coords.longitude);
      },
      (err) => {
        clearTimeout(locTimer);
        const messages = { 1: 'loc.denied', 2: 'loc.no_gps', 3: 'loc.gps_timeout' };
        onLocationError(T(messages[err && err.code] || 'loc.failed'));
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
  }

  function cancelLocation() {
    clearTimeout(locTimer);
    setLocation('idle');
  }

  function openLocationSettings() {
    try {
      if (tgAtLeast('8.0') && tg.LocationManager) tg.LocationManager.openSettings();
    } catch (e) { /* e'tiborsiz */ }
  }

  // ---------------------------------------------------------------- forma tekshiruvi
  function validateForm() {
    const co = state.checkout;
    const name = co.name.trim().replace(/\s+/g, ' ');
    const nameLen = charLen(name);
    const nameOk = nameLen >= LIMITS.nameMin && nameLen <= LIMITS.nameMax;
    const phoneOk = /^\d{9}$/.test(co.phone);
    const addressText = co.address.trim();
    const addressOk = co.locStatus === 'ok' && !!co.loc
      ? true
      : addressText.length > 0 && charLen(addressText) <= LIMITS.addressMax;
    const cartOk = state.cart.size > 0;
    const open = isOpenNow(); // yopiq vaqtda savat ishlaydi, faqat yuborish o'chiriladi
    const ok = nameOk && phoneOk && addressOk && cartOk && open;

    const $nameErr = document.getElementById('nameError');
    const $nameInput = document.getElementById('nameInput');
    if ($nameErr && $nameInput) {
      const show = co.touched.name && !nameOk;
      $nameErr.hidden = !show;
      $nameErr.textContent = show ? T('err.name', { min: LIMITS.nameMin, max: LIMITS.nameMax }) : '';
      $nameInput.classList.toggle('invalid', show);
    }

    const $phoneErr = document.getElementById('phoneError');
    const $phoneField = document.getElementById('phoneField');
    if ($phoneErr && $phoneField) {
      const show = co.touched.phone && !phoneOk;
      const left = LIMITS.phoneDigits - co.phone.length;
      $phoneErr.hidden = !show;
      $phoneErr.textContent = show ? T('err.phone', { left }) : '';
      $phoneField.classList.toggle('invalid', show);
    }

    const $submit = document.getElementById('submitBtn');
    const $hint = document.getElementById('formHint');
    if ($submit) $submit.disabled = !ok || co.submitting;
    if ($hint) {
      const missing = [];
      if (!nameOk) missing.push(T('hint.f_name'));
      if (!phoneOk) missing.push(T('hint.f_phone'));
      if (!addressOk) missing.push(T('hint.f_address'));
      if (!open) $hint.textContent = T('hint.closed', { open: BUSINESS.openFrom });
      else $hint.textContent = missing.length ? T('hint.fill', { list: missing.join(', ') }) : T('hint.total', { sum: fmtPrice(grandTotal()) });
    }
    return { ok, name, addressText };
  }

  // ---------------------------------------------------------------- yuborish
  function setSubmitting(value) {
    state.checkout.submitting = value;
    const $submit = document.getElementById('submitBtn');
    if ($submit) $submit.textContent = value ? T('btn.sending') : T('btn.submit');
    validateForm();
  }

  function submitOrder() {
    const co = state.checkout;
    if (co.submitting) return; // ikki marta bosishdan himoya
    const v = validateForm();
    if (!v.ok) return;
    if (!isOpenNow()) { // tugma o'chiq bo'lsa ham - ehtiyot uchun
      toast(T('toast.closed', { open: BUSINESS.openFrom, close: BUSINESS.closeAt }), true);
      return;
    }

    // Bot 30 soniya ichidagi ikkinchi buyurtmani rad etadi - mijozni oldindan ogohlantiramiz
    const pending = lastPendingOrder();
    const sinceLast = pending ? msSince(pending.date) : Infinity;
    if (sinceLast < ORDER_COOLDOWN_MS) {
      toast(T('toast.cooldown', { sec: Math.ceil((ORDER_COOLDOWN_MS - sinceLast) / 1000) }), true);
      return;
    }
    setSubmitting(true);

    const localId = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    const useLocation = co.locStatus === 'ok' && !!co.loc;
    const lines = Array.from(state.cart).filter(([id]) => state.menu.byId.has(id));
    // Narx va jami summa YUBORILMAYDI - bot ularni menu.json dan hisoblaydi
    const payload = {
      ref: localId, // bot javobini tarixdagi yozuv bilan bog'lash uchun
      lang: LANG, // til tanlamagan (eski) mijozga bot shu tilda javob beradi va uni saqlaydi
      name: v.name,
      phone: '+998' + co.phone,
      address_type: useLocation ? 'location' : 'text',
      address_text: useLocation ? '' : v.addressText,
      lat: useLocation ? co.loc.lat : null,
      lon: useLocation ? co.loc.lon : null,
      address_details: co.details.trim(),
      comment: co.comment.trim(),
      items: lines.map(([id, qty]) => ({ id, qty })),
    };
    const json = JSON.stringify(payload);
    if (new TextEncoder().encode(json).length > LIMITS.payloadMaxBytes) {
      toast(T('toast.too_big'), true);
      setSubmitting(false);
      return;
    }
    if (!inTelegram && !DEBUG) {
      toast(T('toast.only_tg'), true);
      setSubmitting(false);
      return;
    }

    // 1) Tarixga "tasdiq kutilmoqda" holatida yozamiz. Savat TOZALANMAYDI:
    //    bot qabul qilgani haqidagi javob kelgandagina (keyingi ochilishda) tozalanadi.
    const record = {
      localId,
      date: new Date().toISOString(),
      items: lines.map(([id, qty]) => {
        const it = state.menu.byId.get(id);
        return { id, name: it.name, qty, price: it.price };
      }),
      // Summalar snapshot'i (bot ham xuddi shunday hisoblaydi): keyin menyu narxi o'zgarsa ham o'zgarmaydi
      itemsTotal: cartTotal(),
      deliveryFee: BUSINESS.deliveryFee,
      total: grandTotal(),
      payment: 'naqd',
      addressType: payload.address_type,
      // Lokatsiya bo'lsa matn saqlanmaydi - "Joriy manzil" yozuvi ko'rsatishda tanlangan tilda chiqadi
      addressText: useLocation ? '' : v.addressText,
      status: 'pending',
    };
    saveOrder(record);
    // Forma qoralamasi: rad etilsa yoki javob kelmasa qayta ochilganda tiklanadi
    saveDraft(localId);

    // 2) Botga yuborish (Telegram Mini App'ni yopadi)
    try {
      if (inTelegram) {
        tg.sendData(json);
      } else {
        console.info('[DEBUG] sendData payload:', json, `(${new TextEncoder().encode(json).length} bayt)`);
      }
    } catch (e) {
      console.error('sendData xatosi', e);
      removeOrder(record.localId);
      setSubmitting(false);
      toast(T('toast.send_failed', { btn: T('btn.order_bot') }), true);
      haptic('error');
      return;
    }

    haptic('success');
    co.submitting = false;
    if (inTelegram) {
      try { tg.close(); } catch (e) { /* sendData odatda o'zi yopadi */ }
    } else {
      toast(T('toast.debug'));
      state.menuStack = state.stack;
      state.tab = 'orders';
      state.stack = [{ name: 'orders' }];
      render(true);
    }
  }

  // ---------------------------------------------------------------- buyurtmalarim
  function orderStatusBadge(o) {
    if (o.status === 'ok') {
      return h('p', { class: 'order-status ok' }, o.orderNo ? T('status.ok_n', { n: o.orderNo }) : T('status.ok'));
    }
    if (o.status === 'cancelled') {
      return h('p', { class: 'order-status err' }, o.orderNo ? T('status.cancelled_n', { n: o.orderNo }) : T('status.cancelled'));
    }
    if (o.status === 'rejected') {
      // Yangi yozuvlarda sabab kodi (tanlangan tilda); eski yozuvlarda saqlangan tayyor matn
      const reason = o.reasonCode ? rejectReason(o.reasonCode) : (o.reason || rejectReason('data'));
      return h('p', { class: 'order-status err' }, T('status.rejected', { reason }));
    }
    if (o.status === 'pending') {
      return msSince(o.date) < PENDING_STALE_MS
        ? h('p', { class: 'order-status wait' }, T('status.pending'))
        : h('p', { class: 'order-status wait' }, T('status.stale'));
    }
    return null; // eski yozuvlar uchun holat noma'lum
  }

  /** Tarixdagi taom nomi: menu.json dan ID bo'yicha (nom o'zbekcha); menyuda yo'q bo'lsa - saqlangan nom. */
  function orderItemName(i) {
    const it = state.menu && state.menu.byId.get(i.id);
    return it ? it.name : i.name;
  }

  function paymentLabel(p) {
    return p === 'naqd' ? T('pay.cash') : p;
  }

  /**
   * Buyurtma summalari faqat tarixdagi snapshot'dan (menu.json dan emas).
   * Eski yozuvlarda yo'q qatorlar o'tkazib yuboriladi.
   */
  function orderSums(o) {
    // Taomlar jami: saqlangan bo'lsa shu; eski yozuvda - saqlangan narxlardan (hamma qatorda narx bo'lsa)
    let itemsTotal = o.itemsTotal;
    if (itemsTotal === null && o.items.length && o.items.every((i) => i.price !== null)) {
      itemsTotal = o.items.reduce((s, i) => s + i.price * i.qty, 0);
    }
    const rows = [];
    if (itemsTotal !== null) rows.push(h('div', { class: 'os-row' }, h('span', null, T('os.items')), h('span', null, fmtPrice(itemsTotal))));
    if (o.deliveryFee !== null) rows.push(h('div', { class: 'os-row' }, h('span', null, T('os.delivery')), h('span', null, fmtPrice(o.deliveryFee))));
    if (o.total !== null) rows.push(h('div', { class: 'os-row os-total' }, h('span', null, T('os.total')), h('b', null, fmtPrice(o.total))));
    if (o.payment) rows.push(h('div', { class: 'os-row' }, h('span', null, T('os.payment', { method: paymentLabel(o.payment) }))));
    return rows.length ? h('div', { class: 'order-sums' }, rows) : null;
  }

  function renderOrders() {
    const orders = getOrders();
    if (!orders.length) {
      $view.append(emptyState('📋', T('orders.empty'), T('orders.empty_text'),
        h('button', { class: 'btn btn-primary', type: 'button', dataset: { action: 'tab-menu' } }, T('btn.to_menu'))));
      return T('orders.title');
    }
    $view.append(...orders.map((o) => h('article', { class: 'card order-card' },
      h('div', { class: 'order-head' },
        h('span', { class: 'order-date' }, fmtDate(o.date))),
      orderStatusBadge(o),
      h('ul', { class: 'order-items' }, o.items.map((i) =>
        // "Nom × 2 — 60 000 so'm": uzun nom o'raladi, "× 2 — summa" esa bo'linmaydi
        h('li', null,
          h('span', { class: 'oi-name' }, `${orderItemName(i)} `),
          h('span', { class: 'oi-tail' }, `× ${i.qty}`,
            i.price !== null ? h('span', { class: 'oi-sum' }, ` — ${fmtPrice(i.price * i.qty)}`) : null)))),
      orderSums(o),
      o.addressType === 'location'
        ? h('p', { class: 'order-addr' }, T('orders.current_location'))
        : (o.addressText ? h('p', { class: 'order-addr' }, `📍 ${o.addressText}`) : null),
      h('button', { class: 'btn btn-secondary btn-block', type: 'button', dataset: { action: 'reorder', id: o.localId } },
        T('btn.reorder')))));
    return T('orders.title');
  }

  // ---------------------------------------------------------------- hodisalar
  document.addEventListener('click', (event) => {
    const el = event.target.closest('[data-action]');
    if (!el || el.disabled) return;
    const { action, id } = el.dataset;
    switch (action) {
      case 'open-cat': navigate({ name: 'category', id }); break;
      case 'open-cart':
        if (state.tab === 'orders') openInMenu({ name: 'cart' });
        else navigate({ name: 'cart' });
        break;
      case 'tab-menu': switchTab('menu'); break;
      case 'tab-orders': switchTab('orders'); break;
      case 'go-home': goHome(); break;
      case 'back': goBack(); break;
      case 'add': setQty(id, 1); break;
      case 'inc': setQty(id, (state.cart.get(id) || 0) + 1); break;
      case 'dec': setQty(id, (state.cart.get(id) || 0) - 1); break;
      case 'remove': setQty(id, 0); break;
      case 'reorder': reorder(id); break;
      case 'variant': selectVariant(el.dataset.entry, id); break;
      case 'tg-phone': requestTelegramPhone(); break;
      case 'loc-request': requestLocation(); break;
      case 'loc-cancel': cancelLocation(); break;
      case 'loc-settings': openLocationSettings(); break;
      case 'submit': submitOrder(); break;
      case 'retry': init(); break;
      default: break;
    }
  });

  // ---------------------------------------------------------------- Telegram sozlash
  function setupTelegram() {
    if (!inTelegram) return;
    try {
      tg.ready();
      tg.expand();
      if (tgAtLeast('6.1')) {
        tg.setHeaderColor('#0F1115');
        tg.setBackgroundColor('#0F1115');
      }
      if (tgAtLeast('7.10') && tg.setBottomBarColor) tg.setBottomBarColor('#0F1115');
      if (tgAtLeast('7.7') && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      if (useTgBackButton) tg.BackButton.onClick(goBack);
    } catch (e) {
      console.warn('Telegram sozlashda xato', e);
    }
  }

  // ---------------------------------------------------------------- ishga tushirish
  async function init() {
    $view.replaceChildren(h('div', { class: 'loader', 'aria-label': T('loading') }, h('span'), h('span'), h('span')));
    try {
      state.menu = await loadMenu();
    } catch (e) {
      console.error('Menyu yuklanmadi', e);
      $view.replaceChildren(emptyState('😕', T('err.menu'), T('err.menu_text'),
        h('button', { class: 'btn btn-primary', type: 'button', dataset: { action: 'retry' } }, T('btn.retry'))));
      return;
    }
    state.cart = loadCart();
    const removed = pruneCart();
    loadDraft();
    applyBotResult();
    // Natija indamasdan qo'llangan - ilova odatdagidek menyudan ochiladi
    state.menuStack = null;
    state.tab = 'menu';
    state.stack = [{ name: 'home' }];
    render(true);
    if (removed) toast(T('toast.pruned'), true);
    startStatusPolling(); // "kutilmoqda" buyurtma bo'lsa - holatni serverdan so'rash
  }

  // ---------------------------------------------------------------- ish vaqti banneri
  let lastOpen = null;
  function updateHoursBanner() {
    const open = isOpenNow();
    if (open === lastOpen) return;
    lastOpen = open;
    const $banner = document.getElementById('hoursBanner');
    if ($banner) {
      $banner.hidden = open;
      $banner.textContent = open ? '' : T('banner.closed', { open: BUSINESS.openFrom, close: BUSINESS.closeAt });
    }
    validateForm(); // yuborish tugmasini yoqish/o'chirish
  }

  // ---------------------------------------------------------------- index.html dagi doimiy yozuvlar
  /** data-i18n (matn) va data-i18n-aria (aria-label) atributli elementlarni tanlangan tilga o'tkazadi. */
  function applyStaticTexts() {
    document.documentElement.lang = LANG;
    for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = T(el.dataset.i18n);
    for (const el of document.querySelectorAll('[data-i18n-aria]')) el.setAttribute('aria-label', T(el.dataset.i18nAria));
  }

  applyStaticTexts();
  setupTelegram();
  updateHoursBanner();
  setInterval(updateHoursBanner, 30 * 1000); // ochilish/yopilish vaqtida ilova ochiq tursa ham yangilanadi
  init();
})();
