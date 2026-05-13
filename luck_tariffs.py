"""Тарифы режима «Удача» (рубли) — оплата вручную по реквизитам, как PLUS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from html import escape as html_escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class LuckTariff:
    key: str
    title_ru: str
    price_rub: int
    delta: timedelta | None  # None = навсегда


LUCK_TARIFFS: tuple[LuckTariff, ...] = (
    LuckTariff("1h", "1 час", 10, timedelta(hours=1)),
    LuckTariff("3h", "3 часа", 20, timedelta(hours=3)),
    LuckTariff("1d", "Сутки", 30, timedelta(days=1)),
    LuckTariff("7d", "Неделя", 60, timedelta(days=7)),
    LuckTariff("forever", "Навсегда", 150, None),
)


def luck_tariff_by_key(key: str) -> LuckTariff | None:
    k = key.strip().lower()
    for t in LUCK_TARIFFS:
        if t.key == k:
            return t
    return None


def kb_luck_tariffs() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, t in enumerate(LUCK_TARIFFS):
        row.append(
            InlineKeyboardButton(
                text=f"{t.title_ru} · {t.price_rub} ₽",
                callback_data=f"luck:tariff:{t.key}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="🗝 У меня есть промокод",
                callback_data="luck:enter",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def luck_shop_intro_html() -> str:
    return (
        "<b>🍀 Режим «Удача»</b>\n\n"
        "<i>Тарифы ниже — только при активной подписке <b>PLUS</b>.</i>\n\n"
        "При подборе из каталога чаще всплывают <b>симметричные и «удачные»</b> "
        "комбинации при той же цене.\n\n"
        "<b>Выберите срок:</b>"
    )


def kb_luck_payment_nav(*, pay_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pay_url and pay_url.strip().startswith("http"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="Оплатить",
                    url=pay_url.strip(),
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="◀ Все тарифы удачи",
                    callback_data="luck:shop",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗝 Ввести промокод",
                    callback_data="luck:enter",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def luck_tariff_payment_html(
    t: LuckTariff,
    *,
    payment_hint: str,
    platega_auto_note: bool = False,
    online_pay_line: bool = False,
) -> str:
    period = (
        "без срока (навсегда)"
        if t.delta is None
        else f"<b>{t.title_ru}</b> доступа"
    )
    auto = ""
    if platega_auto_note:
        auto = (
            "\n\n<i>После успешной оплаты «Удача» включится автоматически "
            "(обычно в течение минуты).</i>"
        )
    pay_line = ""
    if online_pay_line:
        pay_line = "\n\n<b>Онлайн-оплата:</b> нажмите кнопку <b>«Оплатить»</b> ниже."
    return (
        f"<b>🍀 Оплата «Удача»</b>\n\n"
        f"Тариф: <b>{html_escape(t.title_ru)}</b>\n"
        f"Срок: {period}\n"
        f"К оплате: <b>{t.price_rub} ₽</b>{pay_line}\n\n"
        f"{payment_hint}{auto}"
    )
