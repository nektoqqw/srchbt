"""
HTTP-колбэк Platega: подтверждение оплаты и начисление PLUS / «Удача».

Запуск (отдельно от бота, на сервере с HTTPS):
  python platega_webhook.py

В личном кабинете Platega укажите URL вида:
  https://<ваш-домен>/platega/callback

Переменные окружения: как у бота (BOT_TOKEN, BOT_DB_PATH), плюс PLATEGA_* из .env.
Дополнительно: PLATEGA_WEBHOOK_HOST (0.0.0.0), PLATEGA_WEBHOOK_PORT (8080),
PLATEGA_WEBHOOK_PATH (/platega/callback).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from aiohttp import ClientTimeout, web
from aiohttp.client import ClientSession

from config import load_settings
from db import Database
from platega_apply import apply_platega_purchase
from platega_sync import paid_amount_from_json, transaction_id_from_json, transaction_status_from_json

try:
    from miniapp_api import setup_miniapp_routes
except ImportError as _miniapp_import_err:
    setup_miniapp_routes = None  # type: ignore
    _MINIAPP_IMPORT_ERROR: str | None = str(_miniapp_import_err)
else:
    _MINIAPP_IMPORT_ERROR = None

log = logging.getLogger(__name__)


def _header(h: web.Request, name: str) -> str:
    v = h.headers.get(name)
    if v is not None and str(v).strip():
        return str(v).strip()
    alt = name.replace("-", "").lower()
    for k, val in h.headers.items():
        if k.replace("-", "").lower() == alt:
            return str(val).strip()
    return ""


def _tx_id(body: dict[str, Any]) -> str:
    return transaction_id_from_json(body)


def _status_upper(body: dict[str, Any]) -> str:
    return transaction_status_from_json(body)


def _amount(body: dict[str, Any]) -> float | None:
    return paid_amount_from_json(body)


async def _notify_user(
    session: ClientSession,
    *,
    bot_token: str,
    user_id: int,
    product_kind: str,
) -> None:
    if product_kind == "plus":
        text = "<b>PLUS</b> активирован по оплате. Спасибо!"
    elif product_kind == "luck":
        text = "Режим <b>«Удача»</b> продлён по оплате. Спасибо!"
    else:
        text = "Оплата получена."
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with session.post(
            url,
            json={
                "chat_id": user_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                t = await resp.text()
                log.warning("Telegram sendMessage HTTP %s: %s", resp.status, t[:300])
    except Exception:
        log.exception("Telegram sendMessage failed uid=%s", user_id)


async def platega_callback(request: web.Request) -> web.Response:
    settings = request.app["settings"]
    db: Database = request.app["db"]
    session: ClientSession = request.app["http_session"]

    mid = _header(request, "X-MerchantId")
    sec = _header(request, "X-Secret")
    if not mid or not sec:
        return web.Response(status=401, text="missing auth headers")
    if mid != settings.platega_merchant_id.strip() or sec != settings.platega_secret.strip():
        return web.Response(status=401, text="unauthorized")

    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="invalid json")
    if not isinstance(body, dict):
        return web.Response(status=400, text="expected object")

    tx = _tx_id(body)
    if not tx:
        log.warning("Platega webhook: no transaction id in body")
        return web.json_response({"ok": True})

    st = _status_upper(body)
    amt = _amount(body)

    if st == "CONFIRMED":
        grant = db.platega_try_confirm(tx, amt)
        if not grant:
            grant = db.platega_try_confirm(tx, None)
        if grant:
            uid, kind, tkey = grant
            apply_platega_purchase(db, uid, kind, tkey)
            await _notify_user(
                session,
                bot_token=settings.bot_token,
                user_id=uid,
                product_kind=kind,
            )
    elif st in ("CANCELED", "CANCELLED", "FAILED", "REJECTED"):
        db.platega_mark_canceled(tx)
    elif st == "CHARGEBACK":
        db.platega_mark_chargeback(tx)
        log.warning(
            "Platega CHARGEBACK transaction_id=%s — проверьте заказ и при необходимости отзовите доступ вручную.",
            tx,
        )

    return web.json_response({"ok": True})


async def _on_startup(app: web.Application) -> None:
    # ClientSession нельзя создавать в sync main() — в aiohttp 3.9+ нужен running loop.
    app["http_session"] = ClientSession()
    bot = app.get("bot")
    if bot:
        try:
            me = await bot.get_me()
            app["bot_username"] = (me.username or "").strip()
        except Exception:
            log.exception("get_me on startup")


async def _on_cleanup(app: web.Application) -> None:
    session: ClientSession = app["http_session"]
    await session.close()


async def _health(request: web.Request) -> web.Response:
    """Проверка без nginx: curl http://127.0.0.1:8080/health"""
    miniapp_ok = setup_miniapp_routes is not None
    err = request.app.get("miniapp_import_error")
    return web.json_response(
        {
            "ok": True,
            "platega_callback": True,
            "miniapp_routes": miniapp_ok,
            "miniapp_import_error": err,
        }
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings()
    except Exception as e:
        log.error("%s", e)
        sys.exit(1)

    db = Database(settings.db_path)
    path = (os.environ.get("PLATEGA_WEBHOOK_PATH") or "/platega/callback").strip()
    if not path.startswith("/"):
        path = "/" + path

    app = web.Application()
    app["settings"] = settings
    app["db"] = db
    try:
        from aiogram import Bot

        app["bot"] = Bot(settings.bot_token)
    except Exception:
        app["bot"] = None
        log.warning("Aiogram Bot недоступен — проверка канала в Mini App отключена")
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health)
    app["miniapp_import_error"] = _MINIAPP_IMPORT_ERROR
    app.router.add_post(path, platega_callback)
    if setup_miniapp_routes is not None:
        setup_miniapp_routes(app)
        log.info("Mini App: маршруты /app и /api подключены")
    else:
        log.error(
            "Mini App НЕ подключён (будет 404 на /app). Причина: %s",
            _MINIAPP_IMPORT_ERROR or "unknown",
        )

    host = (os.environ.get("PLATEGA_WEBHOOK_HOST") or "0.0.0.0").strip()
    try:
        port = int((os.environ.get("PLATEGA_WEBHOOK_PORT") or "8080").strip())
    except ValueError:
        port = 8080

    log.info("Platega webhook listening http://%s:%s%s", host, port, path)
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
