"""
Генерация и оценка «удачливых» ников на базе паттерна CVCVC… (согласная с нулевой позиции).

PLUS и гость с «Удачей»: палиндромы / края в духе «xanax», чередование CVC…; без сырого случайного набора букв.
"""

from __future__ import annotations

import random

from username_cv import (
    _CONSONANTS,
    _VOWELS,
    cv_alternating_ok,
    random_cv_username,
)


def luck_score(username: str) -> int:
    """Эвристика «интересности» строки (для сортировки кандидатов в ROLL)."""
    u = username.lower()
    if not u.isalpha():
        return 0
    score = 0
    n = len(u)
    if n >= 2 and u[0] == u[-1]:
        score += 4
    if n >= 4 and u[0] == u[-1] and u[1] == u[-2]:
        score += 10
    if n >= 3 and u == u[::-1]:
        score += 14
    if cv_alternating_ok(u):
        score += 5
    return score


def _rnd_bookends_cv(n: int) -> str:
    """Одинаковые края — возможны только при нечётной длине (оба конца — согласные)."""
    if n < 3 or n % 2 == 0:
        return random_cv_username(n)
    c = random.choice(_CONSONANTS)
    s = list(random_cv_username(n))
    s[0] = c
    s[-1] = c
    return "".join(s)


def _rnd_mirror_cv(n: int) -> str:
    """
    Палиндром с чередованием CVC… только для нечётной длины (чётная — обычный CV).
    """
    if n < 5:
        return random_cv_username(n)
    if n % 2 == 0:
        return random_cv_username(n)
    half_len = n // 2
    left: list[str] = []
    for i in range(half_len):
        if i % 2 == 0:
            left.append(random.choice(_CONSONANTS))
        else:
            left.append(random.choice(_VOWELS))
    mid = random.choice(
        _VOWELS if (half_len % 2 == 1) else _CONSONANTS
    )
    return "".join(left + [mid] + left[::-1])


def random_lucky_username(length: int) -> str:
    """PLUS + удача: палиндромы CV и «книжные» края; без удвоенных соседних букв."""
    if length < 5:
        return random_cv_username(length)

    mode = random.choices(
        [
            "mirror",
            "bookend",
            "uniform_cv",
        ],
        weights=[48, 32, 20],
        k=1,
    )[0]

    if mode == "bookend":
        return _rnd_bookends_cv(length)
    if mode == "mirror":
        return _rnd_mirror_cv(length)
    return random_cv_username(length)


def random_free_lucky_username(length: int) -> str:
    """Гость + удача: те же «красивые» паттерны, что и PLUS (раньше при чётной длине был сырой random)."""
    return random_lucky_username(length)
