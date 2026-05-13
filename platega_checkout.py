"""Создание платежа Platega и экран оплаты тарифа в Telegram."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram.types import CallbackQuery

from config import Settings
from db import Database
from luck_tariffs import (
    LuckTariff,
    kb_luck_payment_nav,
    luck_subscriber_status_banner_html,
    luck_tariff_payment_html,
)
from platega_api import create_platega_transaction, platega_configured
from plus_tariffs import (
    PlusTariff,
    kb_plus_payment_nav,
    plus_subscriber_status_banner_html,
    plus_tariff_payment_html,
)

log = logging.getLogger(__name__)

_PLATEGA_NO_BUTTON_HTML = (
    "\n\n<i>Онлайн-кнопка «Оплатить» не появилась. Проверьте в <code>.env</code>: "
    "<code>PLATEGA_MERCHANT_ID</code>, <code>PLATEGA_SECRET</code>, "
    "<code>PLATEGA_API_BASE</code> (по умолчанию как в доке: <code>https://app.platega.io</code>). "
    "Задайте <code>BOT_USERNAME_FOR_LINKS</code> или явные "
    "<code>PLATEGA_RETURN_URL</code> и <code>PLATEGA_FAILED_URL</code>. "
    "Смотрите логи процесса бота на ошибку от Platega.</i>"
)


async def _bot_username(settings: Settings, cb: CallbackQuery) -> str:
    u = (settings.bot_username_for_links or "").strip().lstrip("@")
    if u:
        return u
    me = await cb.bot.get_me()
    return (me.username or "").strip()


async def _platega_return_urls(settings: Settings, cb: CallbackQuery) -> tuple[str, str]:
    """
    URL после оплаты / отмены. Platega требует валидные URI; без @бота в Telegram
    всё равно подставляем заглушку, чтобы запрос к API не блокировался.
    """
    ret = (settings.platega_return_url or "").strip()
    fail = (settings.platega_failed_url or "").strip()
    if ret and fail:
        return ret, fail
    if ret and not fail:
        return ret, ret
    if fail and not ret:
        return fail, fail
    un = await _bot_username(settings, cb)
    if un:
        base = f"https://t.me/{un}"
        return ret or f"{base}?start=platega_ok", fail or f"{base}?start=platega_fail"
    return ret or "https://telegram.org", fail or "https://telegram.org"


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


def _tx_id_from_response(data: dict) -> str:
    t = data.get("transactionId") or data.get("id")
    return str(t).strip() if t is not None else ""


async def show_plus_tariff_payment_screen(
    cb: CallbackQuery,
    db: Database,
    settings: Settings,
    uid: int,
    t: PlusTariff,
) -> None:
    pay_url: str | None = None
    platega_note = False
    payment_hint = settings.plus_payment_hint
    pg_ok = platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    )
    if pg_ok:
        ret_url, fail_url = await _platega_return_urls(settings, cb)
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
            )
            pay_url = _pay_url_from_response(data)
            tx_id = _tx_id_from_response(data)
            if pay_url and not tx_id:
                log.warning(
                    "Platega PLUS: есть redirect, но нет transactionId — кнопку не показываем: %s",
                    data,
                )
                pay_url = None
            if pay_url and tx_id:
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
            elif not pay_url:
                log.warning(
                    "Platega PLUS: нет redirect в ответе (ключи %s): %s",
                    list(data.keys()) if isinstance(data, dict) else type(data),
                    data,
                )
        except Exception:
            log.exception("Platega create_transaction PLUS uid=%s tariff=%s", uid, t.key)
        if not pay_url:
            payment_hint = payment_hint + _PLATEGA_NO_BUTTON_HTML
    urow = db.get_or_create_user(uid)
    status_banner = plus_subscriber_status_banner_html(
        is_plus=bool(int(urow.is_plus)),
        plus_expires_at=urow.plus_expires_at,
    )
    await cb.message.edit_text(
        plus_tariff_payment_html(
            t,
            payment_hint=payment_hint,
            platega_auto_note=platega_note,
            online_pay_line=bool(pay_url),
            status_banner=status_banner,
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
    payment_hint = settings.luck_payment_hint
    pg_ok = platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    )
    if pg_ok:
        ret_url, fail_url = await _platega_return_urls(settings, cb)
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
            )
            pay_url = _pay_url_from_response(data)
            tx_id = _tx_id_from_response(data)
            if pay_url and not tx_id:
                log.warning(
                    "Platega luck: есть redirect, но нет transactionId — кнопку не показываем: %s",
                    data,
                )
                pay_url = None
            if pay_url and tx_id:
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
            elif not pay_url:
                log.warning(
                    "Platega luck: нет redirect в ответе (ключи %s): %s",
                    list(data.keys()) if isinstance(data, dict) else type(data),
                    data,
                )
        except Exception:
            log.exception("Platega create_transaction luck uid=%s tariff=%s", uid, t.key)
        if not pay_url:
            payment_hint = payment_hint + _PLATEGA_NO_BUTTON_HTML
    urow = db.get_or_create_user(uid)
    luck_banner = luck_subscriber_status_banner_html(
        is_luck=db.is_luck(uid),
        luck_forever=int(getattr(urow, "luck_forever", 0) or 0),
        luck_expires_at=urow.luck_expires_at,
    )
    await cb.message.edit_text(
        luck_tariff_payment_html(
            t,
            payment_hint=payment_hint,
            platega_auto_note=platega_note,
            online_pay_line=bool(pay_url),
            status_banner=luck_banner,
        ),
        parse_mode="HTML",
        reply_markup=kb_luck_payment_nav(pay_url=pay_url),
    )
