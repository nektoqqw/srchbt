"""Начисление PLUS / «Удача» после успешной оплаты Platega."""

from __future__ import annotations

import logging

from db import Database
from luck_tariffs import luck_tariff_by_key
from plus_tariffs import plus_tariff_by_key

log = logging.getLogger(__name__)


def apply_platega_purchase(db: Database, user_id: int, kind: str, tariff_key: str) -> bool:
    """
    Идемпотентно применить покупку (вызывать только после подтверждения оплаты).
    Возвращает True, если тариф найден и применён.
    """
    k = (tariff_key or "").strip().lower()
    kind = (kind or "").strip().lower()
    db.get_or_create_user(user_id)
    if kind == "plus":
        t = plus_tariff_by_key(k)
        if not t:
            log.error("Platega apply: неизвестный тариф PLUS %s", k)
            return False
        if t.days is None:
            db.set_plus_forever_paid(user_id)
        else:
            db.extend_plus_days(user_id, int(t.days))
        return True
    if kind == "luck":
        t = luck_tariff_by_key(k)
        if not t:
            log.error("Platega apply: неизвестный тариф luck %s", k)
            return False
        if t.delta is None:
            db.set_luck_forever_paid(user_id)
        else:
            db.extend_luck_delta(user_id, t.delta)
        return True
    log.error("Platega apply: неизвестный kind %s", kind)
    return False
