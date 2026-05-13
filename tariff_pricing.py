"""Скидка на витринные тарифы PLUS и «Удача»: отображение и сумма в Platega."""

from __future__ import annotations

# Процент скидки от прайса в каталоге (показываем и списываем со скидкой).
TARIFF_DISCOUNT_PERCENT = 30


def sale_price_rub(list_price_rub: int) -> int:
    """Цена со скидкой, не ниже 1 ₽."""
    p = int(list_price_rub)
    if p <= 0:
        return 1
    return max(1, round(p * (100 - TARIFF_DISCOUNT_PERCENT) / 100))


def sale_price_float(list_price_rub: int) -> float:
    """Сумма для API Platega."""
    return float(sale_price_rub(list_price_rub))


def tariff_payment_price_line_html(list_price_rub: int) -> str:
    """Строка «к оплате» в HTML: цена со скидкой, зачёркнутый прайс, процент."""
    sale = sale_price_rub(list_price_rub)
    lp = int(list_price_rub)
    return (
        f"К оплате: <b>{sale} ₽</b> · <s>{lp} ₽</s> · "
        f"<i>−{TARIFF_DISCOUNT_PERCENT}%</i>"
    )
