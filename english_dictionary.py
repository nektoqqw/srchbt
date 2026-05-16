"""Проверка: латинское слово из английского словаря (5–7 букв для крутки)."""

from __future__ import annotations

import random
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


@lru_cache(maxsize=3)
def english_words_at_length(length: int) -> tuple[str, ...]:
    """Слова словаря ровно заданной длины (5, 6 или 7)."""
    if length not in (5, 6, 7):
        return ()
    return tuple(w for w in english_words_5_7() if len(w) == length)


def random_english_dictionary_word(length: int) -> str:
    """Случайное слово из словаря; гарантированно is_english_dictionary_word."""
    pool = english_words_at_length(length)
    if not pool:
        raise RuntimeError(f"Словарь пуст для длины {length}")
    return random.choice(pool)


def shuffled_dictionary_words(length: int) -> list[str]:
    """Все слова длины length в случайном порядке (без повторов в одном проходе)."""
    return dictionary_words_for_roll(length, popular_first=False)


def _priority_path(length: int) -> Path:
    return Path(__file__).resolve().parent / "data" / f"dict_priority_{length}.txt"


@lru_cache(maxsize=3)
def dictionary_priority_words(length: int) -> tuple[str, ...]:
    """Упорядоченный список «популярных» слов из data/dict_priority_N.txt + fallback."""
    if length not in (5, 6, 7):
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    path = _priority_path(length)
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            for line in f:
                raw = line.split("#", 1)[0].strip().lower()
                if not raw.isalpha() or len(raw) != length:
                    continue
                if raw in seen:
                    continue
                seen.add(raw)
                ordered.append(raw)
    for w in _FALLBACK:
        if len(w) == length and w not in seen:
            seen.add(w)
            ordered.append(w)
    return tuple(ordered)


def dictionary_words_for_roll(length: int, *, popular_first: bool = True) -> list[str]:
    """
    Слова для админ-крутки без повторов.
    popular_first: сначала приоритетный список, затем остальной словарь (перемешан).
    """
    all_set = set(english_words_at_length(length))
    if not all_set:
        return []
    if not popular_first:
        out = list(all_set)
        random.shuffle(out)
        return out
    priority = [w for w in dictionary_priority_words(length) if w in all_set]
    seen = set(priority)
    rest = [w for w in all_set if w not in seen]
    random.shuffle(rest)
    return priority + rest


def dictionary_word_count(length: int) -> int:
    return len(english_words_at_length(length))
