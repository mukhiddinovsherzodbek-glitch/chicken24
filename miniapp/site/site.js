/* Chicken24 sayt: menyu (/menu.json), savat, buyurtma (POST {API_BASE}/api/web-orders). Vanilla JS, kutubxonasiz. */
(function () {
  'use strict';
  const CFG = window.SITE_CONFIG;
  const $ = (id) => document.getElementById(id);

  const T = {
    uz: {
      'nav.menu': 'Menyu', 'nav.info': 'Yetkazish', 'cart.btn': 'Savat', 'cart.title': 'Savatingiz', 'cart.go': "Savatga o'tish",
      'hero.hours': 'Har kuni 10:00 dan 03:00 gacha', 'hero.t1': 'Issiq. Qarsildoq.', 'hero.t2': 'Eshigingizgacha.',
      'hero.p': "Burger, lavash, pitsa va tovuq. Saytdan buyurtma bering, biz yetkazamiz. To'lov naqd.",
      'hero.cta': 'Buyurtma berish', 'hero.fee': "Yetkazish 10 000 so'mdan · Minimal buyurtma yo'q",
      'info.title': "Yetkazish va to'lov", 'info.hours': 'Ish vaqti: har kuni 10:00 dan 03:00 gacha',
      'info.pay': "To'lov: naqd, buyurtma yetkazilganda", 'info.fee': "Yetkazish narxi: 10 000 so'mdan",
      sum: "so'm", add: "Savatga qo'shish", minus: 'Kamaytirish', plus: "Ko'paytirish", close: 'Yopish',
      empty: "Savat bo'sh. Menyudan taom tanlang.", items: 'Taomlar', fee: 'Yetkazish', total: 'Jami',
      name: 'Ismingiz', phone: 'Telefon raqamingiz', addr: 'Manzil', byLoc: 'Joylashuvim', byText: 'Matn bilan',
      getLoc: 'Joylashuvimni aniqlash', locOk: 'Joylashuv olindi', locWait: 'Aniqlanmoqda...',
      locFail: "Joylashuv olinmadi. Ruxsat bering yoki manzilni matn bilan yozing.",
      addrText: "Manzil (ko'cha, uy, mo'ljal)", details: 'Uy / qavat / xonadon (ixtiyoriy)', comment: 'Izoh (ixtiyoriy)',
      pay: "To'lov: naqd, yetkazilganda", send: 'Buyurtmani yuborish', sending: 'Yuborilmoqda...',
      eName: 'Ismingizni yozing', ePhone: "Telefon raqamini to'g'ri kiriting (masalan 90 123 45 67)",
      eLoc: 'Joylashuvni aniqlang yoki manzilni yozing', eCaptcha: 'Iltimos, tekshiruvdan o\'ting',
      eNet: "Server javob bermadi. Savat saqlangan, qayta urinib ko'ring.", eMany: "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.",
      eCap: "Tekshiruvdan o'tilmadi. Qayta urinib ko'ring.", eRej: "Buyurtma qabul qilinmadi.",
      okTitle: 'Buyurtma qabul qilindi!', okNum: 'Buyurtma raqami', okText: 'Buyurtmangiz restoranga yuborildi. Telefoningiz yoqilgan bo\'lsin.',
      again: 'Yangi buyurtma', closed: 'Hozir yopiqmiz. Ish vaqti: 10:00 – 03:00. Buyurtmani ish vaqtida yuborishingiz mumkin.',
      menuErr: "Menyu yuklanmadi. Sahifani yangilang.", loading: 'Yuklanmoqda...',
    },
    ru: {
      'nav.menu': 'Меню', 'nav.info': 'Доставка', 'cart.btn': 'Корзина', 'cart.title': 'Ваша корзина', 'cart.go': 'Перейти в корзину',
      'hero.hours': 'Ежедневно с 10:00 до 03:00', 'hero.t1': 'Горячо. Хрустяще.', 'hero.t2': 'Прямо к вашей двери.',
      'hero.p': 'Бургеры, лаваш, пицца и курица. Заказывайте на сайте, мы доставим. Оплата наличными.',
      'hero.cta': 'Заказать', 'hero.fee': 'Доставка от 10 000 сум · Без минимальной суммы',
      'info.title': 'Доставка и оплата', 'info.hours': 'Время работы: ежедневно с 10:00 до 03:00',
      'info.pay': 'Оплата: наличными при получении', 'info.fee': 'Стоимость доставки: от 10 000 сум',
      sum: 'сум', add: 'Добавить в корзину', minus: 'Уменьшить', plus: 'Увеличить', close: 'Закрыть',
      empty: 'Корзина пуста. Выберите блюда в меню.', items: 'Блюда', fee: 'Доставка', total: 'Итого',
      name: 'Ваше имя', phone: 'Ваш телефон', addr: 'Адрес', byLoc: 'Моя геолокация', byText: 'Текстом',
      getLoc: 'Определить местоположение', locOk: 'Местоположение получено', locWait: 'Определяем...',
      locFail: 'Не удалось определить. Разрешите доступ или напишите адрес текстом.',
      addrText: 'Адрес (улица, дом, ориентир)', details: 'Дом / этаж / квартира (необязательно)', comment: 'Комментарий (необязательно)',
      pay: 'Оплата: наличными при получении', send: 'Отправить заказ', sending: 'Отправляем...',
      eName: 'Введите имя', ePhone: 'Введите номер правильно (например 90 123 45 67)',
      eLoc: 'Определите местоположение или напишите адрес', eCaptcha: 'Пройдите проверку',
      eNet: 'Сервер не ответил. Корзина сохранена, попробуйте ещё раз.', eMany: 'Слишком много попыток. Попробуйте чуть позже.',
      eCap: 'Проверка не пройдена. Попробуйте ещё раз.', eRej: 'Заказ не принят.',
      okTitle: 'Заказ принят!', okNum: 'Номер заказа', okText: 'Ваш заказ отправлен в ресторан. Держите телефон включённым.',
      again: 'Новый заказ', closed: 'Сейчас закрыто. Время работы: 10:00 – 03:00. Заказ можно отправить в рабочее время.',
      menuErr: 'Меню не загрузилось. Обновите страницу.', loading: 'Загрузка...',
    },
  };

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* shaxsiy rejim */ } },
  };

  let lang = store.get('c24_lang', /^ru/i.test(navigator.language) ? 'ru' : 'uz');
  let menu = { categories: [], items: [] };
  let byId = new Map();
  let cart = store.get('c24_cart', {});        // { itemId: qty }
  let form = store.get('c24_form', { name: '', phone: '', mode: 'text', text: '', details: '', comment: '' });
  let geo = null;                               // { lat, lon }
  let geoState = '';                            // '', 'wait', 'ok', 'fail'
  let view = 'cart';                            // 'cart' | 'done'
  let done = null;
  let errMsg = '';
  let sending = false;
  let pendingRef = null;                        // tarmoq xatosidan keyin AYNAN SHU ref qayta yuboriladi (idempotent)
  let tsToken = '', tsWidget = null;

  const t = (k) => (T[lang] && T[lang][k]) || T.uz[k] || k;
  const money = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const sum = (n) => `${money(n)} ${t('sum')}`;

  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null) n.append(kid);
    return n;
  }

  const catName = (c) => (lang === 'ru' && c.name_ru) || c.name;
  const itemDesc = (i) => {
    const list = lang === 'ru' ? (i.items_ru || i.items) : i.items;
    if (Array.isArray(list) && list.length) return list.join(' · ');
    return (lang === 'ru' && i.desc_ru) || i.desc || '';
  };
  const imgUrl = (p) => '/' + String(p || '').replace(/^\/+/, '');

  // ---------------------------------------------------------------- savat
  function saveCart() { store.set('c24_cart', cart); }
  function setQty(id, q) {
    if (!byId.has(id)) return;
    q = Math.max(0, Math.min(99, q));
    if (q === 0) delete cart[id]; else cart[id] = q;
    saveCart(); renderCounters(); renderMenuButtons(); if (view === 'cart' && $('cartDlg').open) renderCart();
  }
  const lines = () => Object.entries(cart).filter(([id]) => byId.has(id)).map(([id, q]) => ({ item: byId.get(id), qty: q }));
  const itemsTotal = () => lines().reduce((s, l) => s + l.item.price * l.qty, 0);
  const countAll = () => lines().reduce((s, l) => s + l.qty, 0);

  function renderCounters() {
    const n = countAll();
    $('cartCount').textContent = String(n);
    const bar = $('cartBar');
    bar.hidden = n === 0;
    bar.classList.toggle('on', n > 0);
    $('cartBarTotal').textContent = sum(itemsTotal());
  }

  // ---------------------------------------------------------------- statik matnlar
  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach((n) => { n.textContent = t(n.dataset.i18n); });
    document.querySelectorAll('.lang button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
    $('closeCart').setAttribute('aria-label', t('close'));
    renderMenu(); renderCounters(); updateBanner();
    if ($('cartDlg').open) renderCart();
  }

  // ---------------------------------------------------------------- menyu
  function renderMenu() {
    const host = $('menu'), cats = $('cats');
    host.replaceChildren(); cats.replaceChildren();
    if (!menu.items.length) { host.append(el('div', { class: 'muted', id: 'menuState', text: t(menuFailed ? 'menuErr' : 'loading') })); return; }
    for (const c of menu.categories) {
      const its = menu.items.filter((i) => i.category === c.id);
      if (!its.length) continue;
      cats.append(el('a', { href: '#cat-' + c.id, text: catName(c) }));
      host.append(el('section', { class: 'sec', id: 'cat-' + c.id },
        el('h2', { text: catName(c) }),
        el('div', { class: 'grid' }, its.map(card))));
    }
  }
  function card(i) {
    const d = itemDesc(i);
    return el('article', { class: 'card', 'data-id': i.id },
      el('img', { src: imgUrl(i.image), alt: '', loading: 'lazy', width: 400, height: 300 }),
      el('div', { class: 'card-b' },
        el('h3', { text: i.name }),
        d ? el('p', { text: d }) : null,
        el('div', { class: 'card-f' }, el('span', { class: 'price', text: sum(i.price) }), el('div', { class: 'ctl' }))));
  }
  function renderMenuButtons() {
    document.querySelectorAll('.card').forEach((c) => {
      const id = c.dataset.id, q = cart[id] || 0, ctl = c.querySelector('.ctl');
      ctl.replaceChildren(q === 0
        ? el('button', { type: 'button', class: 'add', 'aria-label': `${t('add')}: ${byId.get(id).name}`, text: '+', onclick: () => setQty(id, 1) })
        : stepper(id, q));
    });
  }
  function stepper(id, q) {
    return el('div', { class: 'step' },
      el('button', { type: 'button', 'aria-label': t('minus'), text: '−', onclick: () => setQty(id, (cart[id] || 0) - 1) }),
      el('b', { text: String(q) }),
      el('button', { type: 'button', 'aria-label': t('plus'), text: '+', onclick: () => setQty(id, (cart[id] || 0) + 1) }));
  }

  // ---------------------------------------------------------------- ish vaqti (Toshkent, 10:00-03:00)
  function isOpen() {
    try {
      const p = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Tashkent', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date());
      const h = Number(p.find((x) => x.type === 'hour').value) % 24;
      return h >= 10 || h < 3;
    } catch { return true; }
  }
  function updateBanner() {
    const b = $('closedBanner');
    b.hidden = isOpen();
    b.textContent = b.hidden ? '' : t('closed');
  }

  // ---------------------------------------------------------------- savat oynasi
  function renderCart() {
    const body = $('cartBody');
    body.replaceChildren();
    if (view === 'done') return renderDone(body);
    const ls = lines();
    if (!ls.length) { body.append(el('p', { class: 'muted', text: t('empty') })); return; }
    const total = itemsTotal(), fee = CFG.DELIVERY_FEE;
    body.append(
      el('div', {}, ls.map(({ item, qty }) => el('div', { class: 'line' },
        el('div', { class: 'nm' }, el('span', { text: item.name }), el('small', { text: sum(item.price) })),
        stepper(item.id, qty)))),
      el('div', { class: 'sum' }, el('span', { text: t('items') }), el('span', { text: sum(total) })),
      el('div', { class: 'sum muted' }, el('span', { text: t('fee') }), el('span', { text: sum(fee) })),
      el('div', { class: 'sum big' }, el('span', { text: t('total') }), el('span', { text: sum(total + fee) })),
      checkoutForm());
    mountCaptcha();
  }

  function checkoutForm() {
    const f = el('form', { novalidate: true, onsubmit: onSubmit });
    const name = el('input', { type: 'text', id: 'fName', autocomplete: 'name', maxlength: 50, value: form.name, required: true,
      oninput: (e) => { form.name = e.target.value; store.set('c24_form', form); } });
    const phone = el('input', { type: 'tel', id: 'fPhone', autocomplete: 'tel', inputmode: 'tel', placeholder: '+998 90 123 45 67', value: form.phone, required: true,
      oninput: (e) => { form.phone = e.target.value; store.set('c24_form', form); } });
    const radio = (v, label) => el('label', {}, el('input', { type: 'radio', name: 'mode', value: v, checked: form.mode === v,
      onchange: () => { form.mode = v; store.set('c24_form', form); renderCart(); } }), label);
    const locBox = el('div', {},
      el('button', { type: 'button', class: 'btn-sec', onclick: getLocation, text: t('getLoc') }),
      el('div', { class: geoState === 'fail' ? 'err' : 'muted', role: 'status',
        text: geoState === 'ok' ? t('locOk') : geoState === 'wait' ? t('locWait') : geoState === 'fail' ? t('locFail') : '' }));
    const text = el('textarea', { id: 'fText', maxlength: 300, 'aria-label': t('addrText'), placeholder: t('addrText'),
      oninput: (e) => { form.text = e.target.value; store.set('c24_form', form); } });
    text.value = form.text;
    const details = el('input', { type: 'text', id: 'fDet', maxlength: 100, value: form.details,
      oninput: (e) => { form.details = e.target.value; store.set('c24_form', form); } });
    const comment = el('textarea', { id: 'fCom', maxlength: 200,
      oninput: (e) => { form.comment = e.target.value; store.set('c24_form', form); } });
    comment.value = form.comment;
    f.append(
      el('label', {}, t('name'), name),
      el('label', {}, t('phone'), phone),
      el('fieldset', {}, el('legend', { text: t('addr') }),
        el('div', { class: 'seg' }, radio('location', t('byLoc')), radio('text', t('byText'))),
        form.mode === 'location' ? locBox : text),
      el('label', {}, t('details'), details),
      el('label', {}, t('comment'), comment),
      CFG.TURNSTILE_SITEKEY ? el('div', { id: 'tsBox' }) : null,
      el('div', { class: 'err', id: 'formErr', role: 'alert', text: errMsg }),
      el('div', { class: 'muted', text: t('pay') }),
      el('button', { type: 'submit', class: 'btn', id: 'sendBtn', disabled: sending || !isOpen(), text: sending ? t('sending') : t('send') }));
    return f;
  }

  function getLocation() {
    if (!navigator.geolocation) { geoState = 'fail'; return renderCart(); }
    geoState = 'wait'; renderCart();
    navigator.geolocation.getCurrentPosition(
      (p) => { geo = { lat: p.coords.latitude, lon: p.coords.longitude }; geoState = 'ok'; renderCart(); },
      () => { geo = null; geoState = 'fail'; renderCart(); },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }

  function normPhone(v) {
    const d = String(v).replace(/\D/g, '');
    if (d.length === 9) return '+998' + d;
    if (d.length === 12 && d.startsWith('998')) return '+' + d;
    return null;
  }
  function newRef() {
    const a = new Uint8Array(16); crypto.getRandomValues(a);
    return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  // ---------------------------------------------------------------- Turnstile (ixtiyoriy)
  function mountCaptcha() {
    const box = $('tsBox');
    if (!box || !CFG.TURNSTILE_SITEKEY) return;
    const render = () => {
      tsToken = '';
      tsWidget = window.turnstile.render(box, { sitekey: CFG.TURNSTILE_SITEKEY, theme: 'dark', callback: (tk) => { tsToken = tk; },
        'expired-callback': () => { tsToken = ''; }, 'error-callback': () => { tsToken = ''; } });
    };
    if (window.turnstile) return render();
    if (!document.getElementById('tsScript')) {
      const s = document.createElement('script');
      s.id = 'tsScript'; s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; s.async = true;
      s.onload = () => { if ($('tsBox')) render(); };
      document.head.append(s);
    }
  }

  // ---------------------------------------------------------------- yuborish
  async function onSubmit(e) {
    e.preventDefault();
    if (sending) return;
    errMsg = '';
    const phone = normPhone(form.phone), name = form.name.trim();
    if (!name) return fail(t('eName'));
    if (!phone) return fail(t('ePhone'));
    if (form.mode === 'location' && !geo) return fail(t('eLoc'));
    if (form.mode === 'text' && !form.text.trim()) return fail(t('eLoc'));
    if (CFG.TURNSTILE_SITEKEY && !tsToken) return fail(t('eCaptcha'));
    const items = lines().map((l) => ({ id: l.item.id, qty: l.qty }));
    if (!items.length) return;
    const ref = pendingRef || (pendingRef = newRef());
    const body = { ref, lang, name, phone, items, address_details: form.details.trim() || null, comment: form.comment.trim() || null };
    if (form.mode === 'location') Object.assign(body, { address_type: 'location', lat: geo.lat, lon: geo.lon });
    else Object.assign(body, { address_type: 'text', address_text: form.text.trim() });
    if (CFG.TURNSTILE_SITEKEY) body.cf_token = tsToken;

    sending = true; renderCart();
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 60000);   // Render uyg'onishi uchun uzoqroq
    try {
      const r = await fetch(CFG.API_BASE + '/api/web-orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctl.signal });
      if (r.status === 429) { errMsg = t('eMany'); }
      else if (r.status === 403) { errMsg = t('eCap'); pendingRef = null; }
      else if (!r.ok) { errMsg = t('eNet'); }                    // ref saqlanadi: qayta urinish takroriy buyurtma bermaydi
      else {
        const j = await r.json();
        pendingRef = null;
        if (j.status === 'accepted') { cart = {}; saveCart(); done = j.n; view = 'done'; renderCounters(); renderMenuButtons(); }
        else errMsg = j.message || t('eRej');
      }
    } catch { errMsg = t('eNet'); }
    finally { clearTimeout(timer); sending = false; if (tsWidget != null && window.turnstile) { try { window.turnstile.reset(tsWidget); } catch { /* */ } } tsToken = ''; renderCart(); }
  }
  function fail(msg) { errMsg = msg; const n = $('formErr'); if (n) n.textContent = msg; }

  function renderDone(body) {
    body.append(el('div', { class: 'ok-box' },
      el('div', { class: 'tick', 'aria-hidden': 'true', text: '✓' }),
      el('h3', { text: t('okTitle') }),
      done ? el('div', { text: `${t('okNum')}: #${done}` }) : null,
      el('p', { class: 'muted', text: t('okText') }),
      el('button', { type: 'button', class: 'btn', text: t('again'), onclick: () => { view = 'cart'; done = null; $('cartDlg').close(); } })));
  }

  // ---------------------------------------------------------------- ishga tushirish
  let menuFailed = false;
  function openCart() { if (view !== 'done') errMsg = ''; renderCart(); const d = $('cartDlg'); if (!d.open) d.showModal(); }

  async function init() {
    document.querySelectorAll('.lang button').forEach((b) => b.addEventListener('click', () => { lang = b.dataset.lang; store.set('c24_lang', lang); applyLang(); }));
    $('openCart').addEventListener('click', openCart);
    $('cartBar').addEventListener('click', openCart);
    $('closeCart').addEventListener('click', () => $('cartDlg').close());
    $('cartDlg').addEventListener('click', (e) => { if (e.target === $('cartDlg')) $('cartDlg').close(); });
    applyLang();
    // Render bepul rejimda uxlab qolishi mumkin: sahifa ochilishi bilan serverni uyg'otamiz (javob kerak emas)
    try { fetch(CFG.API_BASE + '/health', { mode: 'no-cors' }); } catch { /* */ }
    try {
      const r = await fetch('/menu.json', { cache: 'no-cache' });
      if (!r.ok) throw new Error(String(r.status));
      const m = await r.json();
      menu = { categories: m.categories || [], items: m.items || [] };
      byId = new Map(menu.items.map((i) => [i.id, i]));
      for (const id of Object.keys(cart)) if (!byId.has(id)) delete cart[id];   // menyudan olib tashlangan taomlar
      saveCart();
    } catch { menuFailed = true; }
    renderMenu(); renderMenuButtons(); renderCounters();
    setInterval(updateBanner, 60000);
  }
  init();
})();
