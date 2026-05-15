"""
Сводная «редкость» и ориентир цены для крутки и оценки (5–7 букв).
"""

from __future__ import annotations

import random
import re
import string

from checker import is_valid_telegram_username
from db import Database
from english_dictionary import is_english_dictionary_word
from premium_usernames import get_premium_username
from value_model import RARITIES, RarityInfo, predict_price_usd, rarity_from_usd

# Ориентир USD по длине (не из словаря)
_LENGTH_PRICE_BANDS: dict[int, tuple[int, int]] = {
    5: (75, 420),
    6: (40, 260),
    7: (22, 150),
}
_DICT_PRICE_BAND = (200, 1000)


def _rarity_index(r: RarityInfo) -> int:
    for i, x in enumerate(RARITIES):
        if x.name == r.name:
            return i
    return 0


def _pick_rarer(a: RarityInfo, b: RarityInfo) -> RarityInfo:
    return a if _rarity_index(a) >= _rarity_index(b) else b


def is_mirror_letters(username: str) -> bool:
    u = username.lower().lstrip("@")
    if not u.isalpha() or len(u) < 5:
        return False
    return u == u[::-1]


def max_sale_ton(username: str, db: Database, *, ton_to_usd: float) -> float:
    if ton_to_usd <= 0:
        return 0.0
    prices = db.get_fragment_prices(username.lower().lstrip("@"), limit=200)
    if not prices:
        return 0.0
    return max(float(p) / ton_to_usd for p in prices)


def _length_dictionary_rarity(username: str) -> RarityInfo | None:
    """
    5 букв: словарь → Мифический, иначе Эпический
    6 букв: словарь → Эпический, иначе Редкий
    7 букв: словарь → Редкий, иначе Обычный
    """
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z]{5,7}", u):
        return None
    n = len(u)
    in_dict = is_english_dictionary_word(u)
    if n == 5:
        return RARITIES[3] if in_dict else RARITIES[2]
    if n == 6:
        return RARITIES[2] if in_dict else RARITIES[1]
    if n == 7:
        return RARITIES[1] if in_dict else RARITIES[0]
    return None


def _is_roll_line_username(username: str) -> bool:
    return _length_dictionary_rarity(username) is not None


def orientational_price_usd(username: str) -> tuple[float | None, str]:
    """Стабильный ориентир в USD по длине и словарю."""
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z]{5,7}", u):
        return None, ""
    rnd = random.Random(hash(u) & 0xFFFFFFFF)
    if is_english_dictionary_word(u):
        lo, hi = _DICT_PRICE_BAND
        p = round(rnd.uniform(lo, hi), 0)
        return p, f"словарное слово · ориентир ${lo}–${hi}"
    lo, hi = _LENGTH_PRICE_BANDS[len(u)]
    p = round(rnd.uniform(lo, hi), 0)
    return p, f"{len(u)} букв · ориентир ${lo}–${hi}"


def price_band_for_username(username: str) -> tuple[int, int] | None:
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z]{5,7}", u):
        return None
    if is_english_dictionary_word(u):
        return _DICT_PRICE_BAND
    return _LENGTH_PRICE_BANDS.get(len(u))


def _explain_length_dictionary(username: str, rarity: RarityInfo) -> str:
    u = username.lower().lstrip("@")
    in_dict = is_english_dictionary_word(u)
    n = len(u)
    if n == 5:
        kind = "словарное слово" if in_dict else "не из словаря"
        return f"5 символов, {kind} → «{rarity.name}»"
    if n == 6:
        kind = "словарное" if in_dict else "не словарное"
        return f"6 символов ({kind}) → «{rarity.name}»"
    if n == 7:
        kind = "словарное" if in_dict else "не словарное"
        return f"7 символов ({kind}) → «{rarity.name}»"
    return f"«{rarity.name}»"


def _ton_floor_rarity(ton_max: float) -> RarityInfo | None:
    if ton_max >= 150:
        return RARITIES[4]
    if ton_max >= 50:
        return RARITIES[3]
    if ton_max >= 20:
        return RARITIES[2]
    return None


def combined_rarity(
    username: str,
    db: Database,
    *,
    ton_to_usd: float,
) -> tuple[RarityInfo, float | None, str]:
    uname = username.lower().lstrip("@")
    parts: list[str] = []
    base_price: float | None = None
    roll_line = _is_roll_line_username(uname)

    prem_info = get_premium_username(uname)
    if prem_info and ton_to_usd > 0:
        base_price = prem_info.min_ton * ton_to_usd
        r = RARITIES[5] if prem_info.min_ton >= 150_000 else RARITIES[4]
        parts.append(
            f"{prem_info.title_ru}. {prem_info.why_price_ru} "
            f"Ориентир: от ~{prem_info.min_ton:,.0f} TON (~${base_price:,.0f})."
        )
    elif roll_line:
        ld = _length_dictionary_rarity(uname)
        assert ld is not None
        r = ld
        base_price, price_note = orientational_price_usd(uname)
        parts.append(_explain_length_dictionary(uname, ld))
        if price_note:
            parts.append(price_note)
        ton_max = max_sale_ton(uname, db, ton_to_usd=ton_to_usd)
        if ton_max > 0 and ton_to_usd > 0 and base_price is not None:
            sale_usd = ton_max * ton_to_usd
            if sale_usd > base_price:
                base_price = round(sale_usd, 0)
                parts.append(f"по сделкам в базе до ~{ton_max:.0f} TON")
    else:
        base_price, why_usd = predict_price_usd(uname, db)
        r = rarity_from_usd(base_price)
        parts.append(why_usd)
        ton_max = max_sale_ton(uname, db, ton_to_usd=ton_to_usd)
        t_r = _ton_floor_rarity(ton_max)
        if t_r is not None:
            r = _pick_rarer(r, t_r)
            parts.append(f"в базе продажи до ~{ton_max:.0f} TON → «{t_r.name}»")
            if base_price is None:
                base_price = ton_max * ton_to_usd
        if is_mirror_letters(uname):
            r = _pick_rarer(r, RARITIES[1])
            parts.append("зеркальный палиндром → минимум «Редкий»")

    if base_price is None and r.min_usd > 0:
        base_price = float(r.min_usd)

    why = " · ".join(p for p in parts if p)
    return r, base_price, why


def suggest_similar_usernames(username: str, *, limit: int = 8) -> list[str]:
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z0-9_]{5,32}", u):
        return []
    out: list[str] = []
    rnd = random.Random(hash(u) & 0xFFFFFFFF)

    def push(s: str) -> None:
        if is_valid_telegram_username(s) and s not in out and s != u:
            out.append(s)

    vowels = "aeiou"
    for _ in range(40):
        s = list(u)
        if len(s) >= 5 and rnd.random() < 0.4:
            i = rnd.randrange(0, len(s))
            if s[i].isalpha():
                s[i] = rnd.choice(vowels if s[i] in vowels else string.ascii_lowercase)
        elif len(s) >= 6 and rnd.random() < 0.35:
            i = rnd.randrange(1, len(s) - 1)
            s.insert(i, rnd.choice(string.ascii_lowercase))
        elif len(s) > 5 and rnd.random() < 0.3:
            i = rnd.randrange(1, len(s) - 1)
            del s[i]
        else:
            a, b = rnd.sample(range(len(s)), 2)
            s[a], s[b] = s[b], s[a]
        push("".join(s))
        if len(out) >= limit:
            break
    return out[:limit]
