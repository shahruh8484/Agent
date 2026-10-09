"""Russian is the source language; this maps API texts to Uzbek (Latin)."""
from __future__ import annotations

from typing import Literal

Lang = Literal["ru", "uz"]

UZ: dict[str, str] = {
    # errors
    "Некорректный номер телефона": "Telefon raqami noto'g'ri. +998 bilan boshlanadigan raqam kiriting",
    "Код устарел, запросите новый": "Kod eskirgan, yangisini so'rang",
    "Слишком много попыток, запросите новый код": "Urinishlar juda ko'p, yangi kod so'rang",
    "Неверный код": "Kod noto'g'ri",
    "Требуется вход": "Tizimga kiring",
    "Выберите город из списка": "Ro'yxatdan shaharni tanlang",
    "Выберите услуги из каталога": "Katalogdan xizmatlarni tanlang",
    "Выберите конкретную услугу": "Aniq xizmatni tanlang",
    "Специалист не найден": "Mutaxassis topilmadi",
    "Заказ не найден": "Buyurtma topilmadi",
    "Это не ваш заказ": "Bu sizning buyurtmangiz emas",
    "Чат не найден": "Chat topilmadi",
    "Нельзя откликнуться на свой заказ": "O'z buyurtmangizga taklif yubora olmaysiz",
    "Заказ уже закрыт": "Buyurtma yopilgan",
    "Сначала заполните анкету специалиста": "Avval mutaxassis anketasini to'ldiring",
    "Чтобы откликаться на заказы, оформите подписку": "Buyurtmalarga taklif yuborish uchun obuna rasmiylashtiring",
    "Вы уже откликнулись на этот заказ": "Siz bu buyurtmaga taklif yuborgansiz",
    "Исполнитель уже выбран или заказ закрыт": "Ijrochi tanlangan yoki buyurtma yopilgan",
    "Отклик не найден": "Taklif topilmadi",
    "Сначала выберите исполнителя": "Avval ijrochini tanlang",
    "Заказ уже завершён": "Buyurtma allaqachon yakunlangan",
    "Отзыв можно оставить после выполнения заказа": "Sharhni buyurtma bajarilgandan keyin qoldirish mumkin",
    "Отзыв уже оставлен": "Sharh allaqachon qoldirilgan",
    "Тариф не найден": "Tarif topilmadi",
    "Способ оплаты недоступен": "To'lov usuli mavjud emas",
    # push notifications
    "Новый заказ": "Yangi buyurtma",
    "Новый отклик": "Yangi taklif",
    "Вас выбрали исполнителем": "Sizni ijrochi sifatida tanlashdi",
    "Новое сообщение": "Yangi xabar",
}


def pick_lang(header: str | None) -> Lang:
    return "uz" if (header or "").lower().startswith("uz") else "ru"


def tr(text: str, lang: str) -> str:
    return UZ.get(text, text) if lang == "uz" else text
