"""Клиент Platega.io: создание транзакции (оплата). Документация: app.platega.io /transaction/process."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import requests

log = logging.getLogger(__name__)

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
    client_transaction_id: str | None = None,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    """
    POST /transaction/process.
    В теле передаём свой ``id`` (UUID), чтобы не дублировать транзакции при повторе запроса.
    """
    base = (api_base or DEFAULT_BASE).rstrip("/")
    url = f"{base}/transaction/process"
    tx_id = client_transaction_id or str(uuid.uuid4())
    body: dict[str, Any] = {
        "paymentMethod": int(payment_method),
        "id": tx_id,
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
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=timeout_s)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text[:500]}
    if r.status_code >= 400:
        log.warning("Platega HTTP %s: %s", r.status_code, data)
        raise RuntimeError(f"Platega HTTP {r.status_code}: {data}")
    if not isinstance(data, dict):
        raise RuntimeError("Platega: не JSON-ответ")
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
