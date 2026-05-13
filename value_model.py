from __future__ import annotations

import statistics
from dataclasses import dataclass

from db import Database


@dataclass(frozen=True)
class RarityInfo:
    name: str
    min_usd: float


RARITIES: list[RarityInfo] = [
    RarityInfo(name="Обычный", min_usd=0),
    RarityInfo(name="Редкий", min_usd=10),
    RarityInfo(name="Эпический", min_usd=50),
    RarityInfo(name="Мифический", min_usd=150),
    RarityInfo(name="Легендарный", min_usd=500),
    RarityInfo(name="Элитный", min_usd=1000),
]


def rarity_from_usd(price_usd: float | None) -> RarityInfo:
    p = price_usd or 0.0
    chosen = RARITIES[0]
    for r in RARITIES:
        if p >= r.min_usd:
            chosen = r
    return chosen


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return float(statistics.median(values))


def predict_price_usd(username: str, db: Database) -> tuple[float | None, str]:
    """
    MVP-прогноз по рынку:
    - если username встречался в импортированных фрагмент-данных, берём медиану цены
    - иначе: средняя цена по близким префиксам (первые 3 символа, затем 2)
    """
    uname = username.lower().lstrip("@")
    prices_exact = db.get_fragment_prices(uname)
    if prices_exact:
        med = _median(prices_exact)
        return med, f"в базе есть сделки по этому @нику (медиана: ${med:,.0f})"

    # prefix match по импортированным данным
    all_items = db.iter_fragment_items(limit=5000)
    candidates = [it for it in all_items if it.price_usd is not None]
    if not candidates:
        return None, "похожих лотов в базе пока нет — ориентир по цене слабый, проверьте ник в Telegram"

    # фильтруем по длине, чтобы прогноз был осмысленным
    same_len = [it for it in candidates if len(it.username) == len(uname)]
    if same_len:
        pref3 = uname[:3]
        pref2 = uname[:2]
        by3 = [it for it in same_len if it.username.startswith(pref3)]
        if len(by3) >= 3:
            med = _median([float(it.price_usd) for it in by3 if it.price_usd is not None])
            if med is not None:
                return med, f"оценка по похожим никам (первые 3 буквы), медиана ${med:,.0f}"
        by2 = [it for it in same_len if it.username.startswith(pref2)]
        if len(by2) >= 3:
            med = _median([float(it.price_usd) for it in by2 if it.price_usd is not None])
            if med is not None:
                return med, f"оценка по похожим никам (первые 2 буквы), медиана ${med:,.0f}"

    # fallback: берём общий медианный уровень для длины
    if same_len:
        vals = [float(it.price_usd) for it in same_len if it.price_usd is not None]
        med = _median(vals)
        if med is not None:
            return med, f"оценка по длине линии ({len(uname)} симв.), медиана ${med:,.0f}"

    return None, "мало похожих примеров — оценка очень условная"


def rarity_tier_for_username(username: str, db: Database) -> tuple[RarityInfo, float | None, str]:
    price, why = predict_price_usd(username, db)
    r = rarity_from_usd(price)
    return r, price, why

