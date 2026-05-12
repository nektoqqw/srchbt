"""
Генерация и оценка «удачливых» ников на базе паттерна CVCVC… (согласная с нулевой позиции).

PLUS + удача: палиндромы / края в духе «xanax», без соседних удвоений букв.
Гость + удача: палиндром из случайных букв (первые две позиции задают зеркальный хвост).
"""

from __future__ import annotations

import random
import string

from username_cv import (
    _CONSONANTS,
    _VOWELS,
    cv_alternating_ok,
    random_cv_username,
    random_guest_username,
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


def _rnd_scatter_palindrome(n: int) -> str:
    """
    Палиндром из случайных букв: первые ⌊n/2⌋ задают конец (зеркало), как xanax по структуре краёв.
    Без паттерна CVCVC.
    """
    if n <= 0:
        return ""
    half = n // 2
    letters = string.ascii_lowercase
    left = "".join(random.choice(letters) for _ in range(half))
    mid = random.choice(letters) if n % 2 else ""
    s = left + mid + left[::-1]
    if cv_alternating_ok(s):
        for _ in range(40):
            left = "".join(random.choice(letters) for _ in range(half))
            mid = random.choice(letters) if n % 2 else ""
            s = left + mid + left[::-1]
            if not cv_alternating_ok(s):
                break
    return s


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
    """Гость + удача: при нечётной длине — палиндром без CVCVC; иначе случайное имя."""
    if length < 3:
        return random_guest_username(length)
    if length % 2 == 1 and random.random() < 0.78:
        return _rnd_scatter_palindrome(length)
    return random_guest_username(length)
