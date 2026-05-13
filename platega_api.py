"""Клиент Platega.io: создание транзакции (оплата). Документация: docs.platega.io — POST /transaction/process."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

# Базовый URL по документации Platega («Начало работы»): https://app.platega.io/
# При сбоях можно задать в .env другой хост, например PLATEGA_API_BASE=https://api.platega.io
DEFAULT_BASE = "https://app.platega.io"


def platega_configured(*, merchant_id: str, secret: str) -> bool:
    return bool((merchant_id or "").strip() and (secret or "").strip())


def create_platega_transaction(
    *,
    merchant_id: str,
    secret: str,
    api_base: str,
    payment_method: int,
    amount_rub: float,
    currency: str,
    description: str,
    return_url: str,
    failed_url: str,
    payload: str,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    """
    POST /transaction/process.

    Поле ``id`` в теле **нельзя** передавать — ID генерирует Platega (иначе ошибки / риск для магазина).
    """
    base = (api_base or DEFAULT_BASE).rstrip("/")
    url = f"{base}/transaction/process"
    body: dict[str, Any] = {
        "paymentMethod": int(payment_method),
        "paymentDetails": {"amount": float(amount_rub), "currency": currency.upper()},
        "description": (description or "")[:512],
        "return": return_url,
        "failedUrl": failed_url,
        "payload": (payload or "")[:1024],
    }
    headers = {
        "Content-Type": "application/json",
        "X-MerchantId": merchant_id.strip(),
        "X-Secret": secret.strip(),
    }
    log.info(
        "Platega запрос POST %s paymentMethod=%s amount=%s %s",
        url,
        int(payment_method),
        float(amount_rub),
        currency.upper(),
    )
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=timeout_s)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:500]}
    if r.status_code >= 400:
        log.warning(
            "Platega ошибка HTTP %s URL=%s тело=%s",
            r.status_code,
            url,
            data,
        )
        raise RuntimeError(f"Platega HTTP {r.status_code}: {data}")
    if not isinstance(data, dict):
        raise RuntimeError("Platega: не JSON-ответ")
    tid = data.get("transactionId") or data.get("id")
    has_redir = bool(
        data.get("redirect")
        or data.get("redirectUrl")
        or data.get("paymentUrl")
        or data.get("url")
    )
    log.info(
        "Platega ответ HTTP %s transactionId=%s есть_redirect=%s ключи_JSON=%s",
        r.status_code,
        tid,
        has_redir,
        list(data.keys()),
    )
    return data


def get_platega_transaction(
    *,
    merchant_id: str,
    secret: str,
    api_base: str,
    transaction_id: str,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    base = (api_base or DEFAULT_BASE).rstrip("/")
    url = f"{base}/transaction/{transaction_id.strip()}"
    headers = {
        "X-MerchantId": merchant_id.strip(),
        "X-Secret": secret.strip(),
    }
    r = requests.get(url, headers=headers, timeout=timeout_s)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:500]}
    if r.status_code >= 400:
        log.warning("Platega GET %s: %s", r.status_code, data)
        raise RuntimeError(f"Platega HTTP {r.status_code}: {data}")
    if not isinstance(data, dict):
        raise RuntimeError("Platega: не JSON-ответ")
    return data
