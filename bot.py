"""
Telegram-бот: выдача username из латинских букв (a–z).

Режим по умолчанию (fragment): без Telethon — только запросы к fragment.com,
ник не должен числиться в продажах/аукционе Fragment.

Режим telethon (если в .env заданы TELEGRAM_API_ID / TELEGRAM_API_HASH):
проверка свободы через account.checkUsername.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from checker import (
    DisabledUsernameChecker,
    UsernameChecker,
    find_available_batch,
    telethon_connection_class,
)
from config import Settings, load_settings
from db import Database
from fragment_scraper import collect_usernames_not_on_fragment

# --- Константы UI ---
BTN_SEARCH = "🔎 Искать ник"
BTN_CABINET = "👤 Личный кабинет"
BTN_SUPPORT = "💬 Поддержка"
BTN_PLUS = "⭐ Подписка PLUS"

# Сообщения без «/» не считаются командами в Telegram — дублируем /start текстом.
_TEXT_START_ALIASES = frozenset({"старт", "start", "начать"})

# Состояния ожидания промокода: user_id -> True
PENDING_PROMO: dict[int, bool] = {}

log = logging.getLogger(__name__)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SEARCH), KeyboardButton(BTN_CABINET)],
            [KeyboardButton(BTN_SUPPORT), KeyboardButton(BTN_PLUS)],
        ],
        resize_keyboard=True,
    )


def cabinet_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📁 Сохраненные имена", callback_data="cab:saved")],
            [InlineKeyboardButton("◀️ В главное меню", callback_data="cab:back")],
        ]
    )


def length_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Длина 5", callback_data="search:5"),
                InlineKeyboardButton("Длина 6", callback_data="search:6"),
                InlineKeyboardButton("Длина 7", callback_data="search:7"),
            ],
            [InlineKeyboardButton("Отмена", callback_data="search:cancel")],
        ]
    )


def format_cabinet_text(db: Database, user_id: int, settings: Settings) -> str:
    u = db.get_or_create_user(user_id)
    rem = db.searches_remaining(user_id, settings.free_search_limit)
    lines = [
        "<b>Личный кабинет</b>",
        "",
        f"Подписка: {'<b>PLUS</b> ✅' if u.is_plus else 'Бесплатная'}",
    ]
    if rem is None:
        lines.append("Поиски: безлимит")
    else:
        lines.append(f"Осталось бесплатных поисков: <b>{rem}</b> из {settings.free_search_limit}")
    lines.append("")
    lines.append("Выберите раздел:")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    db.get_or_create_user(update.effective_user.id)
    if settings.bot_mode == "fragment":
        intro = (
            "Привет! Я подбираю <b>username</b> из латинских букв <code>a–z</code> длиной <b>5–7</b>.\n\n"
            "Проверка только через <b>Fragment</b>: ник не должен быть в продажах/аукционе на fragment.com. "
            "Занятость в самом Telegram я <b>не</b> проверяю.\n\n"
        )
    else:
        intro = (
            "Привет! Я ищу <b>свободные username</b> из латинских букв длиной <b>5–7</b> "
            "(проверка через Telethon / Telegram).\n\n"
        )
    await update.message.reply_html(
        intro
        + f"Бесплатно — <b>{settings.free_search_limit}</b> поиска. "
        + "С <b>PLUS</b> — безлимит и сохранение найденных имён.\n\n"
        + "Выберите действие в меню ниже.",
        reply_markup=main_menu_keyboard(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message and update.message.text
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]

    if PENDING_PROMO.pop(uid, None):
        code = text.upper()
        if code == settings.plus_promo_code:
            db.set_plus(uid, True)
            await update.message.reply_html(
                "✅ Подписка <b>PLUS</b> активирована!\n"
                "Доступны безлимитный поиск и сохранение имён.",
                reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_html(
                "Промокод не подходит. Попробуйте ещё раз или нажмите «Подписка PLUS».",
                reply_markup=main_menu_keyboard(),
            )
        return

    low = text.lower()
    first_word = low.split(maxsplit=1)[0] if low else ""
    if first_word in _TEXT_START_ALIASES:
        await cmd_start(update, context)
        return

    if text == BTN_SEARCH:
        await handle_search_button(update, context)
        return
    if text == BTN_CABINET:
        await update.message.reply_html(
            format_cabinet_text(db, uid, settings),
            reply_markup=cabinet_inline(),
        )
        return
    if text == BTN_SUPPORT:
        if settings.bot_mode == "fragment":
            tech = (
                "Техническая часть: подбор и проверка по страницам <b>fragment.com</b> (HTTP). "
                "При блокировках используйте <code>PROXY</code> в .env."
            )
        else:
            tech = (
                "Техническая часть: проверка ников идёт через аккаунт Telegram (Telethon), "
                "соблюдайте разумные паузы, чтобы не словить FloodWait."
            )
        await update.message.reply_html(
            "<b>Поддержка</b>\n\n"
            "Напишите владельцу бота или укажите контакт в настройках.\n"
            f"{tech}",
            reply_markup=main_menu_keyboard(),
        )
        return
    if text == BTN_PLUS:
        await update.message.reply_html(
            "<b>Подписка PLUS</b>\n\n"
            "• Безлимитные поиски\n"
            "• Кнопка «Сохранить» у найденных ников\n"
            "• Раздел «Сохраненные имена» в кабинете\n\n"
            "<i>Пробная версия:</i> отправьте промокод одним сообщением "
            f"(код по умолчанию в .env: <code>{settings.plus_promo_code}</code>)\n\n"
            "Нажмите кнопку ниже и затем введите промокод текстом.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Ввести промокод", callback_data="plus:enter")]]
            ),
        )
        return

    await update.message.reply_html(
        "Используйте кнопки меню ниже.\n\n"
        "<i>Приветствие и клавиатура:</i> <code>/start</code> <i>(со слэшем) или</i> "
        "<code>старт</code> / <code>start</code>.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_search_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    uid = update.effective_user.id
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]

    if not db.can_search(uid, settings.free_search_limit):
        await update.message.reply_html(
            "Бесплатные поиски закончились. Оформите <b>PLUS</b> в разделе подписки.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await update.message.reply_html(
        "Выберите длину username (только латинские буквы <code>a–z</code>):",
        reply_markup=length_inline(),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.callback_query and update.effective_user
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = update.effective_user.id
    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    checker = context.bot_data["checker"]

    if data == "plus:enter":
        PENDING_PROMO[uid] = True
        await q.edit_message_text(
            "Введите промокод одним сообщением (или /cancel для отмены)."
        )
        return

    if data == "cab:back":
        await q.edit_message_text("Возврат в меню — используйте кнопки снизу.")
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="Главное меню:",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data == "cab:saved":
        if not db.is_plus(uid):
            await q.edit_message_text(
                "Сохраненные имена доступны только с подпиской PLUS.",
            )
            return
        names = db.list_saved(uid)
        if not names:
            body = "Пока пусто. Найдите ник и нажмите «Сохранить» под результатом."
        else:
            body = "\n".join(f"@{n}" for n in names)
        await q.edit_message_text(f"Сохраненные ({len(names)}):\n\n{body}")
        return

    if data.startswith("save:"):
        name = data.split(":", 1)[1].lower()
        if not db.is_plus(uid):
            await q.answer("Нужна подписка PLUS", show_alert=True)
            return
        ok = db.save_username(uid, name)
        await q.answer("Сохранено ✅" if ok else "Уже было в списке", show_alert=True)
        return

    if data.startswith("search:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            await q.edit_message_text("Поиск отменён.")
            return
        if action not in ("5", "6", "7"):
            await q.edit_message_text("Неизвестное действие.")
            return
        length = int(action)

        if not db.can_search(uid, settings.free_search_limit):
            await q.edit_message_text("Нет доступных поисков.")
            return

        is_plus = db.is_plus(uid)
        max_found = 12 if is_plus else 3

        try:
            if settings.bot_mode == "fragment":
                max_attempts = 180 if is_plus else 45
                await q.edit_message_text(
                    f"Подбираю ники длины <b>{length}</b> и проверяю на <b>fragment.com</b>…\n"
                    "Это может занять несколько минут (каждый кандидат — отдельный запрос).",
                    parse_mode="HTML",
                )
                proxies = settings.proxy.requests_proxies if settings.proxy else None
                found, attempts = await asyncio.to_thread(
                    collect_usernames_not_on_fragment,
                    length=length,
                    max_attempts=max_attempts,
                    max_found=max_found,
                    proxies=proxies,
                    timeout_s=25,
                    delay_between_requests_s=settings.fragment_request_delay_s,
                )
            else:
                max_attempts = 120 if is_plus else 22
                await q.edit_message_text(
                    f"Ищу свободные ники длины <b>{length}</b>…\nЭто может занять до минуты.",
                    parse_mode="HTML",
                )
                found, attempts = await find_available_batch(
                    checker,
                    length=length,
                    max_attempts=max_attempts,
                    max_found=max_found,
                )
        except Exception as e:
            log.exception("Поиск")
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=f"Ошибка при поиске: {e}\nПопробуйте позже.",
            )
            return

        db.increment_search(uid)

        if not found:
            empty_hint = (
                "Подходящих ников не найдено — попробуйте ещё раз (случайный перебор и лимиты Fragment)."
                if settings.bot_mode == "fragment"
                else "Свободных простых комбинаций не найдено — попробуйте ещё раз (случайный перебор)."
            )
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=(
                    f"За этот запуск проверено попыток: <b>{attempts}</b>.\n"
                    f"{empty_hint}"
                ),
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return

        if settings.bot_mode == "fragment":
            header = (
                f"Найдено: <b>{len(found)}</b> (проверок Fragment: {attempts}).\n"
                "Не в продажах на Fragment; в Telegram не проверялись."
            )
        else:
            header = f"Найдено свободных: <b>{len(found)}</b> (проверок: {attempts})"
        lines = [header, ""]
        buttons: list[list[InlineKeyboardButton]] = []
        for n in found:
            lines.append(f"@{n}")
            row = []
            if is_plus:
                row.append(InlineKeyboardButton(f"Сохранить @{n}", callback_data=f"save:{n}"))
            if row:
                buttons.append(row)

        tail = "" if is_plus else "\n\n<i>Сохранение — в подписке PLUS.</i>"
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="\n".join(lines) + tail,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="Меню:",
            reply_markup=main_menu_keyboard(),
        )
        return


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_user and update.message
    PENDING_PROMO.pop(update.effective_user.id, None)
    await update.message.reply_text("Ок.", reply_markup=main_menu_keyboard())


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


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    checker = application.bot_data["checker"]
    if settings.bot_mode == "telethon" and getattr(checker, "uses_telethon", False):
        await checker.start()


async def post_shutdown(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    checker = application.bot_data["checker"]
    if settings.bot_mode == "telethon" and getattr(checker, "uses_telethon", False):
        await checker.stop()


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
    if settings.username_check_mode == "disabled" or not settings.api_id or not settings.api_hash:
        checker = DisabledUsernameChecker()
        log.info(
            "Telethon-проверка @username выключена (нет API/HASH, disabled или режим fragment без ключей)."
        )
    else:
        if settings.mtproxy and settings.telethon_use_mtproxy:
            log.info(
                "Telethon: MTProxy %s:%s (только проверка ников; бот — long polling через PROXY при наличии).",
                settings.mtproxy.host,
                settings.mtproxy.port,
            )
            checker = UsernameChecker(
                settings.api_id,
                settings.api_hash,
                settings.telethon_session,
                mtproxy=settings.mtproxy,
                mtproxy_tcp_mode=settings.mtproxy_telethon_connection,
                timeout=settings.telethon_timeout,
                connection_retries=settings.telethon_connection_retries,
            )
        else:
            th_proxy = None
            if settings.proxy and settings.telethon_use_proxy:
                th_proxy = settings.proxy.telethon_proxy
            checker = UsernameChecker(
                settings.api_id,
                settings.api_hash,
                settings.telethon_session,
                proxy=th_proxy,
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
    if settings.proxy and settings.bot_api_use_proxy:
        builder = builder.request(HTTPXRequest(proxy=settings.proxy.httpx_proxy_url))
        if settings.mtproxy and settings.telethon_use_mtproxy:
            log.info("Long polling через PROXY; Telethon (проверка ников) — через MTProxy.")
    elif settings.proxy and not settings.bot_api_use_proxy:
        log.info(
            "BOT_API_USE_PROXY=0: Bot API без HTTP/SOCKS; PROXY для Fragment/requests сохранён."
        )
    application = builder.build()
    application.bot_data["db"] = db
    application.bot_data["settings"] = settings
    application.bot_data["checker"] = checker

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("grant_plus", cmd_grant_plus))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Бот запущен (long polling)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
