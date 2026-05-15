"""Проверка Telegram WebApp initData (HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str, *, max_age_s: int = 86400) -> dict[str, Any] | None:
    """
    Возвращает распарсенный payload (включая user) или None при невалидной подписи.
    """
    raw = (init_data or "").strip()
    token = (bot_token or "").strip()
    if not raw or not token:
        return None

    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None

    auth_date = int(pairs.get("auth_date") or "0")
    if auth_date and max_age_s > 0:
        if time.time() - auth_date > max_age_s:
            return None

    out: dict[str, Any] = dict(pairs)
    if "user" in out and isinstance(out["user"], str):
        try:
            out["user"] = json.loads(out["user"])
        except json.JSONDecodeError:
            return None
    return out


def user_id_from_init_data(init_data: str, bot_token: str) -> int | None:
    parsed = validate_init_data(init_data, bot_token)
    if not parsed:
        return None
    user = parsed.get("user")
    if not isinstance(user, dict):
        return None
    try:
        uid = int(user.get("id"))
    except (TypeError, ValueError):
        return None
    return uid if uid > 0 else None
