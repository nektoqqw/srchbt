"""Проверка: латинское слово из английского словаря (5–7 букв для крутки)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_WORDS_PATH = Path(__file__).resolve().parent / "data" / "english_words_5_7.txt"

# Запасной мини-набор, если файл словаря не найден
_FALLBACK: frozenset[str] = frozenset(
    {
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
        "king",
        "queen",
        "money",
        "trade",
        "storm",
        "ghost",
        "dragon",
        "tiger",
        "eagle",
        "apple",
        "beach",
        "brain",
        "cloud",
        "dream",
        "earth",
        "flame",
        "green",
        "happy",
        "heart",
        "house",
        "light",
        "magic",
        "night",
        "ocean",
        "peace",
        "power",
        "river",
        "smile",
        "sound",
        "space",
        "sweet",
        "table",
        "train",
        "trust",
        "water",
        "world",
        "young",
        "pavel",
        "bitcoin",
        "ethereum",
        "wallet",
        "token",
        "trade",
        "admin",
        "ghost",
        "lucky",
        "happy",
        "candy",
        "pizza",
        "music",
        "video",
        "photo",
        "games",
        "sport",
        "beach",
        "party",
        "angel",
        "devil",
        "queen",
        "prince",
        "lord",
        "master",
        "legend",
        "hero",
        "fire",
        "ice",
        "rock",
        "metal",
        "steel",
        "blade",
        "sword",
        "shield",
    }
)


@lru_cache(maxsize=1)
def english_words_5_7() -> frozenset[str]:
    words: set[str] = set(_FALLBACK)
    if _WORDS_PATH.is_file():
        with _WORDS_PATH.open(encoding="utf-8") as f:
            for line in f:
                w = line.strip().lower()
                if w.isalpha() and 5 <= len(w) <= 7:
                    words.add(w)
    return frozenset(words)


def is_english_dictionary_word(username: str) -> bool:
    """Точное совпадение со словарём (только a–z, длина 5–7)."""
    u = username.lower().lstrip("@")
    if not re.fullmatch(r"[a-z]{5,7}", u):
        return False
    return u in english_words_5_7()
