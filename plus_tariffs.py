"""Тарифы подписки PLUS (рубли) — оплата через Platega."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as html_escape
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import ui_theme as theme

from tariff_pricing import TARIFF_DISCOUNT_PERCENT, sale_price_rub, tariff_payment_price_line_html


@dataclass(frozen=True)
class PlusTariff:
    key: str
    title_ru: str
    price_rub: int
    days: int | None  # None = навсегда


PLUS_TARIFFS: tuple[PlusTariff, ...] = (
    PlusTariff("1d", "1 день", 40, 1),
    PlusTariff("3d", "3 дня", 70, 3),
    PlusTariff("7d", "Неделя", 120, 7),
    PlusTariff("30d", "Месяц", 200, 30),
    PlusTariff("183d", "Полгода", 550, 183),
    PlusTariff("365d", "Год", 800, 365),
    PlusTariff("forever", "Навсегда", 1500, None),
)


def plus_tariff_by_key(key: str) -> PlusTariff | None:
    k = key.strip().lower()
    for t in PLUS_TARIFFS:
        if t.key == k:
            return t
    return None


def kb_plus_tariffs() -> InlineKeyboardMarkup:
    """Сетка тарифов + промокод."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, t in enumerate(PLUS_TARIFFS):
        row.append(
            InlineKeyboardButton(
                text=f"{t.title_ru} · {sale_price_rub(t.price_rub)} ₽ −{TARIFF_DISCOUNT_PERCENT}%",
                callback_data=f"plus:tariff:{t.key}",
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
                text=f"{theme.PURPLE_ALT} У меня есть промокод",
                callback_data="plus:enter",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_plus_expires_human_ru(plus_expires_at: str | None) -> str:
    """Дата/время окончания PLUS для пользователя (МСК или UTC)."""
    if not plus_expires_at:
        return ""
    raw = str(plus_expires_at).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        loc = dt.astimezone(ZoneInfo("Europe/Moscow"))
        return loc.strftime("%d.%m.%Y %H:%M") + " МСК"
    except Exception:
        return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"


def plus_subscriber_status_banner_html(
    *,
    is_plus: bool,
    plus_expires_at: str | None,
) -> str:
    """Если PLUS уже есть — строки в начале экрана тарифов / оплаты."""
    if not is_plus:
        return ""
    if plus_expires_at is None:
        return (
            f"{theme.OK} <b>Подписка PLUS</b> уже активна <b>без даты окончания</b>.\n"
            "<i>Ниже можно оформить ещё один тариф — дни прибавятся к сроку "
            "(кроме варианта «навсегда»).</i>\n\n"
        )
    when = format_plus_expires_human_ru(plus_expires_at)
    return (
        f"{theme.OK} <b>Подписка PLUS</b> активирована до <b>{html_escape(when)}</b>.\n"
        "<i>Новый тариф продлит срок после оплаты.</i>\n\n"
    )


def plus_shop_intro_html(*, status_banner: str = "") -> str:
    return (
        f"<b>{theme.PLUS} Подписка PLUS</b>\n\n"
        f"{status_banner}"
        f"<b>Скидка {TARIFF_DISCOUNT_PERCENT}%</b> на все сроки — в кнопках указана цена со скидкой.\n\n"
        "Снимает лимит на <b>подбор имён</b> на выбранный срок и даёт <b>сохранение никнеймов</b>.\n\n"
        "<b>Выберите срок:</b>"
    )


def kb_plus_payment_nav(*, pay_url: str | None = None) -> InlineKeyboardMarkup:
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
                    text="◀ Все тарифы",
                    callback_data="plus:shop",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{theme.PURPLE_ALT} Ввести промокод",
                    callback_data="plus:enter",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plus_tariff_payment_html(
    t: PlusTariff,
    *,
    payment_hint: str,
    platega_auto_note: bool = False,
    status_banner: str = "",
) -> str:
    period = (
        "без срока (навсегда)"
        if t.days is None
        else f"<b>{t.days}</b> календарных дней"
    )
    auto = ""
    if platega_auto_note:
        auto = (
            "\n\n<i>После успешной оплаты PLUS включится автоматически "
            "(обычно в течение минуты).</i>"
        )
    return (
        f"<b>{theme.PLUS} Оплата PLUS</b>\n\n"
        f"{status_banner}"
        f"Тариф: <b>{html_escape(t.title_ru)}</b>\n"
        f"Срок: {period}\n"
        f"{tariff_payment_price_line_html(t.price_rub)}\n\n"
        f"{payment_hint}{auto}"
    )
