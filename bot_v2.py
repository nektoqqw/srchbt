"""
Telegram-бот MVP v0.2:
- Проверка «свободен для установки» — только через MTProto (Telethon, account.checkUsername).
  У Bot API официальной замены нет.
- Оценка ценности — по импортированным данным Fragment (веб).
- Режим без Telethon: USERNAME_CHECK_MODE=disabled (только оценка, без проверки занятости).
- Весь бот к Telegram через MTProto (Telethon): USE_MTProto_BOT=1 (без long polling Bot API).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import sys
from functools import partial
from typing import Final

import ui_theme as theme

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    Application,
)
from checker import (
    DisabledUsernameChecker,
    UsernameChecker,
    normalize_username,
    is_valid_telegram_username_for_roll,
    is_valid_telegram_username,
    random_letters_username,
    telethon_connection_class,
)
from config import Settings, load_settings
from channel_gate import (
    SUB_CHECK_CALLBACK,
    ptb_user_is_channel_member,
    ptb_user_may_use_bot,
)
from db import Database, SAVED_USERNAMES_LIMIT
from fragment_scraper import fetch_fragment_gift_price
log = logging.getLogger(__name__)


# ---- UI ----
BTN_ROLL: Final[str] = "🎲 Ролл по ценности"
BTN_ANALYZE: Final[str] = "🔍 Проверить и оценить"
BTN_TOP: Final[str] = "📈 Топ за месяц"
BTN_CABINET: Final[str] = f"{theme.CABINET} Личный кабинет"
BTN_SUPPORT: Final[str] = f"{theme.SUPPORT} Поддержка"
BTN_PLUS: Final[str] = f"{theme.PLUS} Подписка PLUS"


MAIN_MENU_KB: Final[ReplyKeyboardMarkup] = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_ROLL), KeyboardButton(BTN_ANALYZE)],
        [KeyboardButton(BTN_TOP), KeyboardButton(BTN_CABINET)],
        [KeyboardButton(BTN_SUPPORT), KeyboardButton(BTN_PLUS)],
    ],
    resize_keyboard=True,
)


def cabinet_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💾 Сохранённые имена", callback_data="cab:saved")],
            [InlineKeyboardButton("◀️ В меню", callback_data="cab:back")],
        ]
    )


def rarity_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("СВЕРХЛЕГЕНДАРНО!!!", callback_data="roll:tier:super"),
            ],
            [
                InlineKeyboardButton("Легендарный", callback_data="roll:tier:legendary"),
                InlineKeyboardButton("Мифический", callback_data="roll:tier:mythic"),
            ],
            [
                InlineKeyboardButton("Эпический", callback_data="roll:tier:epic"),
                InlineKeyboardButton("Редкий", callback_data="roll:tier:rare"),
            ],
            [
                InlineKeyboardButton("Обычный", callback_data="roll:tier:common"),
                InlineKeyboardButton("Любой", callback_data="roll:tier:any"),
            ],
        ]
    )


def length_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Длина 5", callback_data="roll:len:5"),
                InlineKeyboardButton("Длина 6", callback_data="roll:len:6"),
            ],
            [InlineKeyboardButton("Отмена", callback_data="roll:cancel")],
        ]
    )


def save_inline_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"Сохранить @{username}", callback_data=f"save:{username}")]])


# ---- Roll tier mapping (desired minimum) ----
TIER_MIN_USD: Final[dict[str, float]] = {
    "any": 0.0,
    "common": 0.0,
    "rare": 10.0,
    "epic": 50.0,
    "mythic": 150.0,
    "legendary": 500.0,
    "super": 1000.0,
}


# ожидание промокода: uid -> True
PENDING_PROMO: dict[int, bool] = {}

# Сообщения без ведущего «/» не являются командами в Telegram — дублируем /start текстом.
_TEXT_START_ALIASES: Final[frozenset[str]] = frozenset({"старт", "start", "начать"})


def _format_telegram_username(username: str) -> str:
    return f"@{username.lower()}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    uid = update.effective_user.id
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    if not await ptb_user_may_use_bot(update, context):
        return
    db.get_or_create_user(uid)
    if settings.username_check_mode == "disabled":
        intro = (
            "Привет! Оцениваю username по данным <b>Fragment</b>.\n\n"
            "<b>Режим без Telethon:</b> автоматически проверить «свободен ли ник для установки на аккаунт» "
            "через Bot API <b>нельзя</b> — это умеет только MTProto (Telethon). "
            "Финальную проверку делай в Telegram: Настройки → Профиль → Имя пользователя."
        )
    else:
        intro = (
            "Привет! Я помогаю находить <b>свободные для установки</b> Telegram username и оцениваю их ценность по рынку Fragment."
        )
    await update.message.reply_html(intro, reply_markup=MAIN_MENU_KB)
    await update.message.reply_html(
        f"Пробная версия: <b>{settings.free_search_limit}</b> поисков. <b>PLUS</b> — безлимит и сохранение.",
    )


async def cmd_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    if not await ptb_user_may_use_bot(update, context):
        return
    await update.message.reply_html(
        "<b>Поддержка</b>\n\n"
        "Если нужен апдейт/расширение — пишите в чат владельцу бота.",
        reply_markup=MAIN_MENU_KB,
    )


async def cmd_grant_plus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    settings: Settings = context.bot_data["settings"]
    db: Database = context.bot_data["db"]

    if update.effective_user.id not in settings.admin_ids:
        await update.message.reply_text("Команда только для администратора.")
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Использование: /grant_plus <user_id>")
        return

    target = int(args[0])
    db.set_plus(target, True)
    await update.message.reply_text(f"PLUS выдан пользователю {target}")


async def cmd_import_fragment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    settings: Settings = context.bot_data["settings"]
    db: Database = context.bot_data["db"]

    if update.effective_user.id not in settings.admin_ids:
        await update.message.reply_text("Команда только для администратора.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /import_fragment <fragment_url>")
        return
    url = args[0]

    await update.message.reply_text("Импортирую Fragment…")
    try:
        gift = await asyncio.to_thread(
            partial(
                fetch_fragment_gift_price,
                url,
                ton_to_usd=settings.ton_to_usd,
            )
        )
    except Exception as e:
        log.exception("import_fragment failed")
        await update.message.reply_text(f"Не удалось импортировать: {e}")
        return

    # Импортируем даже если цена не распознана — прогноз станет лучше после новых страниц.
    db.upsert_fragment_item(
        username=gift.username,
        price_usd=gift.price_usd,
        source_url=gift.source_url,
    )

    if gift.price_usd is None:
        await update.message.reply_text(f"Импортирован username: @{gift.username} (цена не распознана).")
    else:
        await update.message.reply_text(f"Импорт: @{gift.username} ~ ${gift.price_usd:,.0f} (сохранено).")


async def post_init(application: Application) -> None:
    checker = application.bot_data["checker"]
    if getattr(checker, "uses_telethon", False):
        await checker.start()


async def post_shutdown(application: Application) -> None:
    checker = application.bot_data["checker"]
    if getattr(checker, "uses_telethon", False):
        await checker.stop()


def _start_roll_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.callback_query or update.message
    # Для простоты: сначала выбираем длину, затем редкость.
    if update.callback_query:
        context_data = context.user_data
        context_data.pop("roll_len", None)
        context_data.pop("roll_tier", None)

        update.callback_query.edit_message_text(
            "Выберите длину username (5–6 латинских букв):",
            reply_markup=length_inline_kb(),
        )
    else:
        assert update.message
        context.user_data.pop("roll_len", None)
        context.user_data.pop("roll_tier", None)
        # Telegram keyboard expects async; but this helper is sync used only for message text.


async def start_roll_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    context.user_data.pop("roll_len", None)
    context.user_data.pop("roll_tier", None)
    await update.message.reply_text(
        "Выберите длину username (5–6 латинских букв):",
        reply_markup=length_inline_kb(),
    )


async def start_analysis_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    context.user_data["await_username"] = True
    await update.message.reply_text("Введи username (например cache). Без @ тоже можно.")


async def format_cabinet(db: Database, uid: int, settings: Settings) -> str:
    u = db.get_or_create_user(uid)
    rem = db.searches_remaining(uid, settings.free_search_limit)
    lines = [
        "<b>Личный кабинет</b>",
        "",
        f"Подписка: {'<b>PLUS</b> ' + theme.OK if u.is_plus else 'Бесплатная'}",
    ]
    if rem is None:
        lines.append("Поиски: безлимит")
    else:
        lines.append(f"Осталось бесплатных поисков: <b>{rem}</b> из {settings.free_search_limit}")
    lines.append("")
    lines.append("Нажмите кнопку ниже:")
    return "\n".join(lines)


async def _check_and_decrement_search(db: Database, uid: int, settings: Settings) -> bool:
    if not db.can_search(uid, settings.free_search_limit):
        return False
    db.increment_search(uid)
    return True


def _letters_only_and_length_ok(username: str, length: int) -> bool:
    return bool(re.fullmatch(r"[a-z]{%d}" % length, username))


async def perform_roll(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    uid: int,
    length: int,
    tier_key: str,
) -> None:
    assert uid
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    checker = context.bot_data["checker"]

    ok = await _check_and_decrement_search(db, uid, settings)
    if not ok:
        await update.callback_query.edit_message_text(
            "Бесплатные поиски закончились. Оформите <b>PLUS</b>.",
            parse_mode="HTML",
            reply_markup=None,
        )
        return

    desired_min = TIER_MIN_USD.get(tier_key, 0.0)
    is_plus = db.is_plus(uid)

    await update.callback_query.edit_message_text(
        f"Роллим username длины <b>{length}</b>…\nЦель: от <b>${desired_min:,.0f}</b>.",
        parse_mode="HTML",
    )

    # 1) Берём кандидатов с Fragment-данными нужной цены (если есть)
    items = db.iter_fragment_items(limit=5000)
    candidates = []
    for it in items:
        if it.price_usd is None:
            continue
        if float(it.price_usd) < desired_min:
            continue
        if len(it.username) != length:
            continue
        if not _letters_only_and_length_ok(it.username, length):
            continue
        candidates.append((it.username, float(it.price_usd)))

    # перестраховка: перемешаем, но начнём с более дорогих
    candidates.sort(key=lambda x: x[1], reverse=True)
    # 2) Проверка доступности через Telethon (или режим без проверки)
    found: list[tuple[str, float | None, str, str]] = []  # username, price, rarity_name, why
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
                rarity_info, predicted_price, why = _rarity_for_display(uname, db)
                found.append((uname, predicted_price, rarity_info.name, why))
            elif avail is False:
                continue
            elif settings.username_check_mode == "disabled":
                rarity_info, predicted_price, why = _rarity_for_display(uname, db)
                found.append(
                    (
                        uname,
                        predicted_price,
                        rarity_info.name,
                        why + f" | {theme.WARN} занятость в Telegram не проверялась (Telethon выключен).",
                    )
                )
        except Exception:
            log.exception("checker.is_available failed for %s", uname)
            continue

    # 3) Fallback: Telethon — только если список пуст (как раньше); без Telethon — добираем до want случайными идеями
    if not found:
        attempts = 0
        if settings.username_check_mode == "disabled":
            while attempts < 30 and len(found) < want:
                attempts += 1
                cand = random_letters_username(length)
                if not is_valid_telegram_username_for_roll(cand, min_len=length, max_len=length):
                    continue
                try:
                    rarity_info, predicted_price, why = _rarity_for_display(cand, db)
                    found.append(
                        (
                            cand,
                            predicted_price,
                            rarity_info.name,
                            why + f" | {theme.WARN} случайный ник, занятость не проверялась (Telethon выключен).",
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
                        rarity_info, predicted_price, why = _rarity_for_display(cand, db)
                        found.append((cand, predicted_price, rarity_info.name, why))
                except Exception:
                    log.exception("fallback checker.is_available failed")
                    continue

    # 4) Ответ пользователю
    if not found:
        await update.callback_query.edit_message_text(
            "Не получилось найти свободные username за разумное время. Попробуйте ещё раз.",
            reply_markup=None,
        )
        return

    # сохраняем события для топа
    for uname, predicted_price, rarity_name, _why in found:
        db.add_roll_event(
            user_id=uid,
            username=uname,
            rarity=rarity_name,
            predicted_price_usd=predicted_price,
        )

    # формируем карточку
    if settings.username_check_mode == "disabled":
        lines = [f"Варианты: <b>{len(found)}</b> (оценка без проверки занятости в Telegram)", ""]
    else:
        lines = [f"Найдено свободных: <b>{len(found)}</b>", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for uname, predicted_price, rarity_name, _why in found:
        usd_txt = "?" if predicted_price is None else f"${predicted_price:,.0f}"
        lines.append(f"• {_format_telegram_username(uname)}")
        lines.append(f"  Оценка: <b>{usd_txt}</b> | Редкость: <b>{html.escape(rarity_name)}</b>")
        lines.append("")
        if is_plus:
            buttons.append([InlineKeyboardButton(f"Сохранить @{uname}", callback_data=f"save:{uname}")])

    if not is_plus:
        lines.append("<i>Сохранение доступно в PLUS.</i>")

    await update.callback_query.edit_message_text(
        "\n".join(lines).strip(),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


def _rarity_for_display(username: str, db: Database):
    rarity_info, predicted_price, why = None, None, ""
    r, price, w = None, None, None
    # value_model.rarity_tier_for_username возвращает (RarityInfo, price, why), но в MVP держим прямое использование:
    from value_model import rarity_tier_for_username

    r, price, w = rarity_tier_for_username(username, db)
    return r, price, w


async def perform_analysis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    uid: int,
    raw_username: str,
) -> None:
    assert update.message
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    checker = context.bot_data["checker"]

    uname = normalize_username(raw_username)
    # Валидация формата Telegram: a-z, 0-9, '_' и длина 5-32
    if not is_valid_telegram_username(uname):
        await update.message.reply_text(
            "Некорректный username. Должно быть: 5–32 символа, только a-z, 0-9, '_' (без @ можно)."
        )
        return

    ok = await _check_and_decrement_search(db, uid, settings)
    if not ok:
        await update.message.reply_text(
            "Бесплатные поиски закончились. Оформите <b>PLUS</b>.",
            parse_mode="HTML",
            reply_markup=MAIN_MENU_KB,
        )
        return

    if settings.username_check_mode == "disabled":
        await update.message.reply_text("Считаю оценку (режим без Telethon — занятость в Telegram не проверяю)…")
        available: bool | None = None
    else:
        await update.message.reply_text("Проверяю валидность (свободен/занят) и оценку…")
        try:
            available = await checker.is_available(uname)
        except Exception as e:
            log.exception("analysis checker error")
            await update.message.reply_text(f"Ошибка проверки username: {e}\nПопробуйте позже.")
            return

    rarity_info, predicted_price, why = _rarity_for_display(uname, db)
    usd_txt = "?" if predicted_price is None else f"${predicted_price:,.0f}"

    if available is None:
        status = (
            f"{theme.WARN} <b>Автопроверка «свободен для установки» недоступна</b> (режим без Telethon). "
            "В Telegram: Настройки → Профиль → Имя пользователя — попробуйте назначить этот логин вручную."
        )
    elif available:
        status = f"{theme.OK} Свободен для установки (по checkUsername)"
    else:
        status = f"{theme.FAIL} Занят или недоступен (не проходит checkUsername)"

    lines = [
        f"{_format_telegram_username(uname)}",
        "",
        f"{status}",
        f"Оценка ценности: <b>{usd_txt}</b>",
        f"Редкость: <b>{html.escape(rarity_info.name)}</b>",
        f"Причина: {html.escape(why)}",
    ]

    is_plus = db.is_plus(uid)
    if is_plus:
        msg = "\n".join(lines)
        await update.message.reply_html(msg, reply_markup=save_inline_kb(uname))
    else:
        msg = "\n".join(lines) + "\n\n<i>Сохранение доступно в PLUS.</i>"
        await update.message.reply_html(msg, reply_markup=MAIN_MENU_KB)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    uid = update.effective_user.id
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    assert update.message.text
    if not await ptb_user_may_use_bot(update, context):
        return
    text = update.message.text.strip()

    # Режим ожидания username для анализа
    if context.user_data.get("await_username"):
        context.user_data["await_username"] = False
        await perform_analysis(update, context, uid=uid, raw_username=text)
        return

    if PENDING_PROMO.pop(uid, None):
        code = text.upper()
        if code == settings.plus_promo_code:
            db.set_plus(uid, True)
            await update.message.reply_html(
                f"{theme.OK} PLUS активирован!\nТеперь можно сохранять найденные username и роллить без лимитов.",
                reply_markup=MAIN_MENU_KB,
            )
        else:
            await update.message.reply_html(
                "Промокод не подходит. Попробуйте снова.",
                reply_markup=MAIN_MENU_KB,
            )
        return

    low = text.lower()
    first_word = low.split(maxsplit=1)[0] if low else ""
    if first_word in _TEXT_START_ALIASES:
        await cmd_start(update, context)
        return

    if text == BTN_ROLL:
        await start_roll_from_message(update, context)
        return
    if text == BTN_ANALYZE:
        await start_analysis_from_message(update, context)
        return
    if text == BTN_TOP:
        top = db.top_roll_month(days=30, limit=10)
        if not top:
            await update.message.reply_text(
                "Пока нет данных за месяц. Нажмите «Ролл» и крутите — топ сформируется."
            )
            return
        lines = ["<b>Топ username за 30 дней</b>", ""]
        for i, (uname, rarity, pred) in enumerate(top, start=1):
            usd_txt = "?" if pred is None else f"${pred:,.0f}"
            lines.append(f"{i}. @{uname} — <b>{rarity}</b> ({usd_txt})")
        await update.message.reply_html("\n".join(lines))
        return
    if text == BTN_CABINET:
        await update.message.reply_html(
            await format_cabinet(db, uid, settings),
            reply_markup=cabinet_inline_kb(),
        )
        return
    if text == BTN_SUPPORT:
        await update.message.reply_html(
            "<b>Поддержка</b>\n\n"
            "Если нужно улучшить бота (скорость, больше паттернов, больше источников Fragment) — пишите в чат.",
            reply_markup=MAIN_MENU_KB,
        )
        return
    if text == BTN_PLUS:
        await update.message.reply_html(
            "<b>Подписка PLUS</b>\n\n"
            "• Безлимитные поиски\n"
            "• Сохранение найденных username\n"
            "• Топы и история в кабинете\n\n"
            "Пробная версия: отправьте промокод одним сообщением.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ввести промокод", callback_data="plus:enter")]]),
        )
        return

    await update.message.reply_html(
        "Используйте кнопки меню ниже.\n\n"
        "<i>Открыть приветствие и клавиатуру:</i> <code>/start</code> <i>(обязательно со слэшем в начале) "
        "или одно слово</i> <code>старт</code> / <code>start</code>.",
        reply_markup=MAIN_MENU_KB,
    )


def _v2_saved_list_kb(names: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🗑 @{n}", callback_data=f"saved_del:{n}")]
            for n in names
        ]
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.callback_query and update.effective_user
    q = update.callback_query
    uid = update.effective_user.id
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]

    data = q.data or ""

    if data == SUB_CHECK_CALLBACK:
        if uid in settings.admin_ids or not settings.required_channel_username:
            await q.answer()
            return
        ok = await ptb_user_is_channel_member(
            context.bot, settings.required_channel_username, uid
        )
        if ok:
            await q.answer(f"{theme.OK} Подписка подтверждена!")
            await q.message.reply_html(
                f"<b>{theme.OK} Канал подписан.</b> Дальше — кнопки меню или <code>/start</code>.",
                reply_markup=MAIN_MENU_KB,
            )
        else:
            await q.answer(
                "Сначала вступите в канал, затем нажмите снова.",
                show_alert=True,
            )
        return

    if (
        settings.required_channel_username
        and uid not in settings.admin_ids
        and not await ptb_user_is_channel_member(
            context.bot, settings.required_channel_username, uid
        )
    ):
        await q.answer(
            "Сначала подпишитесь на канал бота (см. сообщение при /start).",
            show_alert=True,
        )
        return

    await q.answer()

    if data.startswith("saved_del:"):
        if not db.is_plus(uid):
            return
        nick = data.removeprefix("saved_del:").strip()
        uname = normalize_username(nick)
        if not is_valid_telegram_username(uname):
            await q.message.reply_html("<b>Некорректный ник.</b>")
            return
        db.remove_saved(uid, uname)
        names = db.list_saved(uid)
        if not names:
            await q.edit_message_text(
                f"<b>Сохранённые</b> (0/{SAVED_USERNAMES_LIMIT})\n\nСписок пуст.",
                parse_mode="HTML",
            )
        else:
            lines = ["<b>Сохранённые имена</b>", ""]
            for n in names:
                lines.append(f"• @{n}")
            await q.edit_message_text(
                "\n".join(lines)
                + f"\n\n<i>Нажмите 🗑 для удаления. Лимит {SAVED_USERNAMES_LIMIT}.</i>",
                parse_mode="HTML",
                reply_markup=_v2_saved_list_kb(names),
            )
        await q.message.reply_html("🗑 Ник удалён из сохранённых.")
        return

    if data == "cab:saved":
        if not db.is_plus(uid):
            await q.edit_message_text("Сохранённые имена доступны только с PLUS.")
            return
        names = db.list_saved(uid)
        if not names:
            await q.edit_message_text(
                "Пока пусто. Крутите ролл или проверьте ник и нажмите «Сохранить».\n\n"
                f"<i>Можно хранить до {SAVED_USERNAMES_LIMIT} ников.</i>",
                parse_mode="HTML",
            )
            return
        lines = ["<b>Сохранённые имена</b>", ""]
        for n in names:
            lines.append(f"• @{n}")
        await q.edit_message_text(
            "\n".join(lines)
            + f"\n\n<i>Нажмите 🗑 для удаления. Лимит {SAVED_USERNAMES_LIMIT}.</i>",
            parse_mode="HTML",
            reply_markup=_v2_saved_list_kb(names),
        )
        return

    if data == "cab:back":
        await q.edit_message_text("Главное меню:")
        return

    if data == "plus:enter":
        PENDING_PROMO[uid] = True
        await q.edit_message_text("Введите промокод одним сообщением (или нажмите отмену через /cancel).")
        return

    if data.startswith("save:"):
        if not db.is_plus(uid):
            await q.message.reply_html("Нужен PLUS")
            return
        uname = data.split(":", 1)[1].lower()
        res = db.save_username(uid, uname)
        if res == "saved":
            await q.message.reply_html(f"{theme.OK} Юзернейм сохранён!")
        elif res == "duplicate":
            await q.message.reply_html("Этот ник уже в списке.")
        elif res == "limit":
            await q.message.reply_html(
                f"Лимит <b>{SAVED_USERNAMES_LIMIT}</b> сохранённых. Удалите лишнее в «Сохранённые»."
            )
        return

    if data == "roll:cancel":
        context.user_data.pop("roll_len", None)
        context.user_data.pop("roll_tier", None)
        await q.edit_message_text("Ок, отменено.")
        return

    if data.startswith("roll:len:"):
        ln = int(data.split(":", 2)[2])
        context.user_data["roll_len"] = ln
        await q.edit_message_text("Выберите редкость (tier):", reply_markup=rarity_inline_kb())
        return

    if data.startswith("roll:tier:"):
        tier_key = data.split(":", 2)[2]
        context.user_data["roll_tier"] = tier_key

        if "roll_len" not in context.user_data:
            await q.edit_message_text("Сначала выберите длину. Нажмите ролл заново.")
            return

        length = int(context.user_data["roll_len"])
        context.user_data.pop("roll_len", None)
        context.user_data.pop("roll_tier", None)

        # запуск ролла
        await perform_roll(update, context, uid=uid, length=length, tier_key=tier_key)
        return

    await q.edit_message_text("Неизвестная команда.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    if not await ptb_user_may_use_bot(update, context):
        return
    PENDING_PROMO.pop(update.effective_user.id, None)
    context.user_data["await_username"] = False
    context.user_data.pop("roll_len", None)
    context.user_data.pop("roll_tier", None)
    await update.message.reply_text("Отменено.", reply_markup=MAIN_MENU_KB)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Ошибка в обработчике update", exc_info=context.error)
    if not isinstance(update, Update):
        return
    try:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Не удалось отправить ответ (часто это ошибка разметки HTML у Telegram API). "
                "Попробуйте снова /start. Подробности — в логе сервера."
            )
    except Exception:
        log.exception("Не удалось отправить пользователю сообщение об ошибке")


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )


def main() -> None:
    setup_logging()
    settings = load_settings()
    db = Database(settings.db_path.resolve())

    if settings.username_check_mode == "disabled":
        checker = DisabledUsernameChecker()
        log.warning(
            "USERNAME_CHECK_MODE=disabled: Telethon отключён — «свободен для установки» не проверяется, только оценка/Fragment."
        )
    else:
        checker = UsernameChecker(
            settings.api_id,
            settings.api_hash,
            settings.telethon_session,
            timeout=settings.telethon_timeout,
            connection_retries=settings.telethon_connection_retries,
            connection=telethon_connection_class(settings.telethon_connection),
        )

    if settings.use_mtproto_bot:
        from mtproxy_bot_runner import run_telethon_mtproto_bot_stack

        run_telethon_mtproto_bot_stack(settings, db, checker)
        return

    builder = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    application = builder.build()
    application.bot_data["db"] = db
    application.bot_data["settings"] = settings
    application.bot_data["checker"] = checker

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("grant_plus", cmd_grant_plus))
    application.add_handler(CommandHandler("import_fragment", cmd_import_fragment))

    application.add_handler(CommandHandler("support", cmd_support))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(error_handler)

    log.info("Bot v0.2 запущен (long polling)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

