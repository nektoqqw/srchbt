"""
Бот к Telegram через Telethon (MTProto), без long polling Bot API (httpx).

Включается, если задан ``MTPROXY`` или ``USE_MTProto_BOT=1``.
При ``MTPROXY`` — туннель MTProto через MTProxy; иначе — обычное MTProto-подключение
(и при необходимости HTTP/SOCKS для Telethon через ``PROXY`` / ``TELETHON_USE_PROXY``).

Запросы к Fragment остаются HTTP(S) в ``fragment_scraper`` (``PROXY`` / напрямую), не через MTProto.
"""

from __future__ import annotations

import asyncio
import html
import logging
from asyncio import IncompleteReadError
from functools import partial
from typing import Any

from telethon import Button, TelegramClient, events
from telethon.sessions import MemorySession
from checker import (
    DisabledUsernameChecker,
    UsernameChecker,
    is_valid_telegram_username,
    is_valid_telegram_username_for_roll,
    iter_mtproxy_connection_types,
    normalize_username,
    random_letters_username,
    telethon_connection_class,
    telethon_graceful_disconnect,
)
from config import Settings
from db import Database
from fragment_scraper import fetch_fragment_gift_price

import bot_v2 as ui

log = logging.getLogger(__name__)

USER_DATA: dict[int, dict[str, Any]] = {}
PENDING_PROMO: dict[int, bool] = {}


def _ud(uid: int) -> dict[str, Any]:
    if uid not in USER_DATA:
        USER_DATA[uid] = {}
    return USER_DATA[uid]


def _main_menu_rows() -> list[list[Button]]:
    return [
        [Button.text(ui.BTN_ROLL), Button.text(ui.BTN_ANALYZE)],
        [Button.text(ui.BTN_TOP), Button.text(ui.BTN_CABINET)],
        [Button.text(ui.BTN_SUPPORT), Button.text(ui.BTN_PLUS)],
    ]


def _cabinet_rows() -> list[list[Button]]:
    return [
        [Button.inline("💾 Сохранённые имена", b"cab:saved")],
        [Button.inline("◀️ В меню", b"cab:back")],
    ]


def _length_rows() -> list[list[Button]]:
    return [
        [Button.inline("Длина 5", b"roll:len:5"), Button.inline("Длина 6", b"roll:len:6")],
        [Button.inline("Отмена", b"roll:cancel")],
    ]


def _rarity_rows() -> list[list[Button]]:
    return [
        [Button.inline("СВЕРХЛЕГЕНДАРНО!!!", b"roll:tier:super")],
        [
            Button.inline("Легендарный", b"roll:tier:legendary"),
            Button.inline("Мифический", b"roll:tier:mythic"),
        ],
        [
            Button.inline("Эпический", b"roll:tier:epic"),
            Button.inline("Редкий", b"roll:tier:rare"),
        ],
        [
            Button.inline("Обычный", b"roll:tier:common"),
            Button.inline("Любой", b"roll:tier:any"),
        ],
    ]


def _save_row(username: str) -> list[list[Button]]:
    uname = username.lower()
    return [[Button.inline(f"Сохранить @{uname}", f"save:{uname}".encode("utf-8"))]]


def _cb_data(event: events.CallbackQuery.Event) -> str:
    raw = event.data
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


async def _perform_roll(
    event: events.CallbackQuery.Event,
    *,
    uid: int,
    length: int,
    tier_key: str,
    ctx: dict[str, Any],
) -> None:
    db: Database = ctx["db"]
    settings: Settings = ctx["settings"]
    checker = ctx["checker"]

    ok = await ui._check_and_decrement_search(db, uid, settings)
    if not ok:
        await event.edit(
            "Бесплатные поиски закончились. Оформите <b>PLUS</b>.",
            parse_mode="html",
            buttons=None,
        )
        return

    desired_min = ui.TIER_MIN_USD.get(tier_key, 0.0)
    is_plus = db.is_plus(uid)

    await event.edit(
        f"Роллим username длины <b>{length}</b>…\nЦель: от <b>${desired_min:,.0f}</b>.",
        parse_mode="html",
    )

    items = db.iter_fragment_items(limit=5000)
    candidates: list[tuple[str, float]] = []
    for it in items:
        if it.price_usd is None:
            continue
        if float(it.price_usd) < desired_min:
            continue
        if len(it.username) != length:
            continue
        if not ui._letters_only_and_length_ok(it.username, length):
            continue
        candidates.append((it.username, float(it.price_usd)))

    candidates.sort(key=lambda x: x[1], reverse=True)
    found: list[tuple[str, float | None, str, str]] = []
    checked = 0
    max_candidates_to_check = 25
    want = 3 if is_plus else 1

    for uname, _price in candidates:
        if len(found) >= want:
            break
        if checked >= max_candidates_to_check:
            break
        checked += 1
        try:
            avail = await checker.is_available(uname)
            if avail is True:
                rarity_info, predicted_price, why = ui._rarity_for_display(uname, db)
                found.append((uname, predicted_price, rarity_info.name, why))
            elif avail is False:
                continue
            elif settings.username_check_mode == "disabled":
                rarity_info, predicted_price, why = ui._rarity_for_display(uname, db)
                found.append(
                    (
                        uname,
                        predicted_price,
                        rarity_info.name,
                        why + " | ⚠️ занятость в Telegram не проверялась (Telethon выключен).",
                    )
                )
        except Exception:
            log.exception("checker.is_available failed for %s", uname)
            continue

    if not found:
        attempts = 0
        if settings.username_check_mode == "disabled":
            while attempts < 30 and len(found) < want:
                attempts += 1
                cand = random_letters_username(length)
                if not is_valid_telegram_username_for_roll(cand, min_len=length, max_len=length):
                    continue
                try:
                    rarity_info, predicted_price, why = ui._rarity_for_display(cand, db)
                    found.append(
                        (
                            cand,
                            predicted_price,
                            rarity_info.name,
                            why + " | ⚠️ случайный ник, занятость не проверялась (Telethon выключен).",
                        )
                    )
                except Exception:
                    log.exception("fallback disabled roll failed")
                    continue
        else:
            while attempts < 80 and not found:
                attempts += 1
                cand = random_letters_username(length)
                if not is_valid_telegram_username_for_roll(cand, min_len=length, max_len=length):
                    continue
                try:
                    if await checker.is_available(cand) is True:
                        rarity_info, predicted_price, why = ui._rarity_for_display(cand, db)
                        found.append((cand, predicted_price, rarity_info.name, why))
                except Exception:
                    log.exception("fallback checker.is_available failed")
                    continue

    if not found:
        await event.edit(
            "Не получилось найти свободные username за разумное время. Попробуйте ещё раз.",
            buttons=None,
        )
        return

    for uname, predicted_price, rarity_name, _why in found:
        db.add_roll_event(
            user_id=uid,
            username=uname,
            rarity=rarity_name,
            predicted_price_usd=predicted_price,
        )

    if settings.username_check_mode == "disabled":
        lines = [f"Варианты: <b>{len(found)}</b> (оценка без проверки занятости в Telegram)", ""]
    else:
        lines = [f"Найдено свободных: <b>{len(found)}</b>", ""]
    buttons: list[list[Button]] = []
    for uname, predicted_price, rarity_name, why in found:
        usd_txt = "?" if predicted_price is None else f"${predicted_price:,.0f}"
        lines.append(f"• @{uname.lower()}")
        lines.append(f"  Оценка: <b>{usd_txt}</b> | Редкость: <b>{html.escape(rarity_name)}</b>")
        lines.append(f"  Причина: {html.escape(why)}")
        lines.append("")
        if is_plus:
            ul = uname.lower()
            buttons.append([Button.inline(f"Сохранить @{ul}", f"save:{ul}".encode("utf-8"))])

    if not is_plus:
        lines.append("<i>Сохранение доступно в PLUS.</i>")

    await event.edit(
        "\n".join(lines).strip(),
        parse_mode="html",
        buttons=buttons if buttons else None,
    )


async def _perform_analysis(
    event: events.NewMessage.Event,
    *,
    uid: int,
    raw_username: str,
    ctx: dict[str, Any],
) -> None:
    db: Database = ctx["db"]
    settings: Settings = ctx["settings"]
    checker = ctx["checker"]

    uname = normalize_username(raw_username)
    if not is_valid_telegram_username(uname):
        await event.respond(
            "Некорректный username. Должно быть: 5–32 символа, только a-z, 0-9, '_' (без @ можно).",
            buttons=_main_menu_rows(),
        )
        return

    ok = await ui._check_and_decrement_search(db, uid, settings)
    if not ok:
        await event.respond(
            "Бесплатные поиски закончились. Оформите <b>PLUS</b>.",
            parse_mode="html",
            buttons=_main_menu_rows(),
        )
        return

    if settings.username_check_mode == "disabled":
        await event.respond("Считаю оценку (режим без Telethon — занятость в Telegram не проверяю)…")
        available: bool | None = None
    else:
        await event.respond("Проверяю валидность (свободен/занят) и оценку…")
        try:
            available = await checker.is_available(uname)
        except Exception as e:
            log.exception("analysis checker error")
            await event.respond(
                f"Ошибка проверки username: {e}\nПопробуйте позже.",
                buttons=_main_menu_rows(),
            )
            return

    rarity_info, predicted_price, why = ui._rarity_for_display(uname, db)
    usd_txt = "?" if predicted_price is None else f"${predicted_price:,.0f}"

    if available is None:
        status = (
            "⚠️ <b>Автопроверка «свободен для установки» недоступна</b> (режим без Telethon). "
            "В Telegram: Настройки → Профиль → Имя пользователя — попробуйте назначить этот логин вручную."
        )
    elif available:
        status = "✅ Свободен для установки (по checkUsername)"
    else:
        status = "❌ Занят или недоступен (не проходит checkUsername)"

    lines = [
        f"@{uname.lower()}",
        "",
        f"{status}",
        f"Оценка ценности: <b>{usd_txt}</b>",
        f"Редкость: <b>{html.escape(rarity_info.name)}</b>",
        f"Причина: {html.escape(why)}",
    ]

    is_plus = db.is_plus(uid)
    if is_plus:
        await event.respond(
            "\n".join(lines),
            parse_mode="html",
            buttons=_save_row(uname),
        )
    else:
        msg = "\n".join(lines) + "\n\n<i>Сохранение доступно в PLUS.</i>"
        await event.respond(msg, parse_mode="html", buttons=_main_menu_rows())


def register_handlers(client: TelegramClient, ctx: dict[str, Any]) -> None:

    @client.on(events.NewMessage(pattern=r"^/start", incoming=True))
    async def cmd_start(event: events.NewMessage.Event) -> None:
        uid = event.sender_id
        db: Database = ctx["db"]
        settings: Settings = ctx["settings"]
        db.get_or_create_user(uid)
        if settings.username_check_mode == "disabled":
            intro = (
                "Привет! Оцениваю username по данным <b>Fragment</b>.\n\n"
                "<b>Режим без Telethon:</b> автоматически проверить «свободен ли ник для установки на аккаунт» "
                "через Bot API <b>нельзя</b> — это умеет только MTProto (Telethon). "
                "Финальную проверку делай в Telegram: Настройки → Профиль → Имя пользователя."
            )
        else:
            if settings.mtproxy:
                net = (
                    "<b>Сеть:</b> бот и проверка ников к Telegram идут через Telethon (MTProto) "
                    f"и <b>MTProxy</b> {settings.mtproxy.host}:{settings.mtproxy.port}. "
                    "Сайт Fragment — только HTTP(S), отдельно от MTProto (см. <code>PROXY</code> в .env)."
                )
            else:
                net = (
                    "<b>Сеть:</b> бот к Telegram подключён через Telethon (MTProto), без Bot API long polling. "
                    "Сайт Fragment — только HTTP(S), отдельно от MTProto (см. <code>PROXY</code> в .env)."
                )
            intro = (
                "Привет! Я помогаю находить <b>свободные для установки</b> Telegram username "
                "и оцениваю их ценность по рынку Fragment.\n\n"
                + net
            )
        await event.respond(intro, parse_mode="html", buttons=_main_menu_rows())
        await event.respond(
            f"Пробная версия: <b>{settings.free_search_limit}</b> поисков. <b>PLUS</b> — безлимит и сохранение.",
            parse_mode="html",
            buttons=_main_menu_rows(),
        )

    @client.on(events.NewMessage(pattern=r"^/support", incoming=True))
    async def cmd_support(event: events.NewMessage.Event) -> None:
        await event.respond(
            "<b>Поддержка</b>\n\n"
            "Если нужен апдейт/расширение — пишите в чат владельцу бота.",
            parse_mode="html",
            buttons=_main_menu_rows(),
        )

    @client.on(events.NewMessage(pattern=r"^/cancel", incoming=True))
    async def cmd_cancel(event: events.NewMessage.Event) -> None:
        uid = event.sender_id
        PENDING_PROMO.pop(uid, None)
        ud = _ud(uid)
        ud["await_username"] = False
        ud.pop("roll_len", None)
        ud.pop("roll_tier", None)
        await event.respond("Отменено.", buttons=_main_menu_rows())

    @client.on(events.NewMessage(pattern=r"^/grant_plus", incoming=True))
    async def cmd_grant_plus(event: events.NewMessage.Event) -> None:
        settings: Settings = ctx["settings"]
        db: Database = ctx["db"]
        uid = event.sender_id
        if uid not in settings.admin_ids:
            await event.respond("Команда только для администратора.")
            return
        parts = (event.raw_text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await event.respond("Использование: /grant_plus <user_id>")
            return
        target = int(parts[1])
        db.set_plus(target, True)
        await event.respond(f"PLUS выдан пользователю {target}")

    @client.on(events.NewMessage(pattern=r"^/import_fragment", incoming=True))
    async def cmd_import_fragment(event: events.NewMessage.Event) -> None:
        settings: Settings = ctx["settings"]
        db: Database = ctx["db"]
        uid = event.sender_id
        if uid not in settings.admin_ids:
            await event.respond("Команда только для администратора.")
            return
        parts = (event.raw_text or "").split(maxsplit=1)
        if len(parts) < 2:
            await event.respond("Использование: /import_fragment <fragment_url>")
            return
        url = parts[1].strip()
        await event.respond("Импортирую Fragment…")
        try:
            gift = await asyncio.to_thread(
                partial(
                    fetch_fragment_gift_price,
                    url,
                    ton_to_usd=settings.ton_to_usd,
                    proxies=settings.proxy.requests_proxies if settings.proxy else None,
                )
            )
        except Exception as e:
            log.exception("import_fragment failed")
            await event.respond(f"Не удалось импортировать: {e}")
            return

        db.upsert_fragment_item(
            username=gift.username,
            price_usd=gift.price_usd,
            source_url=gift.source_url,
        )

        if gift.price_usd is None:
            await event.respond(f"Импортирован username: @{gift.username} (цена не распознана).")
        else:
            await event.respond(f"Импорт: @{gift.username} ~ ${gift.price_usd:,.0f} (сохранено).")

    @client.on(events.CallbackQuery)
    async def on_cb(event: events.CallbackQuery.Event) -> None:
        uid = event.sender_id
        db: Database = ctx["db"]
        settings: Settings = ctx["settings"]
        data = _cb_data(event)

        if data == "cab:saved":
            await event.answer()
            if not db.is_plus(uid):
                await event.edit("Сохранённые имена доступны только с PLUS.")
                return
            names = db.list_saved(uid)
            if not names:
                await event.edit("Пока пусто. Крутите ролл или проверьте ник и нажмите «Сохранить».")
                return
            lines = ["<b>Сохранённые имена</b>", ""]
            for n in names:
                lines.append(f"• @{n}")
            await event.edit("\n".join(lines), parse_mode="html")
            return

        if data == "cab:back":
            await event.answer()
            await event.edit("Главное меню:")
            return

        if data == "plus:enter":
            await event.answer()
            PENDING_PROMO[uid] = True
            await event.edit(
                "Введите промокод одним сообщением (или нажмите отмену через /cancel)."
            )
            return

        if data.startswith("save:"):
            if not db.is_plus(uid):
                await event.answer("Нужен PLUS", alert=True)
                return
            uname = data.split(":", 1)[1].lower()
            ok = db.save_username(uid, uname)
            await event.answer("Сохранено ✅" if ok else "Уже было", alert=True)
            return

        if data == "roll:cancel":
            await event.answer()
            ud = _ud(uid)
            ud.pop("roll_len", None)
            ud.pop("roll_tier", None)
            await event.edit("Ок, отменено.")
            return

        if data.startswith("roll:len:"):
            await event.answer()
            ln = int(data.split(":", 2)[2])
            _ud(uid)["roll_len"] = ln
            await event.edit("Выберите редкость (tier):", buttons=_rarity_rows())
            return

        if data.startswith("roll:tier:"):
            await event.answer()
            tier_key = data.split(":", 2)[2]
            ud = _ud(uid)
            ud["roll_tier"] = tier_key
            if "roll_len" not in ud:
                await event.edit("Сначала выберите длину. Нажмите ролл заново.")
                return
            length = int(ud["roll_len"])
            ud.pop("roll_len", None)
            ud.pop("roll_tier", None)
            await _perform_roll(event, uid=uid, length=length, tier_key=tier_key, ctx=ctx)
            return

        await event.answer()
        await event.edit("Неизвестная команда.")

    @client.on(events.NewMessage(incoming=True))
    async def on_text(event: events.NewMessage.Event) -> None:
        if not event.is_private:
            return
        if event.raw_text and event.raw_text.startswith("/"):
            return
        uid = event.sender_id
        db: Database = ctx["db"]
        settings: Settings = ctx["settings"]
        text = (event.raw_text or "").strip()
        ud = _ud(uid)

        if ud.get("await_username"):
            ud["await_username"] = False
            await _perform_analysis(event, uid=uid, raw_username=text, ctx=ctx)
            return

        if PENDING_PROMO.pop(uid, None):
            code = text.upper()
            if code == settings.plus_promo_code:
                db.set_plus(uid, True)
                await event.respond(
                    "✅ PLUS активирован!\nТеперь можно сохранять найденные username и роллить без лимитов.",
                    parse_mode="html",
                    buttons=_main_menu_rows(),
                )
            else:
                await event.respond(
                    "Промокод не подходит. Попробуйте снова.",
                    parse_mode="html",
                    buttons=_main_menu_rows(),
                )
            return

        if text == ui.BTN_ROLL:
            ud.pop("roll_len", None)
            ud.pop("roll_tier", None)
            await event.respond(
                "Выберите длину username (5–6 латинских букв):",
                buttons=_length_rows(),
            )
            return
        if text == ui.BTN_ANALYZE:
            ud["await_username"] = True
            await event.respond("Введи username (например cache). Без @ тоже можно.")
            return
        if text == ui.BTN_TOP:
            top = db.top_roll_month(days=30, limit=10)
            if not top:
                await event.respond(
                    "Пока нет данных за месяц. Нажмите «Ролл» и крутите — топ сформируется."
                )
                return
            lines = ["<b>Топ username за 30 дней</b>", ""]
            for i, (uname, rarity, pred) in enumerate(top, start=1):
                usd_txt = "?" if pred is None else f"${pred:,.0f}"
                lines.append(f"{i}. @{uname} — <b>{rarity}</b> ({usd_txt})")
            await event.respond("\n".join(lines), parse_mode="html")
            return
        if text == ui.BTN_CABINET:
            await event.respond(
                await ui.format_cabinet(db, uid, settings),
                parse_mode="html",
                buttons=_cabinet_rows(),
            )
            return
        if text == ui.BTN_SUPPORT:
            await event.respond(
                "<b>Поддержка</b>\n\n"
                "Если нужно улучшить бота (скорость, больше паттернов, больше источников Fragment) — пишите в чат.",
                parse_mode="html",
                buttons=_main_menu_rows(),
            )
            return
        if text == ui.BTN_PLUS:
            await event.respond(
                "<b>Подписка PLUS</b>\n\n"
                "• Безлимитные поиски\n"
                "• Сохранение найденных username\n"
                "• Топы и история в кабинете\n\n"
                "Пробная версия: отправьте промокод одним сообщением.",
                parse_mode="html",
                buttons=[[Button.inline("Ввести промокод", b"plus:enter")]],
            )
            return

        await event.respond(
            "Используйте кнопки меню ниже.",
            parse_mode="html",
            buttons=_main_menu_rows(),
        )


async def _async_main(settings: Settings, db: Database, checker: UsernameChecker | DisabledUsernameChecker) -> None:
    session_bot = f"{settings.telethon_session}_bot_api"
    if settings.mtproxy:
        mp = settings.mtproxy
        last_exc: Exception | None = None
        winning_cls: type | None = None
        for conn_cls in iter_mtproxy_connection_types(settings.mtproxy_telethon_connection):
            probe = TelegramClient(
                MemorySession(),
                settings.api_id,
                settings.api_hash,
                connection=conn_cls,
                proxy=(mp.host, mp.port, mp.secret),
                timeout=settings.telethon_timeout,
                connection_retries=settings.telethon_connection_retries,
            )
            try:
                await probe.start(bot_token=settings.bot_token)
                winning_cls = conn_cls
                log.info("Бот: MTProxy тест TCP %s прошёл (MemorySession)", conn_cls.__name__)
                break
            except (
                OSError,
                ConnectionError,
                IncompleteReadError,
                ConnectionResetError,
                EOFError,
                ValueError,
            ) as e:
                last_exc = e
                log.warning(
                    "MTProxy (бот) TCP %s: тест не прошёл (%s), следующий режим",
                    conn_cls.__name__,
                    e,
                )
            finally:
                await telethon_graceful_disconnect(probe)
        if winning_cls is None:
            raise RuntimeError(
                "MTProxy: ни один режим TCP не подошёл. Проверьте tg://proxy и секрет в официальном Telegram."
            ) from last_exc

        client = TelegramClient(
            session_bot,
            settings.api_id,
            settings.api_hash,
            connection=winning_cls,
            proxy=(mp.host, mp.port, mp.secret),
            timeout=settings.telethon_timeout,
            connection_retries=settings.telethon_connection_retries,
        )
        await client.start(bot_token=settings.bot_token)
    else:
        th_proxy = None
        if settings.proxy and settings.telethon_use_proxy:
            th_proxy = settings.proxy.telethon_proxy
        client = TelegramClient(
            session_bot,
            settings.api_id,
            settings.api_hash,
            proxy=th_proxy,
            timeout=settings.telethon_timeout,
            connection_retries=settings.telethon_connection_retries,
            connection=telethon_connection_class(settings.telethon_connection),
        )
    assert client is not None
    ctx: dict[str, Any] = {"db": db, "settings": settings, "checker": checker}
    register_handlers(client, ctx)

    checker_started = False
    try:
        if settings.mtproxy is None:
            await client.start(bot_token=settings.bot_token)
        me = await client.get_me()
        uname = getattr(me, "username", None) or me.id
        if settings.mtproxy:
            log.info(
                "Bot v0.2 (Telethon MTProto + MTProxy %s:%s) @%s",
                settings.mtproxy.host,
                settings.mtproxy.port,
                uname,
            )
        else:
            log.info("Bot v0.2 (Telethon MTProto, без MTProxy) @%s", uname)

        if getattr(checker, "uses_telethon", False):
            await checker.start()
            checker_started = True

        await client.run_until_disconnected()
    finally:
        if checker_started and getattr(checker, "uses_telethon", False):
            await checker.stop()
        if client is not None and client.is_connected():
            await client.disconnect()


def run_telethon_mtproto_bot_stack(
    settings: Settings,
    db: Database,
    checker: UsernameChecker | DisabledUsernameChecker,
) -> None:
    asyncio.run(_async_main(settings, db, checker))


def run_mtproxy_bot_stack(
    settings: Settings,
    db: Database,
    checker: UsernameChecker | DisabledUsernameChecker,
) -> None:
    """Совместимость со старым именем; см. ``run_telethon_mtproto_bot_stack``."""
    run_telethon_mtproto_bot_stack(settings, db, checker)
