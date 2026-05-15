"""
Сводная «редкость» для крутки и оценки: длина + словарь, рынок (USD),
история продаж (TON), палиндромы, премиум-бренды (@durov и др.).
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


def _rarity_index(r: RarityInfo) -> int:
    for i, x in enumerate(RARITIES):
        if x.name == r.name:
            return i
    return 0


def _pick_rarer(a: RarityInfo, b: RarityInfo) -> RarityInfo:
    return a if _rarity_index(a) >= _rarity_index(b) else b


def is_mirror_letters(username: str) -> bool:
    """Палиндром из букв a–z (зеркало по буквам)."""
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
    Шкала для крутки 5–7 букв:
    · 5 — словарь → Мифический, иначе Эпический
    · 6 — словарь → Эпический, иначе Редкий
    · 7 — словарь → Редкий, иначе Обычный
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


def _explain_length_dictionary(username: str, rarity: RarityInfo) -> str:
    u = username.lower().lstrip("@")
    in_dict = is_english_dictionary_word(u)
    n = len(u)
    if n == 5:
        kind = "словарное английское слово" if in_dict else "не из словаря"
        return f"5 букв, {kind} → «{rarity.name}»"
    if n == 6:
        kind = "словарное" if in_dict else "не словарное"
        return f"6 букв ({kind}) → «{rarity.name}»"
    if n == 7:
        kind = "словарное" if in_dict else "не словарное"
        return f"7 букв ({kind}) → «{rarity.name}»"
    return f"шкала длины → «{rarity.name}»"


def _price_floor_for_rarity(r: RarityInfo) -> float:
    """Ориентир USD, если нет сделок в базе."""
    floors = {
        "Обычный": 8.0,
        "Редкий": 35.0,
        "Эпический": 90.0,
        "Мифический": 220.0,
        "Легендарный": 800.0,
        "Элитный": 2500.0,
    }
    return floors.get(r.name, max(r.min_usd, 10.0))


def _ton_floor_rarity(ton_max: float) -> RarityInfo | None:
    if ton_max >= 150:
        return RARITIES[4]
    if ton_max >= 50:
        return RARITIES[3]
    if ton_max >= 20:
        return RARITIES[2]
    return None


def _intrinsic_floor(username: str) -> RarityInfo | None:
    if get_premium_username(username):
        return RARITIES[5]
    if is_mirror_letters(username):
        return RARITIES[1]
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

    prem_info = get_premium_username(uname)
    if prem_info and ton_to_usd > 0:
        base_price = prem_info.min_ton * ton_to_usd
        r = RARITIES[5] if prem_info.min_ton >= 150_000 else RARITIES[4]
        parts.append(
            f"{prem_info.title_ru}. {prem_info.why_price_ru} "
            f"Ориентир: от ~{prem_info.min_ton:,.0f} TON (~${base_price:,.0f})."
        )
    else:
        ld = _length_dictionary_rarity(uname)
        if ld:
            r = ld
            parts.append(_explain_length_dictionary(uname, ld))
            base_price = _price_floor_for_rarity(ld)
        else:
            base_price, why_usd = predict_price_usd(uname, db)
            r = rarity_from_usd(base_price)
            parts.append(why_usd)

    ton_max = max_sale_ton(uname, db, ton_to_usd=ton_to_usd)
    t_r = _ton_floor_rarity(ton_max)
    if t_r is not None:
        r = _pick_rarer(r, t_r)
        parts.append(f"в базе продажи до ~{ton_max:.0f} TON → минимум «{t_r.name}»")
        if base_price is None:
            base_price = ton_max * ton_to_usd

    floor = _intrinsic_floor(uname)
    if floor is not None and not prem_info:
        r = _pick_rarer(r, floor)
        if is_mirror_letters(uname):
            parts.append("зеркальный палиндром из букв → минимум «Редкий»")

    if base_price is None and r.min_usd > 0:
        base_price = _price_floor_for_rarity(r)

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
