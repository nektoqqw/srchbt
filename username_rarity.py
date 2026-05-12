"""
Сводная «редкость» для крутки и оценки: рынок (USD), история продаж (TON),
зеркальные/палиндромные буквы, короткие и узнаваемые ники.
"""

from __future__ import annotations

import random
import re
import string

from checker import is_valid_telegram_username
from db import Database
from value_model import RARITIES, RarityInfo, predict_price_usd, rarity_from_usd

# Индекс в RARITIES: выше = реже по шкале бота
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


_FAMOUS_USERNAMES = frozenset(
    {
        "durov",
        "telegram",
        "username",
        "support",
        "security",
        "stickers",
        "gif",
        "premium",
        "admin",
        "ceo",
        "nft",
        "wallet",
        "ton",
        "fragment",
    }
)


def _ton_floor_rarity(ton_max: float) -> RarityInfo | None:
    if ton_max >= 150:
        return RARITIES[4]  # Легендарный
    if ton_max >= 50:
        return RARITIES[3]  # Мифический
    if ton_max >= 20:
        return RARITIES[2]  # Эпический
    return None


def _intrinsic_floor(username: str) -> RarityInfo | None:
    u = username.lower().lstrip("@")
    if u in _FAMOUS_USERNAMES:
        return RARITIES[4]
    if is_mirror_letters(u):
        return RARITIES[1]
    return None


def combined_rarity(
    username: str,
    db: Database,
    *,
    ton_to_usd: float,
) -> tuple[RarityInfo, float | None, str]:
    """
    Итоговая метка редкости и пояснение.
    Берётся максимум по правилам: прогноз USD, палиндром, исторические TON, «имя бренда».
    """
    uname = username.lower().lstrip("@")
    base_price, why_usd = predict_price_usd(uname, db)
    r = rarity_from_usd(base_price)
    parts: list[str] = [why_usd]

    ton_max = max_sale_ton(uname, db, ton_to_usd=ton_to_usd)
    t_r = _ton_floor_rarity(ton_max)
    if t_r is not None:
        r = _pick_rarer(r, t_r)
        parts.append(f"в базе продажи до ~{ton_max:.0f} TON → минимум «{t_r.name}»")

    floor = _intrinsic_floor(uname)
    if floor is not None:
        r = _pick_rarer(r, floor)
        if uname in _FAMOUS_USERNAMES:
            parts.append("узнаваемое имя / бренд → повышенная редкость")
        elif is_mirror_letters(uname):
            parts.append("зеркальный палиндром из букв → минимум «Редкий»")

    why = " · ".join(parts)
    return r, base_price, why


def suggest_similar_usernames(username: str, *, limit: int = 8) -> list[str]:
    """Лёгкие вариации для кнопки «Похожие» (без гарантии свободы)."""
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z0-9_]{5,32}", u):
        return []
    letters = [c for c in u if c.isalpha()]
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
