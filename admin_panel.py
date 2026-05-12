"""Админ-панель: только пользователи из ADMIN_IDS / ADMIN_TELEGRAM_ID (.env)."""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Settings
from db import Database
from plus_tariffs import PLUS_TARIFFS, plus_tariff_by_key

log = logging.getLogger(__name__)

ADMIN_SESS_PROMO = "admin_promo_expect"
ADMIN_SESS_BROADCAST = "admin_broadcast"
ADMIN_SESS_DOC = "admin_doc_expect"

USER_PAGE = 35


def _promo_plus_period_ru(plus_days: int | None) -> str:
    if plus_days is None:
        return "без даты окончания"
    for t in PLUS_TARIFFS:
        if t.days == plus_days:
            return t.title_ru
    return f"{plus_days} дн."


def _promo_list_view(
    rows: list[tuple[str, str, int, int, str, int | None]],
) -> tuple[str, InlineKeyboardMarkup]:
    header = "<b>📋 Промокоды</b>\n<code>════════</code>\n\n"
    if not rows:
        body = "<i>Пока нет записей — создайте через кнопки выше.</i>"
        return header + body, InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Пульт", callback_data="adm:home")]
            ]
        )
    lines: list[str] = []
    btn_rows: list[list[InlineKeyboardButton]] = []
    for code, kind, max_u, active, created, plus_days in rows:
        st = "✅" if active else "⛔️"
        kind_ru = "PLUS" if kind == "plus" else "Удача"
        lim = "∞" if max_u <= 0 else str(max_u)
        extra = ""
        if kind == "plus":
            extra = f" · <i>{html.escape(_promo_plus_period_ru(plus_days))}</i>"
        lines.append(
            f"{st} <code>{html.escape(code)}</code> · {kind_ru} · лимит {lim} · "
            f"<i>{html.escape(created)}</i>{extra}"
        )
        cb_data = f"adm:prm:{code}"
        if len(cb_data.encode("utf-8")) <= 64:
            btn_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 {code}",
                        callback_data=cb_data,
                    )
                ]
            )
    btn_rows.append([InlineKeyboardButton(text="« Пульт", callback_data="adm:home")])
    return header + "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=btn_rows)


def admin_clear_session(sess_uid: dict[str, Any]) -> None:
    sess_uid.pop(ADMIN_SESS_PROMO, None)
    sess_uid.pop(ADMIN_SESS_BROADCAST, None)
    sess_uid.pop(ADMIN_SESS_DOC, None)


def is_admin(uid: int, settings: Settings) -> bool:
    return uid in settings.admin_ids


def search_roll_allowed(uid: int, db: Database, settings: Settings) -> bool:
    if db.is_search_globally_blocked() and uid not in settings.admin_ids:
        return False
    return db.can_search(uid, settings.free_search_limit)


def admin_menu_html(db: Database, settings: Settings) -> str:
    blocked = db.is_search_globally_blocked()
    users_n = db.count_users(plus_only=False)
    lock_line = (
        "<b>Поиск никнеймов:</b> <i>закрыт для гостей и подписчиков</i> 🔒"
        if blocked
        else "<b>Поиск никнеймов:</b> <i>открыт</i> 🔓"
    )
    plus_n = db.count_users(plus_only=True)
    return (
        "<blockquote><b>◆ ПУЛЬТ УПРАВЛЕНИЯ ◆</b></blockquote>\n"
        "<code>═══════════ ✦ ═══════════</code>\n\n"
        f"{lock_line}\n"
        f"<b>Пользователей в базе:</b> <code>{users_n}</code> · "
        f"<b>с PLUS:</b> <code>{plus_n}</code>\n\n"
        "<i>Создавайте промокоды, переключайте глобальный стоп-поиск, "
        "рассылайте объявления, правьте ссылки на документы. Доступ только у вас.</i>\n\n"
        "<code>──────── ● ────────</code>"
    )


def _doc_url_preview(url: str, *, max_len: int = 52) -> str:
    u = (url or "").strip()
    if not u:
        return "<i>не задано</i>"
    esc = html.escape(u)
    if len(u) > max_len:
        return f"<code>{esc[: max_len - 1]}…</code>"
    return f"<code>{esc}</code>"


def admin_docs_menu_html(db: Database) -> str:
    t = db.get_legal_document_url("terms")
    p = db.get_legal_document_url("privacy")
    return (
        "<blockquote><b>📄 Документы</b></blockquote>\n"
        "<code>════════════</code>\n\n"
        "<b>• Пользовательское соглашение</b>\n"
        f"{_doc_url_preview(t)}\n\n"
        "<b>• Политика конфиденциальности</b>\n"
        f"{_doc_url_preview(p)}\n\n"
        "<i>Выберите пункт ниже, чтобы задать или сбросить ссылку.</i>"
    )


def kb_admin_docs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Пользовательское соглашение",
                    callback_data="adm:doc:terms",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔐 Политика конфиденциальности",
                    callback_data="adm:doc:privacy",
                ),
            ],
            [
                InlineKeyboardButton(text="« Пульт", callback_data="adm:home"),
            ],
        ]
    )


def kb_admin_root(db: Database) -> InlineKeyboardMarkup:
    blocked = db.is_search_globally_blocked()
    lock_btn = (
        "🔓 Открыть поиск для всех" if blocked else "🔒 Закрыть поиск для всех"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Промокод PLUS",
                    callback_data="adm:ppm",
                ),
                InlineKeyboardButton(
                    text="🍀 Промокод Удача",
                    callback_data="adm:pl",
                ),
            ],
            [
                InlineKeyboardButton(text=lock_btn, callback_data="adm:lock"),
            ],
            [
                InlineKeyboardButton(
                    text="📣 Рассылка",
                    callback_data="adm:bcm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="adm:usm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Список промокодов",
                    callback_data="adm:list",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📄 Документы",
                    callback_data="adm:docs",
                ),
            ],
        ]
    )


async def cmd_admin(
    message: Message,
    *,
    db: Database,
    settings: Settings,
) -> None:
    assert message.from_user
    uid = message.from_user.id
    if not settings.admin_ids:
        await message.answer(
            "<b>Админ-панель не настроена.</b>\n\n"
            "В <code>.env</code> укажите <code>ADMIN_IDS=ваш_telegram_id</code> "
            "или один <code>ADMIN_TELEGRAM_ID=…</code>, перезапустите бота.",
            parse_mode="HTML",
        )
        return
    if uid not in settings.admin_ids:
        await message.answer(
            "<b>Доступ запрещён.</b> Этот раздел только для владельца бота.",
            parse_mode="HTML",
        )
        return
    await message.answer(
        admin_menu_html(db, settings),
        parse_mode="HTML",
        reply_markup=kb_admin_root(db),
    )


async def admin_refresh_message(
    cb: CallbackQuery,
    *,
    db: Database,
    settings: Settings,
) -> None:
    assert cb.message
    await cb.message.edit_text(
        admin_menu_html(db, settings),
        parse_mode="HTML",
        reply_markup=kb_admin_root(db),
    )


async def admin_handle_callback(
    cb: CallbackQuery,
    *,
    sess_uid: dict[str, Any],
    db: Database,
    settings: Settings,
) -> None:
    assert cb.data and cb.from_user and cb.message
    uid = cb.from_user.id
    if uid not in settings.admin_ids:
        await cb.answer("Нет доступа", show_alert=True)
        return
    data = cb.data
    if data == "adm:home":
        admin_clear_session(sess_uid)
        await admin_refresh_message(cb, db=db, settings=settings)
        return
    if data == "adm:lock":
        db.set_search_globally_blocked(not db.is_search_globally_blocked())
        await admin_refresh_message(cb, db=db, settings=settings)
        return
    if data == "adm:ppm":
        admin_clear_session(sess_uid)
        rows: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for i, t in enumerate(PLUS_TARIFFS):
            row.append(
                InlineKeyboardButton(
                    text=t.title_ru,
                    callback_data=f"adm:ppt:{t.key}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="« Пульт", callback_data="adm:home")])
        await cb.message.edit_text(
            "<b>✨ Новый промокод PLUS</b>\n<code>────────</code>\n\n"
            "Выберите <b>срок подписки</b> — как в витрине тарифов (1 день, 3 дня, неделя, …).\n\n"
            "Дальше одним сообщением пришлёте <code>КОД ЛИМИТ</code>:\n"
            "• <b>КОД</b> — латиница и цифры, 3–40 символов\n"
            "• <b>ЛИМИТ</b> — сколько раз код сработает на разных людей "
            "(<code>0</code> = без лимита)\n\n"
            "<code>/cancel</code> — отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    if data.startswith("adm:ppt:"):
        tkey = data.removeprefix("adm:ppt:")
        tar = plus_tariff_by_key(tkey)
        if not tar:
            await cb.answer("Неизвестный тариф", show_alert=True)
            return
        sess_uid.pop(ADMIN_SESS_BROADCAST, None)
        sess_uid.pop(ADMIN_SESS_DOC, None)
        sess_uid[ADMIN_SESS_PROMO] = f"plus:{tar.key}"
        period = (
            "без даты окончания (как «Навсегда»)"
            if tar.days is None
            else f"<b>{html.escape(tar.title_ru)}</b> ({tar.days} дн.)"
        )
        await cb.message.edit_text(
            "<b>✨ Промокод PLUS</b>\n<code>────────</code>\n\n"
            f"<b>Срок по коду:</b> {period}\n\n"
            "Одним сообщением пришлите строку вида:\n"
            "<code>КОД ЛИМИТ</code>\n\n"
            "• <b>ЛИМИТ</b> — сколько раз код сработает <b>на всех пользователей</b> "
            "(<code>0</code> = без лимита)\n\n"
            "<i>Пример:</i> <code>SUMMER 50</code>\n\n"
            "<code>/cancel</code> — отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« К срокам PLUS", callback_data="adm:ppm"
                        )
                    ],
                    [InlineKeyboardButton(text="« Пульт", callback_data="adm:home")],
                ]
            ),
        )
        return
    if data == "adm:pl":
        sess_uid[ADMIN_SESS_PROMO] = "luck"
        await cb.message.edit_text(
            "<b>🍀 Новый промокод Удача</b>\n<code>────────</code>\n\n"
            "Формат тот же:\n<code>КОД ЛИМИТ</code>\n\n"
            "<i>Пример:</i> <code>LUCKY7 100</code>\n\n"
            "<code>/cancel</code> — отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Пульт", callback_data="adm:home"
                        )
                    ]
                ]
            ),
        )
        return
    if data == "adm:bcm":
        admin_clear_session(sess_uid)
        await cb.message.edit_text(
            "<b>📣 Рассылка</b>\n<code>────────</code>\n\n"
            "Выберите аудиторию, затем пришлите <b>одно сообщение</b> с текстом рассылки "
            "(разметка <b>HTML</b>, до ~4000 символов).\n\n"
            "<code>/cancel</code> — отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🌍 Всем в базе",
                            callback_data="adm:bc:set:all",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="♠️ Только с PLUS",
                            callback_data="adm:bc:set:plus",
                        ),
                    ],
                    [
                        InlineKeyboardButton(text="« Пульт", callback_data="adm:home"),
                    ],
                ]
            ),
        )
        return
    if data in ("adm:bc:set:all", "adm:bc:set:plus"):
        mode = "all" if data.endswith(":all") else "plus"
        sess_uid.pop(ADMIN_SESS_PROMO, None)
        sess_uid.pop(ADMIN_SESS_DOC, None)
        sess_uid[ADMIN_SESS_BROADCAST] = mode
        n = (
            db.count_users(plus_only=False)
            if mode == "all"
            else db.count_users(plus_only=True)
        )
        who = "всем пользователям в базе" if mode == "all" else "только пользователям с PLUS"
        await cb.message.edit_text(
            "<b>📣 Рассылка</b>\n<code>────────</code>\n\n"
            f"<b>Аудитория:</b> {html.escape(who)}\n"
            f"<b>Ожидаемых получателей:</b> <code>{n}</code>\n\n"
            "Следующим сообщением пришлите текст рассылки "
            "(<b>HTML</b> как в ответах бота).\n\n"
            "<code>/cancel</code> — отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Выбор аудитории", callback_data="adm:bcm"
                        )
                    ],
                    [InlineKeyboardButton(text="« Пульт", callback_data="adm:home")],
                ]
            ),
        )
        return
    if data == "adm:usm":
        admin_clear_session(sess_uid)
        tot = db.count_users(plus_only=False)
        pl = db.count_users(plus_only=True)
        await cb.message.edit_text(
            "<blockquote><b>👥 Пользователи</b></blockquote>\n"
            "<code>════════════</code>\n\n"
            f"<b>Всего в базе:</b> <code>{tot}</code>\n"
            f"<b>С подпиской PLUS:</b> <code>{pl}</code>\n\n"
            "<i>Откройте список id постранично.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 Все id",
                            callback_data="adm:usr:all:0",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="♠️ Только PLUS",
                            callback_data="adm:usr:p:0",
                        ),
                    ],
                    [InlineKeyboardButton(text="« Пульт", callback_data="adm:home")],
                ]
            ),
        )
        return
    if data.startswith("adm:usr:"):
        parts = data.split(":")
        if len(parts) >= 4 and parts[2] in ("all", "p"):
            plus_only = parts[2] == "p"
            try:
                offset = max(0, int(parts[3]))
            except ValueError:
                offset = 0
            ids, total = db.list_user_ids_page(
                plus_only=plus_only, offset=offset, limit=USER_PAGE
            )
            title = "Все пользователи" if not plus_only else "Только PLUS"
            if not ids:
                body = "<i>На этой странице пусто.</i>"
            else:
                body = "\n".join(f"<code>{uid_u}</code>" for uid_u in ids)
            nav: list[InlineKeyboardButton] = []
            mode = "p" if plus_only else "all"
            if offset > 0:
                nav.append(
                    InlineKeyboardButton(
                        text="◀ Назад",
                        callback_data=f"adm:usr:{mode}:{max(0, offset - USER_PAGE)}",
                    )
                )
            if offset + len(ids) < total:
                nav.append(
                    InlineKeyboardButton(
                        text="Вперёд ▶",
                        callback_data=f"adm:usr:{mode}:{offset + len(ids)}",
                    )
                )
            rows_k: list[list[InlineKeyboardButton]] = []
            if nav:
                rows_k.append(nav)
            rows_k.append(
                [InlineKeyboardButton(text="« К аудиториям", callback_data="adm:usm")]
            )
            rows_k.append([InlineKeyboardButton(text="« Пульт", callback_data="adm:home")])
            await cb.message.edit_text(
                f"<b>{html.escape(title)}</b> · "
                f"<i>стр.</i> <code>{offset + 1}–{offset + len(ids)}</code> из <code>{total}</code>\n"
                "<code>────────</code>\n\n" + body,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_k),
            )
        return
    if data.startswith("adm:prm:"):
        code = data.removeprefix("adm:prm:")
        if code and db.dynamic_promo_delete(code):
            rows = db.dynamic_promo_list(limit=30)
            text, kb = _promo_list_view(rows)
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await cb.answer("Код не найден или не удалось удалить.", show_alert=True)
        return
    if data == "adm:docs":
        admin_clear_session(sess_uid)
        await cb.message.edit_text(
            admin_docs_menu_html(db),
            parse_mode="HTML",
            reply_markup=kb_admin_docs(),
        )
        return
    if data.startswith("adm:doc:clr:"):
        kind = data.rsplit(":", 1)[-1]
        if kind in ("terms", "privacy"):
            db.set_legal_document_url(kind, "")
            sess_uid.pop(ADMIN_SESS_DOC, None)
            await cb.message.edit_text(
                admin_docs_menu_html(db),
                parse_mode="HTML",
                reply_markup=kb_admin_docs(),
            )
        return
    if data in ("adm:doc:terms", "adm:doc:privacy"):
        kind = "terms" if data == "adm:doc:terms" else "privacy"
        sess_uid.pop(ADMIN_SESS_PROMO, None)
        sess_uid.pop(ADMIN_SESS_BROADCAST, None)
        sess_uid[ADMIN_SESS_DOC] = kind
        cur_u = db.get_legal_document_url(kind)
        title = (
            "Пользовательское соглашение"
            if kind == "terms"
            else "Политика конфиденциальности"
        )
        await cb.message.edit_text(
            f"<b>{html.escape(title)}</b>\n<code>────────</code>\n\n"
            f"<b>Сейчас:</b> {_doc_url_preview(cur_u)}\n\n"
            "Пришлите <b>полную ссылку</b> (<code>https://…</code>)\n"
            "или символ <code>-</code>, чтобы убрать ссылку.\n\n"
            "<code>/cancel</code> — отменить ввод.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⛔️ Убрать ссылку",
                            callback_data=f"adm:doc:clr:{kind}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="« Документы", callback_data="adm:docs"
                        )
                    ],
                ]
            ),
        )
        return
    if data == "adm:list":
        rows = db.dynamic_promo_list(limit=30)
        text, kb = _promo_list_view(rows)
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return


def _parse_promo_line(text: str) -> tuple[str, int] | None:
    raw = text.strip()
    if not raw:
        return None
    parts = raw.split()
    if len(parts) == 1:
        return parts[0], 0
    code = parts[0]
    try:
        lim = int(parts[1])
    except ValueError:
        lim = 0
    return code, max(0, lim)


async def admin_try_handle_text(
    message: Message,
    *,
    sess_uid: dict[str, Any],
    db: Database,
    settings: Settings,
    kb_main: Any,
) -> bool:
    """Возвращает True, если сообщение обработано как админ-ввод."""
    assert message.from_user and message.text
    uid = message.from_user.id
    if uid not in settings.admin_ids:
        return False

    doc_expect = sess_uid.get(ADMIN_SESS_DOC)
    if doc_expect in ("terms", "privacy"):
        raw = message.text.strip()
        if raw in ("-", "—", "сброс", "Сброс"):
            sess_uid.pop(ADMIN_SESS_DOC, None)
            db.set_legal_document_url(doc_expect, "")
            await message.answer(
                "<b>Ссылка снята.</b> Пользователи не увидят этот пункт, пока не зададите URL снова.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        if not (raw.startswith("http://") or raw.startswith("https://")):
            await message.answer(
                "<b>Нужна ссылка</b> с протоколом <code>http://</code> или <code>https://</code>.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        if len(raw) > 2048:
            await message.answer(
                "<b>Слишком длинная ссылка.</b> Максимум 2048 символов.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        sess_uid.pop(ADMIN_SESS_DOC, None)
        db.set_legal_document_url(doc_expect, raw)
        label = (
            "Пользовательское соглашение"
            if doc_expect == "terms"
            else "Политика конфиденциальности"
        )
        await message.answer(
            f"<b>Сохранено:</b> {html.escape(label)}\n<code>{html.escape(raw)}</code>",
            parse_mode="HTML",
            reply_markup=kb_main,
        )
        return True

    kind = sess_uid.get(ADMIN_SESS_PROMO)
    if kind == "luck":
        parsed = _parse_promo_line(message.text)
        sess_uid.pop(ADMIN_SESS_PROMO, None)
        if not parsed:
            await message.answer(
                "<b>Формат не распознан.</b> Пример: <code>MYCODE 25</code>",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        code, max_u = parsed
        ok, reason = db.dynamic_promo_create(code, "luck", max_u)
        if ok:
            lim_txt = "без лимита" if max_u <= 0 else f"до {max_u} активаций"
            await message.answer(
                f"<b>Готово.</b> Код <code>{html.escape(code.upper())}</code> "
                f"(luck) — <i>{lim_txt}</i>.\n\n"
                "Пользователь вводит его в том же месте, что и обычный промокод.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
        else:
            err = {
                "exists": "такой код уже есть в базе",
                "format": "неверный формат кода (латиница, цифры, _, 3–40 символов)",
                "kind": "внутренняя ошибка вида",
                "plus_days": "неверный срок PLUS",
            }.get(reason, reason)
            await message.answer(
                f"<b>Не создано:</b> {html.escape(err)}",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
        return True

    if isinstance(kind, str) and kind.startswith("plus:"):
        tkey = kind.split(":", 1)[1]
        tar = plus_tariff_by_key(tkey)
        if not tar:
            sess_uid.pop(ADMIN_SESS_PROMO, None)
            await message.answer(
                "<b>Сессия сброшена.</b> Откройте снова: <code>/admin</code> → промокод PLUS.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        parsed = _parse_promo_line(message.text)
        sess_uid.pop(ADMIN_SESS_PROMO, None)
        if not parsed:
            await message.answer(
                "<b>Формат не распознан.</b> Пример: <code>MYCODE 25</code>",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        code, max_u = parsed
        ok, reason = db.dynamic_promo_create(
            code, "plus", max_u, plus_days=tar.days
        )
        if ok:
            lim_txt = "без лимита" if max_u <= 0 else f"до {max_u} активаций"
            per = _promo_plus_period_ru(tar.days)
            await message.answer(
                f"<b>Готово.</b> Код <code>{html.escape(code.upper())}</code> "
                f"(PLUS, <i>{html.escape(per)}</i>) — <i>{lim_txt}</i>.\n\n"
                "Пользователь вводит его там же, где обычный промокод PLUS.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
        else:
            err = {
                "exists": "такой код уже есть в базе",
                "format": "неверный формат кода (латиница, цифры, _, 3–40 символов)",
                "kind": "внутренняя ошибка вида",
                "plus_days": "неверный срок PLUS",
            }.get(reason, reason)
            await message.answer(
                f"<b>Не создано:</b> {html.escape(err)}",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
        return True

    bc_mode = sess_uid.get(ADMIN_SESS_BROADCAST)
    if bc_mode in ("all", "plus"):
        sess_uid.pop(ADMIN_SESS_BROADCAST, None)
        text = message.text.strip()
        if len(text) > 4090:
            await message.answer(
                "<b>Слишком длинно.</b> Укоротите до ~4000 символов.",
                parse_mode="HTML",
                reply_markup=kb_main,
            )
            return True
        uids = (
            db.list_all_user_ids()
            if bc_mode == "all"
            else db.list_plus_user_ids()
        )
        aud = "всем в базе" if bc_mode == "all" else "только PLUS"
        await message.answer(
            f"<b>Рассылка запущена</b> ({html.escape(aud)}) — "
            f"получателей: <code>{len(uids)}</code>. Это может занять минуту…",
            parse_mode="HTML",
        )
        ok_n = 0
        fail_n = 0
        bot = message.bot
        for target in uids:
            try:
                await bot.send_message(target, text, parse_mode="HTML")
                ok_n += 1
            except Exception:
                fail_n += 1
                log.debug("broadcast skip user %s", target, exc_info=False)
            await asyncio.sleep(0.04)
        await message.answer(
            "<b>Рассылка завершена.</b>\n"
            f"• доставлено: <b>{ok_n}</b>\n"
            f"• пропущено (блок / старт не нажимали): <b>{fail_n}</b>",
            parse_mode="HTML",
            reply_markup=kb_main,
        )
        return True

    return False


def redeem_plus_code(
    text: str,
    uid: int,
    *,
    db: Database,
    settings: Settings,
) -> tuple[bool, str, int | None]:
    """(успех, reason, plus_days). ``plus_days`` — ``None`` без срока; иначе дней продления (динамический код)."""
    t = text.strip().upper()
    if t == settings.plus_promo_code:
        return True, "env", None
    ok, _reason, pd = db.dynamic_promo_redeem(t, uid, "plus")
    if ok:
        return True, "dynamic", pd
    return False, _reason, None


def redeem_luck_code(
    text: str,
    uid: int,
    *,
    db: Database,
    settings: Settings,
) -> tuple[bool, str]:
    t = text.strip().upper()
    if settings.luck_promo_code and t == settings.luck_promo_code:
        if settings.luck_promo_max_uses > 0:
            if not db.luck_promo_try_consume(
                settings.luck_promo_code, settings.luck_promo_max_uses
            ):
                return False, "limit_env"
        return True, "env"
    ok, reason, _pd = db.dynamic_promo_redeem(t, uid, "luck")
    if ok:
        return True, "dynamic"
    return False, reason


def luck_promo_entry_available(settings: Settings, db: Database) -> bool:
    return bool(settings.luck_promo_code) or db.has_active_dynamic_promo("luck")


def legal_documents_user_html(db: Database) -> str:
    """Текст для кнопки «Документы» у пользователей (HTML + ссылки)."""
    t = db.get_legal_document_url("terms")
    p = db.get_legal_document_url("privacy")
    if not t and not p:
        return (
            "<b>📄 Документы</b>\n<code>────────</code>\n\n"
            "<i>Ссылки ещё не добавлены. Если нужны юридические тексты — напишите в поддержку.</i>"
        )
    lines = [
        "<b>📄 Документы</b>",
        "<code>────────</code>",
        "",
    ]
    if t:
        lines.append(
            f'• <a href="{html.escape(t, quote=True)}">Пользовательское соглашение</a>'
        )
    if p:
        lines.append(
            f'• <a href="{html.escape(p, quote=True)}">Политика конфиденциальности</a>'
        )
    return "\n".join(lines)
