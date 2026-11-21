"""Mijozga ko'rinadigan bot matnlari: o'zbekcha (uz) va ruscha (ru).

- Kalit bo'yicha: t(lang, "kalit", param=...) -> matn.
- Ikkala tilda kalitlar AYNAN bir xil bo'lishi shart (test tekshiradi).
- Brend nomi (Chicken24) va TAOM NOMLARI tarjima qilinmaydi.
- Admin xabarlari (chek, /hisobot, /excel, /backup) bu yerda emas - ular o'zbekcha qoladi.
"""
from __future__ import annotations

import config

LANGS = ("uz", "ru")
DEFAULT_LANG = "uz"

# Hamma matnlarga avtomatik qo'shiladigan parametrlar (tilga qarab)
COMMON: dict[str, dict[str, str]] = {"uz": {}, "ru": {}}

TEXTS: dict[str, dict[str, str]] = {
    "uz": {
        # --- til tanlash
        "lang.prompt": "Tilni tanlang / Выберите язык",
        "lang.btn_uz": "🇺🇿 O'zbekcha",
        "lang.btn_ru": "🇷🇺 Русский",
        "lang.picked": "🇺🇿 O'zbekcha tanlandi",
        "lang.changed": "✅ Til o'zgartirildi: O'zbekcha.\nBuyurtma berish uchun quyidagi tugmani bosing 👇",
        # --- salomlashuv
        "welcome.hello": "Assalomu alaykum, {name}! 👋",
        "welcome.friend": "do'stim",
        "welcome.title": "Chicken24 ga xush kelibsiz!",
        "welcome.hours": "🕙 Ish vaqti: {open} – {close}",
        "welcome.delivery": "🚚 Yetkazish: {fee}dan, masofaga qarab",
        "welcome.payment": "💵 To'lov: naqd",
        "welcome.closed": "⛔ Hozir yopiqmiz, {open} da ochamiz.",
        "welcome.cta": "Buyurtma berish uchun quyidagi tugmani bosing 👇",
        # --- klaviatura
        "btn.order": "Buyurtma berish",
        "menu.btn": "Buyurtma",  # matn maydoni yonidagi menyu tugmasi (qisqa)
        "currency": "so'm",
        # --- buyurtma rad etildi
        "order.closed": (
            "⛔ Buyurtma qabul qilinmadi.\nSabab: Hozir yopiqmiz. Ish vaqti {open} – {close}.\n\n"
            "Savatingiz Mini App'da saqlangan — ish vaqtida qayta urinib ko'ring."
        ),
        "order.limit": (
            "⏳ Buyurtma qabul qilinmadi: siz hozirgina buyurtma berdingiz.\n"
            "Savatingiz Mini App'da saqlangan — {sec} soniyadan so'ng qayta urinib ko'ring."
        ),
        "order.rejected": (
            "❌ Buyurtma qabul qilinmadi.\nSabab: {reason}\n\n"
            "Savatingiz Mini App'da saqlangan — tuzatib, qayta urinib ko'ring."
        ),
        "error.generic": "Xatolik yuz berdi, qayta urinib ko'ring",
        "error.cart_saved": "Savatingiz Mini App'da saqlangan.",
        # --- buyurtma qabul qilindi
        "order.accepted": "✅ <b>Buyurtmangiz qabul qilindi!</b>",
        "order.number": "Buyurtma raqami: <b>#{id}</b>",
        "order.courier": "Kuryerimiz tez orada siz bilan bog'lanadi.",
        "order.delayed": (
            "⚠️ Buyurtma saqlandi, lekin restoranga yetkazishda kechikish bo'ldi — "
            "u avtomatik qayta yuboriladi. Qayta buyurtma bermang."
        ),
        "totals.items": "Taomlar: {sum}",
        "totals.delivery": "🚚 Yetkazish: {sum}",
        "totals.delivery_km": "🚚 Yetkazish ({km} km{approx}): {sum}",
        "totals.approx": ", taxminiy",
        "totals.total": "💰 <b>Jami: {sum}</b>",
        "totals.payment": "💵 To'lov: naqd (kuryerga)",
        # --- bekor qilish
        "cancel.offer": "Fikringiz o'zgardimi? Buyurtma #{id} ni {min} daqiqa ichida bekor qilishingiz mumkin.",
        "cancel.btn": "❌ Buyurtmani bekor qilish",
        "cancel.expired": "Buyurtma #{id} ni bekor qilish muddati tugadi.",
        "cancel.bad": "Noto'g'ri so'rov",
        "cancel.not_found": "Buyurtma topilmadi",
        "cancel.already": "Buyurtma #{id} allaqachon bekor qilingan.",
        "cancel.too_late": "Bekor qilish muddati ({min} daqiqa) tugagan. Buyurtma tayyorlanmoqda.",
        "cancel.done_alert": "Buyurtma bekor qilindi",
        "cancel.done_edit": "🚫 Buyurtma #{id} bekor qilindi.",
        "cancel.done_msg": "🚫 Buyurtma #{id} bekor qilindi. Yangi buyurtma uchun quyidagi tugmani bosing.",
        # --- boshqa
        "fallback": (
            "Buyurtma berish uchun quyidagi <b>«{btn}»</b> tugmasini bosing 👇\n"
            "Yoki matn maydoni yonidagi «Buyurtma» menyu tugmasidan foydalaning."
        ),
        # --- buyurtma tekshiruvi (validators.py)
        "val.empty": "Buyurtma ma'lumoti bo'sh keldi.",
        "val.too_big": "Buyurtma hajmi juda katta.",
        "val.broken": "Buyurtma ma'lumoti buzilgan.",
        "val.name_missing": "Ism kiritilmagan.",
        "val.name_chars": "Ismda ruxsat etilmagan belgilar bor.",
        "val.name_len": "Ism {min}–{max} belgidan iborat bo'lishi kerak.",
        "val.phone": "Telefon raqam noto'g'ri. Namuna: +998 90 123 45 67.",
        "val.addr_type": "Manzil ko'rsatilmagan.",
        "val.loc_missing": "Joriy manzil koordinatalari kelmadi.",
        "val.loc_bad": "Joriy manzil koordinatalari noto'g'ri.",
        "val.addr_missing": "Manzil kiritilmagan.",
        "val.addr_len": "Manzil {max} belgidan oshmasligi kerak.",
        "val.addr_chars": "Manzilda ruxsat etilmagan belgilar bor.",
        "val.details_type": "Uy / qavat / xonadon noto'g'ri.",
        "val.details_len": "Uy / qavat / xonadon {max} belgidan oshmasligi kerak.",
        "val.details_chars": "Uy / qavat / xonadonda ruxsat etilmagan belgilar bor.",
        "val.comment_type": "Izoh noto'g'ri.",
        "val.comment_len": "Izoh {max} belgidan oshmasligi kerak.",
        "val.comment_chars": "Izohda ruxsat etilmagan belgilar bor.",
        "val.cart_empty": "Savat bo'sh.",
        "val.cart_lines": "Savatda {max} xildan ortiq taom bo'lishi mumkin emas.",
        "val.cart_broken": "Savat ma'lumoti buzilgan.",
        "val.qty": "Har bir taom soni {min}–{max} oralig'ida bo'lishi kerak.",
        "val.menu": (
            "Savatdagi ba'zi taomlar menyuda yo'q (menyu yangilangan bo'lishi mumkin). "
            "Savatni yangilab, qayta buyurtma bering."
        ),
        "val.loc_required": (
            "Joriy manzil (lokatsiya) yuborilmagan. Yetkazish narxi masofaga qarab hisoblanadi — "
            "Mini App'da «📍 Joriy manzilni yuborish» tugmasini bosing."
        ),
    },
    "ru": {
        "lang.prompt": "Tilni tanlang / Выберите язык",
        "lang.btn_uz": "🇺🇿 O'zbekcha",
        "lang.btn_ru": "🇷🇺 Русский",
        "lang.picked": "🇷🇺 Выбран русский язык",
        "lang.changed": "✅ Язык изменён: русский.\nЧтобы сделать заказ, нажмите кнопку ниже 👇",
        "welcome.hello": "Здравствуйте, {name}! 👋",
        "welcome.friend": "друг",
        "welcome.title": "Добро пожаловать в Chicken24!",
        "welcome.hours": "🕙 Мы работаем с {open} до {close}",
        "welcome.delivery": "🚚 Доставка: от {fee}, в зависимости от расстояния",
        "welcome.payment": "💵 Оплата: наличными",
        "welcome.closed": "⛔ Сейчас мы закрыты, откроемся в {open}.",
        "welcome.cta": "Чтобы сделать заказ, нажмите кнопку ниже 👇",
        "btn.order": "Сделать заказ",
        "menu.btn": "Заказ",
        "currency": "сум",
        "order.closed": (
            "⛔ Заказ не принят.\nПричина: сейчас мы закрыты. Мы работаем с {open} до {close}.\n\n"
            "Ваша корзина сохранена в Mini App — попробуйте снова в рабочее время."
        ),
        "order.limit": (
            "⏳ Заказ не принят: вы только что оформили заказ.\n"
            "Корзина сохранена в Mini App — попробуйте снова через {sec} сек."
        ),
        "order.rejected": (
            "❌ Заказ не принят.\nПричина: {reason}\n\n"
            "Корзина сохранена в Mini App — исправьте данные и попробуйте снова."
        ),
        "error.generic": "Произошла ошибка, попробуйте ещё раз",
        "error.cart_saved": "Корзина сохранена в Mini App.",
        "order.accepted": "✅ <b>Ваш заказ принят!</b>",
        "order.number": "Номер заказа: <b>#{id}</b>",
        "order.courier": "Наш курьер скоро свяжется с вами.",
        "order.delayed": (
            "⚠️ Заказ сохранён, но передача в ресторан задерживается — "
            "он будет отправлен автоматически. Не оформляйте заказ повторно."
        ),
        "totals.items": "Блюда: {sum}",
        "totals.delivery": "🚚 Доставка: {sum}",
        "totals.delivery_km": "🚚 Доставка ({km} км{approx}): {sum}",
        "totals.approx": ", примерно",
        "totals.total": "💰 <b>Итого: {sum}</b>",
        "totals.payment": "💵 Оплата: наличными курьеру",
        "cancel.offer": "Передумали? Заказ #{id} можно отменить в течение {min} минут.",
        "cancel.btn": "❌ Отменить заказ",
        "cancel.expired": "Время на отмену заказа #{id} истекло.",
        "cancel.bad": "Неверный запрос",
        "cancel.not_found": "Заказ не найден",
        "cancel.already": "Заказ #{id} уже отменён.",
        "cancel.too_late": "Время на отмену ({min} мин) истекло. Заказ уже готовится.",
        "cancel.done_alert": "Заказ отменён",
        "cancel.done_edit": "🚫 Заказ #{id} отменён.",
        "cancel.done_msg": "🚫 Заказ #{id} отменён. Для нового заказа нажмите кнопку ниже.",
        "fallback": (
            "Чтобы сделать заказ, нажмите кнопку <b>«{btn}»</b> ниже 👇\n"
            "Или воспользуйтесь кнопкой меню «Заказ» рядом с полем ввода."
        ),
        "val.empty": "Данные заказа не получены.",
        "val.too_big": "Слишком большой заказ.",
        "val.broken": "Данные заказа повреждены.",
        "val.name_missing": "Не указано имя.",
        "val.name_chars": "В имени есть недопустимые символы.",
        "val.name_len": "Имя должно содержать от {min} до {max} символов.",
        "val.phone": "Неверный номер телефона. Пример: +998 90 123 45 67.",
        "val.addr_type": "Адрес не указан.",
        "val.loc_missing": "Координаты местоположения не получены.",
        "val.loc_bad": "Неверные координаты местоположения.",
        "val.addr_missing": "Адрес не указан.",
        "val.addr_len": "Адрес не должен превышать {max} символов.",
        "val.addr_chars": "В адресе есть недопустимые символы.",
        "val.details_type": "Неверно указаны дом / этаж / квартира.",
        "val.details_len": "Поле «дом / этаж / квартира» не должно превышать {max} символов.",
        "val.details_chars": "В поле «дом / этаж / квартира» есть недопустимые символы.",
        "val.comment_type": "Неверный комментарий.",
        "val.comment_len": "Комментарий не должен превышать {max} символов.",
        "val.comment_chars": "В комментарии есть недопустимые символы.",
        "val.cart_empty": "Корзина пуста.",
        "val.cart_lines": "В корзине может быть не более {max} позиций.",
        "val.cart_broken": "Данные корзины повреждены.",
        "val.qty": "Количество каждого блюда — от {min} до {max}.",
        "val.menu": (
            "Некоторых блюд из корзины нет в меню (возможно, меню обновилось). "
            "Обновите корзину и оформите заказ снова."
        ),
        "val.loc_required": (
            "Не отправлено текущее местоположение. Стоимость доставки зависит от расстояния — "
            "нажмите в Mini App «📍 Отправить текущее местоположение»."
        ),
    },
}


def normalize(lang: str | None) -> str:
    """Saqlangan/yuborilgan til kodi -> 'uz' yoki 'ru' (noma'lum bo'lsa 'uz')."""
    return lang if lang in LANGS else DEFAULT_LANG


def lang_from_code(code: str | None) -> str:
    """Telegram language_code ('ru', 'ru-RU', 'uz', 'en'...) -> 'ru' yoki 'uz'."""
    return "ru" if (code or "").lower().startswith("ru") else "uz"


def t(lang: str | None, key: str, **params) -> str:
    """Matn: avval tanlangan tilda, bo'lmasa o'zbekcha; topilmasa - kalitning o'zi (log uchun ko'rinadi)."""
    lang = normalize(lang)
    template = TEXTS[lang].get(key) or TEXTS[DEFAULT_LANG].get(key) or key
    values = {**COMMON[lang], **params}
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def format_price(amount: int, lang: str | None = DEFAULT_LANG) -> str:
    """30000 -> "30 000 so'm" / "30 000 сум"."""
    return f"{amount:,}".replace(",", " ") + " " + t(lang, "currency")
