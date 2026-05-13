"""Создание платежа Platega и экран оплаты тарифа в Telegram."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid

from aiogram.types import CallbackQuery

from config import Settings
from db import Database
from luck_tariffs import LuckTariff, kb_luck_payment_nav, luck_tariff_payment_html
from platega_api import create_platega_transaction, platega_configured
from plus_tariffs import PlusTariff, kb_plus_payment_nav, plus_tariff_payment_html

log = logging.getLogger(__name__)


async def _bot_username(settings: Settings, cb: CallbackQuery) -> str:
    u = (settings.bot_username_for_links or "").strip().lstrip("@")
    if u:
        return u
    me = await cb.bot.get_me()
    return (me.username or "").strip()


async def _platega_return_urls(settings: Settings, cb: CallbackQuery) -> tuple[str, str] | None:
    un = await _bot_username(settings, cb)
    if not un:
        return None
    base = f"https://t.me/{un}"
    ret = (settings.platega_return_url or "").strip() or f"{base}?start=platega_ok"
    fail = (settings.platega_failed_url or "").strip() or f"{base}?start=platega_fail"
    return ret, fail


def _normalize_pay_url(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    lo = s.lower()
    if lo.startswith("//"):
        s = "https:" + s
        lo = s.lower()
    if lo.startswith("https://") or lo.startswith("http://"):
        return s
    return None


def _pay_url_from_response(data: dict) -> str | None:
    """Достаём ссылку на оплату из типичных вариантов ответа Platega."""
    keys = (
        "redirect",
        "redirectUrl",
        "redirect_url",
        "paymentUrl",
        "payment_url",
        "url",
        "link",
        "payUrl",
        "pay_url",
        "paymentLink",
        "payment_link",
    )
    for key in keys:
        u = _normalize_pay_url(data.get(key))
        if u:
            return u
    for nest_key in ("data", "result", "transaction", "body"):
        inner = data.get(nest_key)
        if isinstance(inner, dict):
            for key in keys:
                u = _normalize_pay_url(inner.get(key))
                if u:
                    return u
    return None


def _tx_id_from_response(data: dict, client_tx: str) -> str:
    t = data.get("transactionId") or data.get("id") or client_tx
    s = str(t).strip()
    return s or client_tx


async def show_plus_tariff_payment_screen(
    cb: CallbackQuery,
    db: Database,
    settings: Settings,
    uid: int,
    t: PlusTariff,
) -> None:
    pay_url: str | None = None
    platega_note = False
    if platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    ):
        urls = await _platega_return_urls(settings, cb)
        if not urls:
            log.warning(
                "Platega: задайте BOT_USERNAME_FOR_LINKS или username у бота, иначе нет return URL"
            )
        else:
            ret_url, fail_url = urls
            client_tx = str(uuid.uuid4())
            payload = f"tg:{uid}:plus:{t.key}"
            try:
                data = await asyncio.to_thread(
                    create_platega_transaction,
                    merchant_id=settings.platega_merchant_id,
                    secret=settings.platega_secret,
                    api_base=settings.platega_api_base,
                    payment_method=settings.platega_payment_method,
                    amount_rub=float(t.price_rub),
                    currency="RUB",
                    description=f"PLUS {t.title_ru}",
                    return_url=ret_url,
                    failed_url=fail_url,
                    payload=payload,
                    client_transaction_id=client_tx,
                )
                pay_url = _pay_url_from_response(data)
                tx_id = _tx_id_from_response(data, client_tx)
                if pay_url:
                    try:
                        db.platega_insert_pending(
                            transaction_id=tx_id,
                            user_id=uid,
                            product_kind="plus",
                            tariff_key=t.key,
                            amount_rub=float(t.price_rub),
                            currency="RUB",
                            pay_url=pay_url,
                        )
                        platega_note = True
                    except sqlite3.IntegrityError:
                        log.warning("Platega: дубликат transaction_id %s", tx_id)
                        platega_note = True
            except Exception:
                log.exception("Platega create_transaction PLUS uid=%s tariff=%s", uid, t.key)
    await cb.message.edit_text(
        plus_tariff_payment_html(
            t,
            payment_hint=settings.plus_payment_hint,
            platega_auto_note=platega_note,
            online_pay_line=bool(pay_url),
        ),
        parse_mode="HTML",
        reply_markup=kb_plus_payment_nav(pay_url=pay_url),
    )


async def show_luck_tariff_payment_screen(
    cb: CallbackQuery,
    db: Database,
    settings: Settings,
    uid: int,
    t: LuckTariff,
) -> None:
    pay_url: str | None = None
    platega_note = False
    if platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    ):
        urls = await _platega_return_urls(settings, cb)
        if not urls:
            log.warning(
                "Platega: задайте BOT_USERNAME_FOR_LINKS или username у бота, иначе нет return URL"
            )
        else:
            ret_url, fail_url = urls
            client_tx = str(uuid.uuid4())
            payload = f"tg:{uid}:luck:{t.key}"
            try:
                data = await asyncio.to_thread(
                    create_platega_transaction,
                    merchant_id=settings.platega_merchant_id,
                    secret=settings.platega_secret,
                    api_base=settings.platega_api_base,
                    payment_method=settings.platega_payment_method,
                    amount_rub=float(t.price_rub),
                    currency="RUB",
                    description=f"Удача {t.title_ru}",
                    return_url=ret_url,
                    failed_url=fail_url,
                    payload=payload,
                    client_transaction_id=client_tx,
                )
                pay_url = _pay_url_from_response(data)
                tx_id = _tx_id_from_response(data, client_tx)
                if pay_url:
                    try:
                        db.platega_insert_pending(
                            transaction_id=tx_id,
                            user_id=uid,
                            product_kind="luck",
                            tariff_key=t.key,
                            amount_rub=float(t.price_rub),
                            currency="RUB",
                            pay_url=pay_url,
                        )
                        platega_note = True
                    except sqlite3.IntegrityError:
                        log.warning("Platega: дубликат transaction_id %s", tx_id)
                        platega_note = True
            except Exception:
                log.exception("Platega create_transaction luck uid=%s tariff=%s", uid, t.key)
    await cb.message.edit_text(
        luck_tariff_payment_html(
            t,
            payment_hint=settings.luck_payment_hint,
            platega_auto_note=platega_note,
            online_pay_line=bool(pay_url),
        ),
        parse_mode="HTML",
        reply_markup=kb_luck_payment_nav(pay_url=pay_url),
    )
