"""Фильтры подбора имени (PLUS): префикс/суффикс, режим цифр."""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass

from fragment_scraper import random_letters_username_length
from luck_username import random_free_lucky_username, random_lucky_username
from username_cv import random_guest_username

_LETTERS = string.ascii_lowercase
_DIGITS = string.digits


@dataclass
class RollFilters:
    """Префикс/суффикс — только строчные латинские буквы, до 2 символов с каждой стороны."""

    prefix: str = ""
    suffix: str = ""
    digits: str = "any"  # any | yes | no

    def active(self) -> bool:
        return bool(self.prefix or self.suffix or self.digits != "any")

    def normalized(self, *, max_len: int) -> RollFilters:
        pre = re.sub(r"[^a-z]", "", (self.prefix or "").lower())[:2]
        suf = re.sub(r"[^a-z]", "", (self.suffix or "").lower())[:2]
        dig = self.digits if self.digits in ("any", "yes", "no") else "any"
        if len(pre) + len(suf) > max_len:
            suf = suf[: max(0, max_len - len(pre))]
        return RollFilters(prefix=pre, suffix=suf, digits=dig)


def _random_middle(length: int, *, allow_digit: bool) -> str:
    if length <= 0:
        return ""
    alphabet = _LETTERS + _DIGITS + "_" if allow_digit else _LETTERS
    out: list[str] = []
    for i in range(length):
        if i == 0 and not allow_digit:
            out.append(random.choice(_LETTERS))
        else:
            out.append(random.choice(alphabet))
    if not allow_digit:
        return "".join(out)
    if not any(c in _DIGITS for c in out):
        pos = random.randrange(length)
        if pos == 0:
            pos = 1 if length > 1 else 0
        out[pos] = random.choice(_DIGITS)
    return "".join(out)


def username_roll_random(length: int, *, lucky: bool, plus_full_cv: bool) -> str:
    """PLUS: чередование CVC… и режим «Удача»; гость: случайные буквы без CV, при удаче — симметрия."""
    if lucky:
        if plus_full_cv:
            return random_lucky_username(length)
        return random_free_lucky_username(length)
    if plus_full_cv:
        return random_letters_username_length(length)
    return random_guest_username(length)


def generate_roll_candidate(
    length: int,
    *,
    lucky: bool,
    filters: RollFilters,
    plus_full_cv: bool = True,
) -> str:
    """
    Без фильтров: у PLUS — чередование CVC… и при удаче палиндромы/края (без удвоений);
    у гостя — случайные буквы без CVCVC, при удаче чаще палиндром «край–край».
    С фильтрами — строка a-z / a-z0-9_ (первая буква всегда буква).
    """
    fl = filters.normalized(max_len=length)
    if not fl.active():
        return username_roll_random(length, lucky=lucky, plus_full_cv=plus_full_cv)

    pre, suf = fl.prefix, fl.suffix
    core_len = length - len(pre) - len(suf)
    if core_len < 0:
        return username_roll_random(length, lucky=lucky, plus_full_cv=plus_full_cv)

    allow_digit = fl.digits != "no"
    need_digit = fl.digits == "yes"

    if core_len == 0:
        cand = pre + suf
        if (
            len(cand) == length
            and cand[0] in _LETTERS
            and re.fullmatch(rf"[a-z][a-z0-9_]{{{length - 1}}}", cand)
            and (not need_digit or any(c in _DIGITS for c in cand))
            and not (fl.digits == "no" and any(c in _DIGITS for c in cand))
        ):
            return cand
        return username_roll_random(length, lucky=lucky, plus_full_cv=plus_full_cv)

    for _ in range(80):
        core = _random_middle(core_len, allow_digit=allow_digit)
        if core_len > 0 and not core:
            continue
        if not pre and core and core[0] not in _LETTERS:
            continue
        cand = pre + core + suf
        if len(cand) != length:
            continue
        if cand[0] not in _LETTERS:
            continue
        if not re.fullmatch(rf"[a-z][a-z0-9_]{{{length - 1}}}", cand):
            continue
        if need_digit and not any(c in _DIGITS for c in cand):
            continue
        if fl.digits == "no" and any(c in _DIGITS for c in cand):
            continue
        return cand

    return username_roll_random(length, lucky=lucky, plus_full_cv=plus_full_cv)


def filters_summary_ru(f: RollFilters) -> str:
    fl = f.normalized(max_len=7)
    if not fl.active():
        return "как обычно"
    bits: list[str] = []
    if fl.prefix:
        bits.append(f"нач.{fl.prefix}")
    if fl.suffix:
        bits.append(f"кон.{fl.suffix}")
    if fl.digits == "yes":
        bits.append("с цифр.")
    elif fl.digits == "no":
        bits.append("без цифр")
    return " · ".join(bits) if bits else "как обычно"
