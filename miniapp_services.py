"""Бизнес-логика Telegram Mini App (общая с ботом БД и сервисами)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from admin_panel import (
    is_admin,
    legal_documents_user_html,
    luck_promo_entry_available,
    redeem_luck_code,
    redeem_plus_code,
)
from checker import is_valid_telegram_username, normalize_username
from config import Settings
from db import Database
from fragment_scraper import username_listed_on_fragment
from luck_tariffs import LUCK_TARIFFS, luck_tariff_by_key
from miniapp_roll import find_one_username_fragment_miniapp
from platega_api import create_platega_transaction, platega_configured
from platega_checkout import _pay_url_from_response, _tx_id_from_response
from platega_sync import finalize_pending_platega_for_user
from plus_tariffs import PLUS_TARIFFS, plus_tariff_by_key
from roll_filters import RollFilters, filters_summary_ru
from tariff_pricing import sale_price_float, sale_price_rub
from username_rarity import combined_rarity
from username_valuation import evaluate_username_market

log = logging.getLogger(__name__)


def _parse_usernames(text: str) -> list[str]:
    from bot_aiogram import parse_usernames_from_user_input

    return parse_usernames_from_user_input(text)


def _build_checker(settings: Settings) -> Any:
    from bot_aiogram import build_checker

    return build_checker(settings)


@dataclass
class UserSession:
    filters: RollFilters = field(default_factory=RollFilters)
    last_roll: dict[str, Any] | None = None
    last_roll_at: float = 0.0


@dataclass
class RollJob:
    job_id: str
    user_id: int
    status: str  # running | done | error
    progress: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None


_sessions: dict[int, UserSession] = {}
_roll_jobs: dict[str, RollJob] = {}
_checker: Any | None = None
_checker_lock = asyncio.Lock()


async def get_checker(settings: Settings) -> Any:
    global _checker
    async with _checker_lock:
        if _checker is None:
            _checker = _build_checker(settings)
            if getattr(_checker, "uses_telethon", False):
                await _checker.start()
        return _checker


def _sess(uid: int) -> UserSession:
    if uid not in _sessions:
        _sessions[uid] = UserSession()
    return _sessions[uid]


def _strip_url(raw: str | None) -> str:
    return (raw or "").strip().strip('"').strip("'")


def _valid_url(url: str) -> bool:
    s = (url or "").strip()
    if len(s) < 12:
        return False
    try:
        p = urlparse(s)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def platega_urls(settings: Settings, bot_username: str) -> tuple[str, str]:
    ret = _strip_url(settings.platega_return_url)
    fail = _strip_url(settings.platega_failed_url)
    un = (bot_username or settings.bot_username_for_links or "").strip().lstrip("@")
    default_ok = f"https://t.me/{un}?start=platega_ok" if un else "https://telegram.org/"
    default_fail = f"https://t.me/{un}?start=platega_fail" if un else "https://telegram.org/"
    if ret and _valid_url(ret):
        out_ok = ret
    else:
        out_ok = default_ok
    if fail and _valid_url(fail):
        out_fail = fail
    else:
        out_fail = default_fail
    return out_ok, out_fail


def _format_expires(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК")
    except Exception:
        return str(raw)


def cabinet_payload(db: Database, uid: int, settings: Settings) -> dict[str, Any]:
    u = db.get_or_create_user(uid)
    rem = db.searches_remaining(uid, settings.free_search_limit)
    return {
        "user_id": uid,
        "is_admin": is_admin(uid, settings),
        "is_plus": bool(int(u.is_plus)),
        "plus_expires": _format_expires(u.plus_expires_at),
        "has_luck": bool(db.is_luck(uid)),
        "luck_expires": _format_expires(u.luck_expires_at),
        "luck_forever": bool(int(u.luck_forever)),
        "luck_roll_paused": bool(int(getattr(u, "luck_roll_paused", 0))),
        "luck_active_in_roll": db.is_luck_roll_active(uid),
        "searches_remaining": rem,
        "searches_limit": settings.free_search_limit,
        "referral_count": db.referral_count(uid),
        "referral_bonus_hours": settings.referral_plus_hours,
        "search_blocked": db.is_search_globally_blocked(),
        "bot_mode": settings.bot_mode,
        "display_name": u.display_name,
        "ton_to_usd": settings.ton_to_usd,
    }


def set_display_name(db: Database, uid: int, raw: str) -> dict[str, Any]:
    ok, reason = db.set_display_name(uid, raw)
    if ok:
        name = db.get_display_name(uid)
        return {"ok": True, "display_name": name}
    err = {
        "too_long": "Слишком длинное имя (макс. 32 символа)",
        "invalid_chars": "Недопустимые символы",
    }
    return {"ok": False, "error": reason, "message": err.get(reason, reason)}


def filters_payload(uid: int) -> dict[str, Any]:
    s = _sess(uid)
    flt = s.filters.normalized(max_len=7)
    s.filters = flt
    return {
        "prefix": flt.prefix,
        "suffix": flt.suffix,
        "digits": flt.digits,
        "summary": filters_summary_ru(flt),
        "active": flt.active(),
    }


def set_filters(uid: int, *, prefix: str, suffix: str, digits: str) -> dict[str, Any]:
    s = _sess(uid)
    s.filters = RollFilters(
        prefix=prefix or "",
        suffix=suffix or "",
        digits=digits if digits in ("any", "yes", "no") else "any",
    ).normalized(max_len=7)
    return filters_payload(uid)


def referral_payload(db: Database, settings: Settings, uid: int, bot_username: str) -> dict[str, Any]:
    un = (bot_username or settings.bot_username_for_links or "").strip().lstrip("@")
    if not un:
        return {"ok": False, "error": "bot_username_unknown"}
    return {
        "ok": True,
        "link": f"https://t.me/{un}?start=ref_{uid}",
        "count": db.referral_count(uid),
        "bonus_hours": settings.referral_plus_hours,
    }


def documents_payload(db: Database) -> dict[str, Any]:
    terms = db.get_legal_document_url("terms")
    privacy = db.get_legal_document_url("privacy")
    return {
        "html": legal_documents_user_html(db),
        "terms_url": terms or None,
        "privacy_url": privacy or None,
    }


def tariffs_payload() -> dict[str, Any]:
    plus = [
        {
            "key": t.key,
            "title": t.title_ru,
            "price_rub": sale_price_rub(t.price_rub),
            "price_base": t.price_rub,
            "days": t.days,
        }
        for t in PLUS_TARIFFS
    ]
    luck = [
        {
            "key": t.key,
            "title": t.title_ru,
            "price_rub": sale_price_rub(t.price_rub),
            "price_base": t.price_rub,
        }
        for t in LUCK_TARIFFS
    ]
    return {"plus": plus, "luck": luck}


async def create_checkout(
    db: Database,
    settings: Settings,
    uid: int,
    *,
    kind: str,
    tariff_key: str,
    bot_username: str,
) -> dict[str, Any]:
    if not platega_configured(
        merchant_id=settings.platega_merchant_id, secret=settings.platega_secret
    ):
        return {"ok": False, "error": "platega_not_configured"}

    if kind == "plus":
        t = plus_tariff_by_key(tariff_key)
        if not t:
            return {"ok": False, "error": "unknown_tariff"}
        amount = sale_price_float(t.price_rub)
        desc = f"PLUS {t.title_ru}"
        payload = f"tg:{uid}:plus:{t.key}"
    elif kind == "luck":
        if not db.is_plus(uid):
            return {"ok": False, "error": "plus_required"}
        t = luck_tariff_by_key(tariff_key)
        if not t:
            return {"ok": False, "error": "unknown_tariff"}
        amount = sale_price_float(t.price_rub)
        desc = f"Удача {t.title_ru}"
        payload = f"tg:{uid}:luck:{t.key}"
    else:
        return {"ok": False, "error": "invalid_kind"}

    if settings.platega_test_amount_rub and settings.platega_test_amount_rub > 0:
        amount = float(settings.platega_test_amount_rub)

    ret_url, fail_url = platega_urls(settings, bot_username)
    try:
        data = await asyncio.to_thread(
            create_platega_transaction,
            merchant_id=settings.platega_merchant_id,
            secret=settings.platega_secret,
            api_base=settings.platega_api_base,
            payment_method=settings.platega_payment_method,
            amount_rub=amount,
            currency="RUB",
            description=desc,
            return_url=ret_url,
            failed_url=fail_url,
            payload=payload,
            universal_payment_form=settings.platega_v2_universal,
        )
    except Exception as e:
        log.exception("miniapp platega create")
        return {"ok": False, "error": str(e)[:200]}

    pay_url = _pay_url_from_response(data)
    tx_id = _tx_id_from_response(data)
    if not pay_url or not tx_id:
        return {"ok": False, "error": "no_pay_url", "raw": data}

    import sqlite3

    try:
        db.platega_insert_pending(
            transaction_id=tx_id,
            user_id=uid,
            product_kind=kind,
            tariff_key=tariff_key,
            amount_rub=amount,
            currency="RUB",
            pay_url=pay_url,
        )
    except sqlite3.IntegrityError:
        log.warning("duplicate platega tx %s", tx_id)

    return {"ok": True, "pay_url": pay_url, "transaction_id": tx_id}


async def sync_payments(db: Database, settings: Settings, uid: int) -> dict[str, Any]:
    _n, msgs = await finalize_pending_platega_for_user(uid, db, settings)
    return {"ok": True, "messages": msgs, "activated": _n}


def redeem_promo(
    db: Database, settings: Settings, uid: int, code: str, kind: str
) -> dict[str, Any]:
    if kind == "plus":
        ok, reason, plus_days, plus_hours = redeem_plus_code(code, uid, db=db, settings=settings)
        if ok:
            from bot_aiogram import _apply_redeemed_plus

            line = _apply_redeemed_plus(db, uid, plus_days=plus_days, plus_hours=plus_hours)
            return {"ok": True, "message": line}
        return {"ok": False, "reason": reason}
    if kind == "luck":
        if not luck_promo_entry_available(settings, db):
            return {"ok": False, "reason": "no_promos"}
        ok, reason, luck_hours = redeem_luck_code(code, uid, db=db, settings=settings)
        if ok:
            from bot_aiogram import _apply_redeemed_luck

            line = _apply_redeemed_luck(db, uid, luck_hours)
            return {"ok": True, "message": line}
        return {"ok": False, "reason": reason}
    return {"ok": False, "reason": "invalid_kind"}


def toggle_luck_pause(db: Database, uid: int) -> dict[str, Any]:
    u = db.get_or_create_user(uid)
    if not db.is_plus(uid) or not db.is_luck(uid):
        return {"ok": False, "error": "plus_and_luck_required"}
    paused = int(getattr(u, "luck_roll_paused", 0))
    db.set_luck_roll_paused(uid, paused=not paused)
    return {"ok": True, "paused": not paused}


async def run_valuation(
    db: Database, settings: Settings, uid: int, raw: str, checker: Any
) -> dict[str, Any]:
    tokens = _parse_usernames(raw)
    if not tokens:
        return {"ok": False, "error": "no_usernames"}

    listed_map: dict[str, bool | None] = {}
    if settings.bot_mode == "fragment" and len(tokens) <= 3:
        for t in tokens:
            if not is_valid_telegram_username(t):
                listed_map[t] = None
                continue
            try:
                listed_map[t] = await asyncio.to_thread(
                    username_listed_on_fragment, t, timeout_s=22
                )
            except Exception:
                listed_map[t] = None

    items: list[dict[str, Any]] = []
    for t in tokens:
        if not is_valid_telegram_username(t):
            items.append({"username": t, "error": "invalid_format"})
            continue
        rep = evaluate_username_market(t, db, ton_to_usd=settings.ton_to_usd)
        frag = None
        if t in listed_map and listed_map[t] is not None:
            frag = bool(listed_map[t])
        ri, pred, why = combined_rarity(t, db, ton_to_usd=settings.ton_to_usd)
        price = pred if pred is not None else rep.estimated_price_usd
        items.append(
            {
                "username": t,
                "price_usd": price,
                "rarity": ri.name,
                "why": why,
                "rank": rep.rank_10,
                "stars": rep.stars_5,
                "pros": rep.pros,
                "cons": rep.cons,
                "fragment_listed": frag,
            }
        )
    return {"ok": True, "items": items}


async def start_roll_job(
    db: Database,
    settings: Settings,
    uid: int,
    *,
    length: int,
) -> dict[str, Any]:
    if length not in (5, 6, 7):
        return {"ok": False, "error": "invalid_length"}
    if db.is_search_globally_blocked():
        return {"ok": False, "error": "search_blocked"}
    if not db.can_search(uid, settings.free_search_limit):
        return {"ok": False, "error": "no_attempts"}

    is_plus = db.is_plus(uid)
    flt = _sess(uid).filters.normalized(max_len=length)
    if flt.active() and not is_plus:
        return {"ok": False, "error": "filters_need_plus"}

    job_id = uuid.uuid4().hex
    job = RollJob(job_id=job_id, user_id=uid, status="running")
    _roll_jobs[job_id] = job

    async def _run() -> None:
        try:
            checker = await get_checker(settings)
            lucky = db.is_luck_roll_active(uid)
            max_attempts = 420 if is_plus else 140
            if getattr(checker, "uses_telethon", False):
                max_attempts = min(1400, int(max_attempts * 1.45))

            async def on_prog(n: int) -> None:
                job.progress = n

            found, attempts, timed_out = await find_one_username_fragment_miniapp(
                length=length,
                max_attempts=max_attempts,
                delay_s=settings.fragment_request_delay_s,
                lucky=lucky,
                checker=checker,
                filters=flt,
                fragment_timeout_s=settings.fragment_roll_timeout_s,
                is_plus=is_plus,
                on_progress=on_prog,
            )
            if not timed_out:
                db.increment_search(uid)

            if found:
                ri, pred, why = combined_rarity(
                    found, db, ton_to_usd=settings.ton_to_usd
                )
                db.add_roll_event(
                    user_id=uid,
                    username=found.lower(),
                    rarity=ri.name,
                    predicted_price_usd=pred,
                )
                res = {
                    "found": True,
                    "username": found.lower(),
                    "attempts": attempts,
                    "rarity": ri.name,
                    "price_usd": pred,
                    "why": why,
                    "timed_out": timed_out,
                }
                _sess(uid).last_roll = res
                _sess(uid).last_roll_at = time.time()
            else:
                res = {
                    "found": False,
                    "attempts": attempts,
                    "timed_out": timed_out,
                }
            job.status = "done"
            job.result = res
        except Exception as e:
            log.exception("roll job %s", job_id)
            job.status = "error"
            job.error = str(e)[:300]

    asyncio.create_task(_run())
    return {"ok": True, "job_id": job_id}


def roll_job_status(job_id: str, uid: int) -> dict[str, Any] | None:
    job = _roll_jobs.get(job_id)
    if not job or job.user_id != uid:
        return None
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
    }


def list_saved(db: Database, uid: int) -> list[str]:
    return db.list_saved(uid)


def save_username(db: Database, uid: int, username: str) -> dict[str, Any]:
    if not db.is_plus(uid):
        return {"ok": False, "error": "plus_required"}
    u = normalize_username(username)
    if not is_valid_telegram_username(u):
        return {"ok": False, "error": "invalid_username"}
    ok = db.save_username(uid, u)
    return {"ok": ok, "error": None if ok else "limit_or_duplicate"}


def delete_saved(db: Database, uid: int, username: str) -> dict[str, Any]:
    db.remove_saved(uid, normalize_username(username))
    return {"ok": True}
