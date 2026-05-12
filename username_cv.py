"""
Генерация ников из латиницы с чередованием согласная–гласная (с 1-й буквы — согласная).

Паттерн для длины 5: C V C V C (как «согл/гл/согл/гл/согл»).
"""

from __future__ import annotations

import random
import string

_VOWELS: tuple[str, ...] = tuple("aeiou")
_CONSONANTS: tuple[str, ...] = tuple(
    c for c in string.ascii_lowercase if c not in frozenset(_VOWELS)
)


def random_cv_username(length: int) -> str:
    """Случайная строка a–z нужной длины с чередованием С / Г."""
    if length <= 0:
        return ""
    chars: list[str] = []
    for i in range(length):
        if i % 2 == 0:
            chars.append(random.choice(_CONSONANTS))
        else:
            chars.append(random.choice(_VOWELS))
    return "".join(chars)


def random_guest_username(length: int) -> str:
    """
    Случайные буквы a–z без паттерна CVCVC… (вариант для гостя).
    """
    if length <= 0:
        return ""
    letters = string.ascii_lowercase
    for _ in range(80):
        s = "".join(random.choice(letters) for _ in range(length))
        if not cv_alternating_ok(s):
            return s
    return s


def cv_alternating_ok(username: str) -> bool:
    """Строка строго укладывается в паттерн CVCVC… (индекс 0 — согласная)."""
    u = username.lower()
    for i, ch in enumerate(u):
        if not ch.isalpha():
            return False
        is_vowel = ch in _VOWELS
        want_vowel = i % 2 == 1
        if is_vowel != want_vowel:
            return False
    return True
