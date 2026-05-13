"""Парсинг deep-link /start ref_<telegram_id> (payload, текст, base64 как у Telegram)."""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

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


def _normalize_start_payload_token(raw: str) -> str:
    """Один токен после /start: как есть или после base64url (Telegram deep links)."""
    s = (raw or "").strip()
    if not s or len(s) > 64:
        return ""
    if re.fullmatch(r"(?i)ref_\d{1,15}", s):
        return s
    try:
        from aiogram.utils.deep_linking import decode_payload

        decoded = decode_payload(s)
        return (decoded or "").strip()
    except Exception:
        log.debug("decode_payload failed for start token", exc_info=True)
        return s


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
    arg = start_arg_from_message_text(text)
    if not arg:
        return None
    return parse_referrer_id_from_start_payload(_normalize_start_payload_token(arg))


def referrer_id_for_start(
    *,
    command_args: str | None,
    message_text: str | None,
) -> int | None:
    """
    Устойчивое извлечение ref_: из CommandObject.args, из текста /start, с base64 payload.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    a = (command_args or "").strip()
    if a:
        candidates.append(a)
        seen.add(a)
    b = start_arg_from_message_text(message_text) or ""
    if b and b not in seen:
        candidates.append(b)
        seen.add(b)
    for raw in candidates:
        norm = _normalize_start_payload_token(raw)
        rid = parse_referrer_id_from_start_payload(norm)
        if rid is not None:
            return rid
    return None


def referrer_id_from_command(command: Any, message_text: str | None) -> int | None:
    return referrer_id_for_start(
        command_args=getattr(command, "args", None), message_text=message_text
    )
