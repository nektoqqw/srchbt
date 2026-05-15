"""Админ-функции Mini App (доступ по Telegram ID из ADMIN_IDS)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from admin_panel import is_admin
from config import Settings
from db import Database
from luck_tariffs import LUCK_TARIFFS, luck_tariff_by_key
from plus_tariffs import PLUS_TARIFFS, plus_tariff_by_key

log = logging.getLogger(__name__)

_PROMO_CODE_RE = re.compile(r"^[A-Za-z0-9_]{3,40}$")


def require_admin(uid: int, settings: Settings) -> bool:
    return is_admin(uid, settings)


def admin_dashboard(db: Database, settings: Settings) -> dict[str, Any]:
    blocked = db.is_search_globally_blocked()
    return {
        "ok": True,
        "search_blocked": blocked,
        "users_total": db.count_users(plus_only=False),
        "users_plus": db.count_users(plus_only=True),
        "promos": [
            {
                "code": row[0],
                "kind": row[1],
                "max_uses": row[2],
                "active": bool(row[3]),
                "uses": row[8],
            }
            for row in db.dynamic_promo_list(limit=40)
        ],
        "plus_tariffs": [{"key": t.key, "title": t.title_ru} for t in PLUS_TARIFFS],
        "luck_tariffs": [{"key": t.key, "title": t.title_ru} for t in LUCK_TARIFFS],
    }


def admin_toggle_search(db: Database) -> dict[str, Any]:
    blocked = db.is_search_globally_blocked()
    db.set_search_globally_blocked(not blocked)
    return {"ok": True, "search_blocked": not blocked}


def admin_grant(
    db: Database,
    *,
    target_uid: int,
    action: str,
    tariff_key: str = "",
    hours: int = 0,
) -> dict[str, Any]:
    if target_uid <= 0:
        return {"ok": False, "error": "invalid_uid"}
    db.get_or_create_user(target_uid)
    action = (action or "").strip().lower()

    if action == "plus_forever":
        db.set_plus_forever_paid(target_uid)
        return {"ok": True, "message": f"PLUS навсегда → {target_uid}"}
    if action == "plus_basic":
        db.set_plus(target_uid, True)
        return {"ok": True, "message": f"PLUS включён → {target_uid}"}
    if action == "plus_hours" and hours > 0:
        db.extend_plus_hours(target_uid, hours)
        return {"ok": True, "message": f"+{hours} ч PLUS → {target_uid}"}
    if action == "plus_tariff":
        t = plus_tariff_by_key(tariff_key)
        if not t:
            return {"ok": False, "error": "unknown_tariff"}
        if t.days is None:
            db.set_plus_forever_paid(target_uid)
        else:
            db.extend_plus_days(target_uid, t.days)
        return {"ok": True, "message": f"PLUS {t.title_ru} → {target_uid}"}
    if action == "luck_basic":
        db.set_luck(target_uid, True)
        return {"ok": True, "message": f"Удача → {target_uid}"}
    if action == "luck_tariff":
        if not db.is_plus(target_uid):
            return {"ok": False, "error": "target_needs_plus"}
        lt = luck_tariff_by_key(tariff_key)
        if not lt:
            return {"ok": False, "error": "unknown_tariff"}
        if lt.delta is None:
            db.set_luck_forever_paid(target_uid)
        else:
            db.extend_luck_delta(target_uid, lt.delta)
        return {"ok": True, "message": f"Удача {lt.title_ru} → {target_uid}"}
    if action == "luck_off":
        db.set_luck(target_uid, False)
        return {"ok": True, "message": f"Удача выкл. → {target_uid}"}

    return {"ok": False, "error": "unknown_action"}


async def admin_broadcast(
    db: Database,
    settings: Settings,
    *,
    mode: str,
    text: str,
    bot_token: str,
) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "empty_text"}
    if len(body) > 4090:
        return {"ok": False, "error": "too_long"}
    m = (mode or "all").strip().lower()
    if m == "plus":
        uids = db.list_plus_user_ids()
    else:
        uids = db.list_all_user_ids()
    if not uids:
        return {"ok": True, "sent": 0, "failed": 0}

    import aiohttp

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    ok_n = 0
    fail_n = 0
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for target in uids:
            try:
                async with session.post(
                    url,
                    json={"chat_id": target, "text": body, "parse_mode": "HTML"},
                ) as resp:
                    if resp.status < 400:
                        ok_n += 1
                    else:
                        fail_n += 1
            except Exception:
                fail_n += 1
            await asyncio.sleep(0.04)
    return {"ok": True, "sent": ok_n, "failed": fail_n, "total": len(uids)}


def admin_create_promo(
    db: Database,
    *,
    code: str,
    kind: str,
    max_uses: int,
    plus_days: int | None = None,
    plus_hours: int | None = None,
    luck_hours: int | None = None,
) -> dict[str, Any]:
    c = (code or "").strip().upper()
    if not _PROMO_CODE_RE.match(c):
        return {"ok": False, "error": "bad_code"}
    k = (kind or "plus").strip().lower()
    if k not in ("plus", "luck"):
        return {"ok": False, "error": "bad_kind"}
    ok, reason = db.dynamic_promo_create(
        c,
        k,
        max(0, int(max_uses)),
        plus_days=plus_days,
        plus_hours=plus_hours,
        luck_hours=luck_hours,
    )
    if ok:
        return {"ok": True, "code": c}
    return {"ok": False, "error": reason or "create_failed"}
