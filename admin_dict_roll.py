"""Режим подбора «только из словаря» — только для админов (сессия в памяти)."""

from __future__ import annotations

from dataclasses import dataclass

_DICT_LENS = frozenset({5, 6, 7})


@dataclass
class AdminDictRoll:
    enabled: bool = False
    length: int = 5
    popular_first: bool = True

    def normalized(self) -> AdminDictRoll:
        ln = self.length if self.length in _DICT_LENS else 5
        return AdminDictRoll(
            enabled=bool(self.enabled),
            length=ln,
            popular_first=bool(self.popular_first),
        )


_store: dict[int, AdminDictRoll] = {}


def admin_dict_roll_get(uid: int) -> AdminDictRoll:
    return _store.get(uid, AdminDictRoll()).normalized()


def admin_dict_roll_set(
    uid: int,
    *,
    enabled: bool | None = None,
    length: int | None = None,
    popular_first: bool | None = None,
) -> AdminDictRoll:
    cur = admin_dict_roll_get(uid)
    if enabled is not None:
        cur = AdminDictRoll(
            enabled=bool(enabled),
            length=cur.length,
            popular_first=cur.popular_first,
        )
    if length is not None and int(length) in _DICT_LENS:
        cur = AdminDictRoll(
            enabled=cur.enabled,
            length=int(length),
            popular_first=cur.popular_first,
        )
    if popular_first is not None:
        cur = AdminDictRoll(
            enabled=cur.enabled,
            length=cur.length,
            popular_first=bool(popular_first),
        )
    out = cur.normalized()
    _store[uid] = out
    return out


def admin_dict_roll_payload(uid: int) -> dict[str, bool | int | str]:
    d = admin_dict_roll_get(uid)
    return {
        "enabled": d.enabled,
        "length": d.length,
        "popular_first": d.popular_first,
        "summary": admin_dict_roll_summary_ru(d),
    }


def admin_dict_roll_summary_ru(d: AdminDictRoll | None = None) -> str:
    if d is None:
        return "выкл."
    d = d.normalized()
    if not d.enabled:
        return "выкл."
    pop = "⭐" if d.popular_first else "↻"
    return f"вкл. · {d.length} букв {pop}"


def resolve_roll_dictionary_length(
    uid: int,
    *,
    is_admin: bool,
    requested_length: int,
) -> tuple[int, int | None]:
    """
    (effective_length, dictionary_length).
    dictionary_length задан — кандидаты только из english_words_5_7.
    """
    if not is_admin:
        return requested_length, None
    cfg = admin_dict_roll_get(uid)
    if not cfg.enabled:
        return requested_length, None
    return cfg.length, cfg.length
