"""Парсинг deep-link /start ref_<telegram_id> из payload и из текста сообщения."""

from __future__ import annotations

import re

_START_CMD = re.compile(r"(?i)^/start(?:@[\w\d_]+)?(?:\s+(\S+))?\s*$")


def parse_referrer_id_from_start_payload(payload: str) -> int | None:
    """Параметр deep-link /start: ref_<telegram_id>."""
    raw = (payload or "").strip()
    if not raw or len(raw) > 64:
        return None
    m = re.fullmatch(r"(?i)ref_(\d{1,15})", raw)
    if not m:
        return None
    rid = int(m.group(1))
    return rid if rid > 0 else None


def start_arg_from_message_text(text: str | None) -> str | None:
    """Аргумент после /start из полного текста сообщения (как у Telegram deep-link)."""
    t = (text or "").strip()
    if not t:
        return None
    m = _START_CMD.match(t)
    if not m:
        return None
    g = m.group(1)
    return (g or "").strip() or None


def referrer_id_from_start_message_text(text: str | None) -> int | None:
    return parse_referrer_id_from_start_payload(start_arg_from_message_text(text) or "")
