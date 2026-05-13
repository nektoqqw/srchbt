"""Отложенный referrer_id, если /start с ref_ перехвачен гейтом канала до обработчика."""

from __future__ import annotations

_pending: dict[int, int] = {}


def stash_pending_referrer(user_id: int, referrer_id: int) -> None:
    if user_id <= 0 or referrer_id <= 0 or user_id == referrer_id:
        return
    _pending[user_id] = referrer_id


def take_pending_referrer(user_id: int) -> int | None:
    return _pending.pop(user_id, None)


def clear_pending_referrer(user_id: int) -> None:
    _pending.pop(user_id, None)
