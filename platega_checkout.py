"""Создание платежа Platega и экран оплаты тарифа в Telegram."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from urllib.parse import urlparse

from aiogram.types import CallbackQuery

from config import Settings
from db import Database
import ui_theme as theme
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
from tariff_pricing import sale_price_float

log = logging.getLogger(__name__)

_PLATEGA_PAYMENT_HINT_OK = ""
_PLATEGA_PAYMENT_HINT_NO_KEYS = (
    "<i>Владельцу бота нужно задать в <code>.env</code> "
    "<code>PLATEGA_MERCHANT_ID</code> и <code>PLATEGA_SECRET</code>.</i>"
)

_PLATEGA_NO_BUTTON_HTML = (
    "\n\n<i>Платёж не оформлен. Проверьте ключи и URL в <code>.env</code> "
    "и логи бота (строки с <code>Platega</code>).</i>"
)


async def _bot_username(settings: Settings, cb: CallbackQuery) -> str:
    u = (settings.bot_username_for_links or "").strip().lstrip("@")
    if u:
        return u
    me = await cb.bot.get_me()
    return (me.username or "").strip()


def _strip_url_env(raw: str | None) -> str:
    """Убираем пробелы и типичные кавычки из .env."""
    return (raw or "").strip().strip('"').strip("'")


def _valid_absolute_http_url(url: str) -> bool:
    """Platega требует валидный абсолютный URL (схема + хост)."""
    s = (url or "").strip()
    if len(s) < 12:
        return False
    try:
        p = urlparse(s)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.netloc:
        return False
    return "." in p.netloc or p.netloc == "localhost"


async def _platega_return_urls(settings: Settings, cb: CallbackQuery) -> tuple[str, str]:
    """
    URL после оплаты / отмены. Невалидные значения из .env отбрасываются,
    иначе Platega отвечает 400 «URL is not a valid absolute URL».
    """
    ret = _strip_url_env(settings.platega_return_url)
    fail = _strip_url_env(settings.platega_failed_url)
    if ret and not _valid_absolute_http_url(ret):
        log.warning("PLATEGA_RETURN_URL не валиден (%r), подставляем запасной.", ret[:80])
        ret = ""
    if fail and not _valid_absolute_http_url(fail):
        log.warning("PLATEGA_FAILED_URL не валиден (%r), подставляем запасной.", fail[:80])
        fail = ""

    un = await _bot_username(settings, cb)
    if un:
        default_ok = f"https://t.me/{un}?start=platega_ok"
        default_fail = f"https://t.me/{un}?start=platega_fail"
    else:
        default_ok = "https://telegram.org/"
        default_fail = "https://telegram.org/"

    if ret and fail:
        out_ok, out_fail = ret, fail
    elif ret:
        out_ok, out_fail = ret, ret
    elif fail:
        out_ok, out_fail = fail, fail
    else:
        out_ok, out_fail = default_ok, default_fail

    if not _valid_absolute_http_url(out_ok):
        out_ok = default_ok
    if not _valid_absolute_http_url(out_fail):
        out_fail = default_fail

    log.info("Platega redirect: return=%s failedUrl=%s", out_ok, out_fail)
    return out_ok, out_fail


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


def _platega_amount_to_charge_rub(settings: Settings, tariff_price_rub: float) -> float:
    """Сумма в Platega и в platega_orders (должны совпадать для подтверждения по вебхуку)."""
    tr = settings.platega_test_amount_rub
    if tr is not None and tr > 0:
        return float(tr)
    return float(sale_price_float(tariff_price_rub))


def _hint_test_amount_prefix(settings: Settings) -> str:
    tr = settings.platega_test_amount_rub
    if tr is None or tr <= 0:
        return ""
    return (
        f"<b>{theme.WARN} Тестовая сумма:</b> к оплате в Platega уйдёт <b>{tr:g} ₽</b> "
        f"(<code>PLATEGA_TEST_AMOUNT_RUB</code>). После проверки удалите строку из <code>.env</code>.\n\n"
    )


async def show_plus_tariff_payment_screen(
    cb: CallbackQuery,
    db: Database,
    settings: Settings,
    uid: int,
    t: PlusTariff,
) -> None:
    pay_url: str | None = None
    platega_note = False
    pg_ok = platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    )
    payment_hint = _PLATEGA_PAYMENT_HINT_OK if pg_ok else _PLATEGA_PAYMENT_HINT_NO_KEYS
    payment_hint = _hint_test_amount_prefix(settings) + payment_hint
    if pg_ok:
        ret_url, fail_url = await _platega_return_urls(settings, cb)
        payload = f"tg:{uid}:plus:{t.key}"
        charge_rub = _platega_amount_to_charge_rub(settings, t.price_rub)
        if settings.platega_test_amount_rub is not None and settings.platega_test_amount_rub > 0:
            log.info(
                "Platega PLUS: тестовая сумма %s ₽ вместо тарифа %s",
                charge_rub,
                sale_price_float(t.price_rub),
            )
        try:
            data = await asyncio.to_thread(
                create_platega_transaction,
                merchant_id=settings.platega_merchant_id,
                secret=settings.platega_secret,
                api_base=settings.platega_api_base,
                payment_method=settings.platega_payment_method,
                amount_rub=charge_rub,
                currency="RUB",
                description=f"PLUS {t.title_ru}",
                return_url=ret_url,
                failed_url=fail_url,
                payload=payload,
                universal_payment_form=settings.platega_v2_universal,
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
                        amount_rub=charge_rub,
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
    pg_ok = platega_configured(
        merchant_id=settings.platega_merchant_id,
        secret=settings.platega_secret,
    )
    payment_hint = _PLATEGA_PAYMENT_HINT_OK if pg_ok else _PLATEGA_PAYMENT_HINT_NO_KEYS
    payment_hint = _hint_test_amount_prefix(settings) + payment_hint
    if pg_ok:
        ret_url, fail_url = await _platega_return_urls(settings, cb)
        payload = f"tg:{uid}:luck:{t.key}"
        charge_rub = _platega_amount_to_charge_rub(settings, t.price_rub)
        if settings.platega_test_amount_rub is not None and settings.platega_test_amount_rub > 0:
            log.info(
                "Platega luck: тестовая сумма %s ₽ вместо тарифа %s",
                charge_rub,
                sale_price_float(t.price_rub),
            )
        try:
            data = await asyncio.to_thread(
                create_platega_transaction,
                merchant_id=settings.platega_merchant_id,
                secret=settings.platega_secret,
                api_base=settings.platega_api_base,
                payment_method=settings.platega_payment_method,
                amount_rub=charge_rub,
                currency="RUB",
                description=f"Удача {t.title_ru}",
                return_url=ret_url,
                failed_url=fail_url,
                payload=payload,
                universal_payment_form=settings.platega_v2_universal,
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
                        amount_rub=charge_rub,
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
            status_banner=luck_banner,
        ),
        parse_mode="HTML",
        reply_markup=kb_luck_payment_nav(pay_url=pay_url),
    )
