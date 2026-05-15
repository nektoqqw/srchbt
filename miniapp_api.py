"""HTTP API и статика Telegram Mini App (aiohttp)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import web
from aiogram import Bot

from channel_gate_aiogram import aiogram_get_channel_membership
from config import Settings
from db import Database
from miniapp_auth import user_id_from_init_data
from miniapp_services import (
    cabinet_payload,
    create_checkout,
    delete_saved,
    documents_payload,
    filters_payload,
    list_saved,
    redeem_promo,
    referral_payload,
    roll_job_status,
    run_valuation,
    save_username,
    set_filters,
    start_roll_job,
    sync_payments,
    tariffs_payload,
    toggle_luck_pause,
)

log = logging.getLogger(__name__)

_STATIC = Path(__file__).resolve().parent / "miniapp" / "static"
_bot_username_cache: str = ""


async def _bot_username(settings: Settings, bot: Bot | None) -> str:
    global _bot_username_cache
    u = (settings.bot_username_for_links or "").strip().lstrip("@")
    if u:
        return u
    if _bot_username_cache:
        return _bot_username_cache
    if bot:
        try:
            me = await bot.get_me()
            _bot_username_cache = (me.username or "").strip()
        except Exception:
            log.exception("get_me for miniapp")
    return _bot_username_cache


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _uid_from_request(request: web.Request) -> int | None:
    init_data = request.headers.get("X-Telegram-Init-Data") or request.headers.get(
        "Authorization", ""
    ).removeprefix("tma ").strip()
    if not init_data and request.query.get("initData"):
        init_data = request.query.get("initData", "")
    settings: Settings = request.app["settings"]
    return user_id_from_init_data(init_data, settings.bot_token)


async def _require_user(request: web.Request) -> tuple[int, web.Response | None]:
    uid = _uid_from_request(request)
    if uid is None:
        return 0, _json({"ok": False, "error": "unauthorized"}, 401)
    return uid, None


async def api_me(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    bot: Bot | None = request.app.get("bot")
    un = await _bot_username(settings, bot)
    ch = settings.required_channel_username
    subscribed = True
    gate_err = None
    if ch and bot:
        subscribed, gate_err = await aiogram_get_channel_membership(bot, ch, uid)
    body = cabinet_payload(db, uid, settings)
    body["ok"] = True
    body["filters"] = filters_payload(uid)
    body["channel"] = ch or None
    body["channel_subscribed"] = subscribed
    body["channel_gate_error"] = gate_err
    body["bot_username"] = un
    body["support"] = "@amnyam_supportt"
    return _json(body)


async def api_tariffs(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    data = tariffs_payload()
    data["ok"] = True
    data["is_plus"] = db.is_plus(uid)
    return _json(data)


async def api_filters_get(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    return _json({"ok": True, **filters_payload(uid)})


async def api_filters_set(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    if not isinstance(body, dict):
        return _json({"ok": False, "error": "invalid_body"}, 400)
    flt = set_filters(
        uid,
        prefix=str(body.get("prefix") or ""),
        suffix=str(body.get("suffix") or ""),
        digits=str(body.get("digits") or "any"),
    )
    return _json({"ok": True, **flt})


async def api_roll_start(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    length = int(body.get("length") or 0) if isinstance(body, dict) else 0
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    result = await start_roll_job(db, settings, uid, length=length)
    return _json(result, 200 if result.get("ok") else 400)


async def api_roll_status(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    job_id = request.match_info.get("job_id", "")
    st = roll_job_status(job_id, uid)
    if not st:
        return _json({"ok": False, "error": "not_found"}, 404)
    return _json({"ok": True, **st})


async def api_valuate(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    raw = str(body.get("text") or "") if isinstance(body, dict) else ""
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    from miniapp_services import get_checker

    checker = await get_checker(settings)
    result = await run_valuation(db, settings, uid, raw, checker)
    return _json(result)


async def api_saved_list(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    return _json({"ok": True, "items": list_saved(db, uid)})


async def api_saved_add(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    username = str(body.get("username") or "") if isinstance(body, dict) else ""
    db: Database = request.app["db"]
    return _json(save_username(db, uid, username))


async def api_saved_del(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    username = request.match_info.get("username", "")
    db: Database = request.app["db"]
    return _json(delete_saved(db, uid, username))


async def api_checkout(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    kind = str(body.get("kind") or "")
    key = str(body.get("tariff_key") or "")
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    bot: Bot | None = request.app.get("bot")
    un = await _bot_username(settings, bot)
    result = await create_checkout(db, settings, uid, kind=kind, tariff_key=key, bot_username=un)
    return _json(result, 200 if result.get("ok") else 400)


async def api_promo(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _json({"ok": False, "error": "invalid_json"}, 400)
    code = str(body.get("code") or "")
    kind = str(body.get("kind") or "plus")
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    return _json(redeem_promo(db, settings, uid, code, kind))


async def api_luck_toggle(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    return _json(toggle_luck_pause(db, uid))


async def api_referral(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    bot: Bot | None = request.app.get("bot")
    un = await _bot_username(settings, bot)
    return _json(referral_payload(db, settings, uid, un))


async def api_documents(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    return _json({"ok": True, **documents_payload(db)})


async def api_sync_payments(request: web.Request) -> web.Response:
    uid, err = await _require_user(request)
    if err:
        return err
    db: Database = request.app["db"]
    settings: Settings = request.app["settings"]
    return _json(await sync_payments(db, settings, uid))


async def static_index(_request: web.Request) -> web.Response:
    path = _STATIC / "index.html"
    if not path.is_file():
        return web.Response(text="Mini App static not found", status=404)
    return web.FileResponse(path)


def setup_miniapp_routes(app: web.Application) -> None:
    """Маршруты API и статики Mini App."""
    app.router.add_get("/app", static_index)
    app.router.add_get("/app/", static_index)
    app.router.add_static("/app/static", str(_STATIC), name="miniapp_static")

    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/tariffs", api_tariffs)
    app.router.add_get("/api/filters", api_filters_get)
    app.router.add_post("/api/filters", api_filters_set)
    app.router.add_post("/api/roll", api_roll_start)
    app.router.add_get("/api/roll/{job_id}", api_roll_status)
    app.router.add_post("/api/valuate", api_valuate)
    app.router.add_get("/api/saved", api_saved_list)
    app.router.add_post("/api/saved", api_saved_add)
    app.router.add_delete(r"/api/saved/{username}", api_saved_del)
    app.router.add_post("/api/checkout", api_checkout)
    app.router.add_post("/api/promo", api_promo)
    app.router.add_post("/api/luck/toggle", api_luck_toggle)
    app.router.add_get("/api/referral", api_referral)
    app.router.add_get("/api/documents", api_documents)
    app.router.add_post("/api/payments/sync", api_sync_payments)

    log.info("Mini App routes: /app/ static=%s", _STATIC)
