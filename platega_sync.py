"""Разбор ответов Platega и синхронизация оплат (GET), если webhook не сработал."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import Settings
from db import Database
from platega_api import get_platega_transaction, platega_configured
from platega_apply import apply_platega_purchase

log = logging.getLogger(__name__)


def transaction_status_from_json(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for k in ("status", "Status", "transactionStatus", "state"):
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip().upper()
    for nest in ("data", "transaction", "result"):
        inner = data.get(nest)
        if isinstance(inner, dict):
            s = transaction_status_from_json(inner)
            if s:
                return s
    return ""


def paid_amount_from_json(data: dict[str, Any]) -> float | None:
    if not isinstance(data, dict):
        return None
    for k in ("amount", "Amount"):
        raw = data.get(k)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    pd = data.get("paymentDetails")
    if isinstance(pd, dict):
        raw = pd.get("amount")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    if isinstance(pd, str):
        # иногда "100 RUB"
        parts = str(pd).strip().split()
        if parts:
            try:
                return float(parts[0].replace(",", "."))
            except ValueError:
                pass
    for nest in ("data", "transaction", "result"):
        inner = data.get(nest)
        if isinstance(inner, dict):
            a = paid_amount_from_json(inner)
            if a is not None:
                return a
    return None


def transaction_id_from_json(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for k in ("transactionId", "id", "transaction_id"):
        v = data.get(k)
        if v is not None:
            s = str(v).strip()
            if s:
                return s
    for nest in ("data", "transaction", "result"):
        inner = data.get(nest)
        if isinstance(inner, dict):
            s = transaction_id_from_json(inner)
            if s:
                return s
    return ""


async def finalize_pending_platega_for_user(
    uid: int,
    db: Database,
    settings: Settings,
) -> tuple[int, list[str]]:
    """
    Опрашивает Platega по заказам пользователя в статусе PENDING; при CONFIRMED начисляет PLUS/Удачу.
    Возвращает (сколько раз успешно начислили, тексты для пользователя).
    """
    if not platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    ):
        return 0, []
    tids = db.platega_pending_transaction_ids_for_user(uid, limit=12)
    if not tids:
        return 0, []
    msgs: list[str] = []
    ok_n = 0
    for tid in tids:
        try:
            data = await asyncio.to_thread(
                get_platega_transaction,
                merchant_id=settings.platega_merchant_id,
                secret=settings.platega_secret,
                api_base=settings.platega_api_base,
                transaction_id=tid,
            )
        except Exception:
            log.warning("Platega GET transaction %s", tid, exc_info=True)
            continue
        st = transaction_status_from_json(data)
        if st != "CONFIRMED":
            continue
        amt = paid_amount_from_json(data)
        grant = db.platega_try_confirm(tid, amt)
        if not grant:
            grant = db.platega_try_confirm(tid, None)
        if not grant:
            continue
        user_id, kind, tkey = grant
        if user_id != uid:
            log.warning("Platega sync: uid mismatch order=%s db_uid=%s", tid, user_id)
            continue
        if apply_platega_purchase(db, user_id, kind, tkey):
            ok_n += 1
            if kind == "plus":
                msgs.append("<b>PLUS</b> активирован — загляните в «Аккаунт».")
            elif kind == "luck":
                msgs.append("Режим <b>«Удача»</b> продлён.")
    return ok_n, msgs
