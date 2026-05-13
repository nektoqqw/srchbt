from __future__ import annotations

import math
import re
from dataclasses import dataclass

from db import Database
from username_rarity import combined_rarity

_VOWELS = set("aeiou")
def fragment_listing_usd_band_by_length(length: int) -> tuple[int, int] | None:
    """
    Ориентировочный диапазон цен на витрине Fragment для ников данной длины
    (эвристика, не инвестиционный совет).
    """
    if length < 5:
        return None
    bands: dict[int, tuple[int, int]] = {
        5: (75, 420),
        6: (40, 260),
        7: (22, 150),
        8: (12, 95),
        9: (8, 70),
        10: (5, 55),
    }
    if length >= 11:
        return (4, 45)
    return bands.get(length)


_WORDLIKE = {
    "wolf",
    "ninja",
    "crypto",
    "alpha",
    "beta",
    "delta",
    "omega",
    "nova",
    "luna",
    "star",
    "moon",
    "sun",
    "gold",
    "silver",
    "black",
    "white",
    "king",
    "queen",
    "ace",
    "joker",
    "money",
    "cash",
    "coin",
    "token",
    "trade",
    "market",
    "chain",
    "block",
    "prime",
    "trust",
    "swift",
    "flash",
    "storm",
    "shadow",
    "ghost",
    "cyber",
    "neon",
    "pixel",
    "dragon",
    "tiger",
    "eagle",
    "falcon",
}


@dataclass(frozen=True)
class UsernameValuation:
    username: str
    estimated_price_usd: float | None
    rarity_name: str
    rank_10: int
    stars_5: int
    pros: list[str]
    cons: list[str]
    market_note: str
    exact_sales_count: int
    length_market_band: str


def _pronounceability_score(username: str) -> int:
    u = username.lower()
    if not re.fullmatch(r"[a-z0-9_]{5,32}", u):
        return 0

    letters = [c for c in u if c.isalpha()]
    if not letters:
        return 0

    vowels = sum(1 for c in letters if c in _VOWELS)
    ratio = vowels / max(1, len(letters))

    max_cons_run = 0
    run = 0
    for ch in letters:
        if ch in _VOWELS:
            run = 0
        else:
            run += 1
            max_cons_run = max(max_cons_run, run)

    score = 0
    if vowels > 0:
        score += 25
    if 0.25 <= ratio <= 0.65:
        score += 35
    elif 0.15 <= ratio <= 0.75:
        score += 20
    if max_cons_run <= 2:
        score += 30
    elif max_cons_run == 3:
        score += 15
    if letters[0] not in _VOWELS and letters[-1] in _VOWELS:
        score += 10
    return min(100, score)


def _is_dictionary_like(username: str) -> bool:
    u = username.lower()
    if u in _WORDLIKE:
        return True
    if len(u) <= 8 and re.fullmatch(r"[a-z]+", u):
        # Простая эвристика "похоже на слово"
        return _pronounceability_score(u) >= 70
    return False


def evaluate_username_market(
    username: str, db: Database, *, ton_to_usd: float = 2.0
) -> UsernameValuation:
    uname = username.lower().lstrip("@")
    rarity_info, predicted_price, market_note = combined_rarity(
        uname, db, ton_to_usd=ton_to_usd
    )
    exact_sales = db.get_fragment_prices(uname, limit=100)
    exact_sales_count = len(exact_sales)

    has_digits = any(c.isdigit() for c in uname)
    has_underscore = "_" in uname
    short_len = len(uname) <= 5
    pronounceability = _pronounceability_score(uname)
    dictionary_like = _is_dictionary_like(uname)

    pros: list[str] = []
    cons: list[str] = []

    if short_len:
        pros.append("Короткая длина (до 5 символов)")
    else:
        cons.append("Длина больше 5 символов")

    if not has_digits:
        pros.append("Без цифр")
    else:
        cons.append("Содержит цифры")

    if not has_underscore:
        pros.append("Без подчеркиваний")
    else:
        cons.append("Содержит подчеркивания")

    if pronounceability >= 70:
        pros.append("Хорошая произносимость")
    elif pronounceability >= 50:
        pros.append("Умеренная произносимость")
    else:
        cons.append("Слабая произносимость")

    if rarity_info.min_usd >= 150:
        pros.append("Высокая редкость по сводной модели")
    elif rarity_info.min_usd >= 50:
        pros.append("Повышенная редкость по сводной модели")
    else:
        cons.append("Сводная редкость без «топ»-меток")

    if not dictionary_like:
        if rarity_info.min_usd < 150:
            cons.append("Не является словарным словом")

    if exact_sales_count == 0:
        if rarity_info.min_usd < 50:
            cons.append("По этому имени в боте мало данных — ориентир по цене слабее")
    else:
        pros.append(f"В базе зафиксировано продаж по этому @нику: {exact_sales_count}")

    rank = 1
    rank += 2 if short_len else 1
    rank += 1 if not has_digits else 0
    rank += 1 if not has_underscore else 0
    rank += 2 if pronounceability >= 70 else (1 if pronounceability >= 50 else 0)
    rank += 3 if rarity_info.min_usd >= 500 else (
        2 if rarity_info.min_usd >= 150 else (1 if rarity_info.min_usd >= 50 else 0)
    )
    rank += 2 if exact_sales_count > 0 else (1 if predicted_price is not None else 0)
    rank = max(1, min(10, rank))

    stars = max(1, min(5, int(math.ceil(rank / 2))))

    band = fragment_listing_usd_band_by_length(len(uname))
    if band:
        lo, hi = band
        length_market_band = (
            f"Ориентир по длине ({len(uname)} симв.): на Fragment похожие лоты "
            f"часто держатся в районе <b>${lo:,}–${hi:,}</b> (оценочно)."
        )
    else:
        length_market_band = ""

    return UsernameValuation(
        username=uname,
        estimated_price_usd=predicted_price,
        rarity_name=rarity_info.name,
        rank_10=rank,
        stars_5=stars,
        pros=pros,
        cons=cons,
        market_note=market_note,
        exact_sales_count=exact_sales_count,
        length_market_band=length_market_band,
    )

