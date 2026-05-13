"""
Бот на Aiogram 3 (long polling).

- BOT_MODE=fragment: подбор свободных @username по данным Fragment и (если настроено) проверка в Telegram.
- BOT_MODE=telethon: подбор по каталогу, оценка, проверка занятости через Telethon.

Запуск: python bot_aiogram.py

USE_MTProto_BOT=1 — только стек mtproxy_bot_runner (Telethon), Aiogram не используется.
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import string
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
from functools import partial
from typing import Any, Final

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.types.error_event import ErrorEvent

from checker import (
    DisabledUsernameChecker,
    UsernameChecker,
    is_valid_telegram_username,
    is_valid_telegram_username_for_roll,
    normalize_username,
    random_letters_username,
    telethon_connection_class,
)
from channel_gate_aiogram import AiogramChannelGateMiddleware
from config import Settings, load_settings
from db import Database, SAVED_USERNAMES_LIMIT
from pending_referral import clear_pending_referrer, take_pending_referrer
from referral_start import parse_referrer_id_from_start_payload
from admin_panel import (
    admin_clear_session,
    admin_handle_callback,
    admin_try_handle_text,
    cmd_admin,
    legal_documents_user_html,
    luck_promo_entry_available,
    redeem_luck_code,
    redeem_plus_code,
)
from fragment_scraper import (
    fetch_fragment_gift_price,
    username_listed_on_fragment,
)
from luck_tariffs import (
    kb_luck_payment_nav,
    kb_luck_tariffs,
    luck_shop_intro_html,
    luck_tariff_by_key,
    luck_tariff_payment_html,
)
from luck_username import luck_score
from plus_tariffs import (
    PLUS_TARIFFS,
    kb_plus_payment_nav,
    kb_plus_tariffs,
    plus_shop_intro_html,
    plus_tariff_by_key,
    plus_tariff_payment_html,
)
from roll_filters import (
    RollFilters,
    filters_summary_ru,
    generate_roll_candidate,
    username_roll_random,
)
from trader_tips import text_komandy, text_shpargalka, text_sovety
from username_rarity import combined_rarity, suggest_similar_usernames
from username_valuation import UsernameValuation, evaluate_username_market

log = logging.getLogger(__name__)


# ---------- Имя бота в подписи к сообщениям ----------
AMNYAM = "Амням"
# Ровная линия под шапкой в меню крутки (моноширинный блок)
ROLL_RULE_LINE_HTML = "<code>" + ("─" * 30) + "</code>"
# Максимальное время одного запроса подбора имени (сек.)
FRAGMENT_USERNAME_SEARCH_WALL_S = 180


async def answer_referral_program(
    message: Message, db: Database, settings: Settings
) -> None:
    assert message.from_user
    uid = message.from_user.id
    db.get_or_create_user(uid)
    bot = message.bot
    un = (settings.bot_username_for_links or "").strip().lstrip("@")
    if not un:
        un = (getattr(bot, "username", None) or "").strip()
    if not un:
        try:
            me = await bot.get_me()
            un = (me.username or "").strip()
        except Exception:
            log.exception("get_me for referral link")
            un = ""
    if not un:
        await message.answer(
            "<b>Реферальная ссылка временно недоступна.</b>\n\n"
            "Не удалось узнать @username бота через Telegram API.\n\n"
            "<b>Обходной путь:</b> в <code>.env</code> задайте имя бота <b>без @</b>:\n"
            "<code>BOT_USERNAME_FOR_LINKS=имя_вашего_бота</code>\n\n"
            "После сохранения перезапустите бота.",
            parse_mode="HTML",
        )
        return
    link = f"https://t.me/{un}?start=ref_{uid}"
    n = db.referral_count(uid)
    h = settings.referral_plus_hours
    kb = (
        kb_fragment_main(uid=uid, settings=settings)
        if settings.bot_mode == "fragment"
        else kb_v2_main(uid=uid, settings=settings)
    )
    await message.answer(
        f"<b>🤝 Реферальная программа</b> · {html.escape(AMNYAM)}\n"
        f"{ROLL_RULE_LINE_HTML}\n\n"
        f"🔗 <b>Ваша ссылка</b> <i>(нажмите, чтобы скопировать):</i>\n"
        f"<code>{html.escape(link)}</code>\n\n"
        f"📊 <b>Приглашено по ссылке:</b> <code>{n}</code>\n"
        f"🎁 За каждого <b>нового</b> пользователя, который впервые нажмёт Start по этой ссылке, "
        f"вам начисляется <b>+{h} ч</b> подписки PLUS "
        "<i>(если у вас не тариф PLUS «навсегда» без даты — тогда часы не добавляются к сроку).</i>\n\n"
        "<i>Свою статистику видно и в разделе «Аккаунт».</i>",
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _plus_promo_period_user_ru(plus_days: int | None) -> str:
    if plus_days is None:
        return "без даты окончания (как тариф «Навсегда» в витрине)."
    for t in PLUS_TARIFFS:
        if t.days == plus_days:
            return (
                f"<b>{html.escape(t.title_ru)}</b> — срок как у платного тарифа; "
                "время прибавляется от сейчас или от конца действующего PLUS."
            )
    return f"<b>{plus_days}</b> календарных дней."


def _ui_frag_pick_length() -> str:
    return (
        f"✨ <b>{html.escape(AMNYAM)}</b> крутит для вас <code>@username</code>\n"
        f"{ROLL_RULE_LINE_HTML}\n\n"
        "🔤 <b>Сколько символов?</b> <i>(латиница <code>a–z</code>, 5–7)</i>\n"
        "<i>Выбери длину кнопкой ниже 👇</i>"
    )


def _ui_roll_loading_head(tick: int) -> str:
    """Экран «крутка идёт»: переливающиеся искорки + бегущие точки."""
    dot_phases = (".", "..", "...", "..", ".")
    dots = dot_phases[tick % len(dot_phases)]
    sparks = ("✨", "💫", "🌟", "⭐", "🫧")
    spark = sparks[tick % len(sparks)]
    return (
        f"{spark} <b>{html.escape(AMNYAM)}</b> подбирает вам красивый юзернейм{dots}\n"
        "<i>Ожидайте — скоро увидите результат…</i>\n\n"
        f"{ROLL_RULE_LINE_HTML}"
    )


def _ui_frag_search_frame(tick: int) -> str:
    return _ui_roll_loading_head(tick)


def _ui_roll_spin_frame(tick: int) -> str:
    return _ui_roll_loading_head(tick)


def _ui_frag_found(
    name: str,
    *,
    rarity_name: str | None = None,
    predicted_price: float | None = None,
    why: str | None = None,
    luck_used: bool = False,
) -> str:
    n = name.lower()
    body = (
        "<b>Есть совпадение</b>\n\n"
        f"<code>@{html.escape(n)}</code>\n\n"
    )
    if rarity_name is not None:
        body += (
            _rarity_metrics_html(
                rarity_name=rarity_name,
                predicted_price=predicted_price,
                why=why or "",
                show_explanation=False,
            )
            + "\n\n"
        )
    foot: list[str] = []
    if luck_used:
        foot.append("<i>🍀 С учётом режима <b>«Удача»</b>.</i>")
    foot.append(
        "<i>Проверьте доступность юзернейма сами для большей достоверности.</i>"
    )
    body += "\n".join(foot)
    return body


def _ui_frag_fail() -> str:
    return (
        "<b>Свободное имя не найдено</b>\n\n"
        f"Попробуйте снова через кнопку <b>{html.escape(BTN_SEARCH_F)}</b>."
    )


def _ui_frag_timeout_fail() -> str:
    return (
        "<b>Не успели за отведённое время</b>\n\n"
        "Свободного варианта под текущие настройки не нашли. "
        "Смените длину или параметры в разделе фильтров (для подписчиков PLUS) и повторите запрос."
    )


def _ui_roll_wait() -> str:
    return (
        "<b>Почти готово</b>\n"
        "<i>Сверяем последние детали…</i>"
    )


def _ui_plain_error() -> str:
    return (
        f"<b>{html.escape(AMNYAM)}</b>: сервис временно недоступен. "
        "Попробуйте позже или нажмите <code>/start</code>."
    )


# ---------- Клавиатура ----------
BTN_SEARCH_F = "🎰 Крутить"
BTN_VALUATE_F = "💰 Оценка ника"
BTN_CABINET_F = "👤 Аккаунт"
BTN_SUPPORT_F = "💬 Поддержка"
BTN_PLUS_F = "♠️ Подписка PLUS"
BTN_LUCK_F = "🍀 Удача"
BTN_DOCS_F = "📄 Документы"
BTN_ADMIN_F = "⚙️ Админ"
BTN_REFERRAL_F = "🤝 Рефералка"

# ---------- Режим telethon (v2) ----------
BTN_ROLL = "🎰 Крутить"
BTN_VALUATE = "💰 Оценка ника"
BTN_TOP = "🏆 Лидеры"
BTN_CABINET = "👤 Аккаунт"
BTN_SUPPORT = "💬 Поддержка"
BTN_PLUS = "♠️ Подписка PLUS"
BTN_LUCK = "🍀 Удача"
BTN_DOCS = "📄 Документы"
BTN_ADMIN = "⚙️ Админ"
BTN_REFERRAL = "🤝 Рефералка"

_TEXT_START: Final[frozenset[str]] = frozenset({"старт", "start", "начать"})
PENDING_PROMO: dict[int, bool] = {}
PENDING_LUCK_PROMO: dict[int, bool] = {}
# сессии: await_username, roll_len, roll_tier_for pending roll selection
_sess: defaultdict[int, dict[str, Any]] = defaultdict(dict)

TIER_MIN_USD: Final[dict[str, float]] = {
    "any": 0.0,
    "common": 0.0,
    "rare": 10.0,
    "epic": 50.0,
    "mythic": 150.0,
    "legendary": 500.0,
    "super": 1000.0,
}

# Пауза перед повторной круткой по кнопке (секунды + обратный отсчёт в UI)
ROLL_REPEAT_COOLDOWN_S: Final[int] = 5
ROLL_REPEAT_STAR_FRAMES: Final[tuple[str, ...]] = ("✨", "⭐", "🌟", "💫")


def _frag_roll_state(uid: int) -> dict:
    d = _sess[uid]
    if "roll_f" not in d:
        d["roll_f"] = {"pre": "", "suf": "", "dig": "any"}
    else:
        rf = d["roll_f"]
        if not isinstance(rf, dict):
            d["roll_f"] = {"pre": "", "suf": "", "dig": "any"}
        else:
            pre = str(rf.get("pre") or "")
            suf = str(rf.get("suf") or "")
            dig = str(rf.get("dig") or "any")
            if dig not in ("any", "yes", "no"):
                dig = "any"
            d["roll_f"] = {"pre": pre, "suf": suf, "dig": dig}
    d.setdefault("roll_draft_pre", [])
    d.setdefault("roll_draft_suf", [])
    return d


def _roll_filters_obj(uid: int) -> RollFilters:
    rf = _frag_roll_state(uid)["roll_f"]
    return RollFilters(
        prefix=str(rf.get("pre") or ""),
        suffix=str(rf.get("suf") or ""),
        digits=str(rf.get("dig") or "any"),
    )


def _kb_frag_letter_grid(*, kind: str) -> InlineKeyboardMarkup:
    """kind: pre | suf — выбор 1–2 букв латиницей."""
    pfx = "pl" if kind == "pre" else "sl"
    ok = "pok" if kind == "pre" else "sok"
    pop = "pex" if kind == "pre" else "sex"
    letters = string.ascii_lowercase
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, 26, 7):
        chunk = letters[i : i + 7]
        rows.append(
            [
                InlineKeyboardButton(text=ch, callback_data=f"frag:f:{pfx}:{ch}")
                for ch in chunk
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="✓", callback_data=f"frag:f:{ok}"),
            InlineKeyboardButton(text="⌫", callback_data=f"frag:f:{pop}"),
            InlineKeyboardButton(
                text="✕", callback_data=f"frag:f:{'pz' if kind == 'pre' else 'sz'}"
            ),
            InlineKeyboardButton(text="«", callback_data="frag:f:pm"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _html_frag_filters_panel(uid: int) -> str:
    st = _frag_roll_state(uid)
    draft_p = "".join(st["roll_draft_pre"])
    draft_s = "".join(st["roll_draft_suf"])
    fl = _roll_filters_obj(uid)
    summ = filters_summary_ru(fl)
    dig_ru = {
        "any": "по умолчанию (без жёстких цифр)",
        "yes": "обязательна цифра",
        "no": "только буквы a–z",
    }.get(fl.digits, "любые")
    return (
        "<b>Параметры имени</b> <i>(PLUS)</i>\n\n"
        f"<b>Начало:</b> <code>{html.escape(fl.prefix or '—')}</code>\n"
        f"<b>Конец:</b> <code>{html.escape(fl.suffix or '—')}</code>\n"
        f"<b>Цифры:</b> {html.escape(dig_ru)}\n"
        f"<i>Сводка:</i> <code>{html.escape(summ)}</code>\n\n"
        f"<i>Черновик начала:</i> <code>{html.escape(draft_p or '—')}</code> · "
        f"<i>конца:</i> <code>{html.escape(draft_s or '—')}</code>\n\n"
        "<i>Сброс сбрасывает все поля. Без активных ограничений используются настройки по умолчанию.</i>"
    )


def _kb_frag_filters_panel(uid: int) -> InlineKeyboardMarkup:
    r = _frag_roll_state(uid)["roll_f"]
    dig = str(r.get("dig") or "any")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔤 Начало", callback_data="frag:f:pre"),
                InlineKeyboardButton(text="🔤 Конец", callback_data="frag:f:suf"),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 С цифрами" + (" ✓" if dig == "yes" else ""),
                    callback_data="frag:f:dg:yes",
                ),
                InlineKeyboardButton(
                    text="🪶 Без цифр" + (" ✓" if dig == "no" else ""),
                    callback_data="frag:f:dg:no",
                ),
            ],
            [
                InlineKeyboardButton(text="🔄 Сброс", callback_data="frag:f:rst"),
                InlineKeyboardButton(text="◀ К длине", callback_data="frag:f:bak"),
            ],
        ]
    )


def kb_fragment_lengths(*, uid: int, db: Database) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🔹 5", callback_data="frag:len:5"),
            InlineKeyboardButton(text="🔹 6", callback_data="frag:len:6"),
            InlineKeyboardButton(text="🔹 7", callback_data="frag:len:7"),
        ],
    ]
    if db.is_plus(uid):
        fl = _roll_filters_obj(uid)
        label = filters_summary_ru(fl)
        if len(label) > 28:
            label = label[:25] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🎛 Фильтры · {label}",
                    callback_data="frag:filters",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✕ Закрыть", callback_data="frag:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_fragment_main(
    *, uid: int | None = None, settings: Settings | None = None
) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [
            KeyboardButton(text=BTN_SEARCH_F),
            KeyboardButton(text=BTN_VALUATE_F),
        ],
        [
            KeyboardButton(text=BTN_CABINET_F),
            KeyboardButton(text=BTN_SUPPORT_F),
        ],
        [
            KeyboardButton(text=BTN_PLUS_F),
            KeyboardButton(text=BTN_LUCK_F),
        ],
        [
            KeyboardButton(text=BTN_REFERRAL_F),
        ],
        [
            KeyboardButton(text=BTN_DOCS_F),
        ],
    ]
    if (
        uid is not None
        and settings is not None
        and settings.admin_ids
        and uid in settings.admin_ids
    ):
        rows.append([KeyboardButton(text=BTN_ADMIN_F)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def kb_v2_main(
    *, uid: int | None = None, settings: Settings | None = None
) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_ROLL), KeyboardButton(text=BTN_VALUATE)],
        [KeyboardButton(text=BTN_TOP), KeyboardButton(text=BTN_CABINET)],
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_PLUS)],
        [KeyboardButton(text=BTN_DOCS)],
        [KeyboardButton(text=BTN_REFERRAL)],
        [KeyboardButton(text=BTN_LUCK)],
    ]
    if (
        uid is not None
        and settings is not None
        and settings.admin_ids
        and uid in settings.admin_ids
    ):
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def kb_v2_cabinet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍀 Тарифы «Удача»",
                    callback_data="luck:shop",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📂 Сохранённые ники", callback_data="cab:saved"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀ В меню", callback_data="cab:back"
                )
            ],
        ]
    )


def kb_v2_rarity() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 От $1 000",
                    callback_data="roll:tier:super"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🥇 От $500", callback_data="roll:tier:legendary"
                ),
                InlineKeyboardButton(
                    text="🥈 От $150", callback_data="roll:tier:mythic"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🥉 От $50", callback_data="roll:tier:epic"
                ),
                InlineKeyboardButton(
                    text="🔸 От $10", callback_data="roll:tier:rare"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✨ По умолчанию", callback_data="roll:tier:common"
                ),
                InlineKeyboardButton(
                    text="🌊 Все лоты", callback_data="roll:tier:any"
                ),
            ],
        ]
    )


def kb_v2_lengths() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔹 5 символов", callback_data="roll:len:5"),
                InlineKeyboardButton(text="🔹 6 символов", callback_data="roll:len:6"),
            ],
            [
                InlineKeyboardButton(
                    text="✕ Отмена",
                    callback_data="roll:cancel"
                )
            ],
        ]
    )


def _roll_pick_intro_html() -> str:
    return (
        f"✨ <b>{html.escape(AMNYAM)}</b> крутит для вас <code>@username</code>\n"
        f"{ROLL_RULE_LINE_HTML}\n\n"
        "🔤 <b>Сколько символов в @нике?</b>\n"
        "<i>Латиница <code>a–z</code> — жми кнопку ниже 👇</i>"
    )


def _roll_tier_pick_html() -> str:
    return (
        f"✨ <b>{html.escape(AMNYAM)}</b> крутит <code>@username</code>\n"
        f"{ROLL_RULE_LINE_HTML}\n\n"
        "🎛 <b>Настройки крутки</b>\n\n"
        "💵 <b>От какой оценки крутить?</b> <i>(USD)</i>\n"
        "<i>✨ По умолчанию — спокойный старт; 🌊 Все лоты — без нижнего порога.</i>"
    )


def _rarity_glyph(name: str) -> str:
    return {
        "Обычный": "◇",
        "Редкий": "◆",
        "Эпический": "✦",
        "Мифический": "✧",
        "Легендарный": "★",
        "Элитный": "✶",
    }.get(name, "◇")


def kb_roll_post_result(username: str, *, is_plus: bool) -> InlineKeyboardMarkup:
    """После крутки (режим telethon): сначала пауза 5→1, затем кнопка «ещё раз»."""
    ul = username.lower()
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔁 Крутить ещё раз", callback_data="roll:repeat:start")],
        [
            InlineKeyboardButton(
                text="📊 Оценить этот ник",
                callback_data=f"val:go:{ul}",
            )
        ],
    ]
    if is_plus:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_frag_roll_post_result(username: str, *, is_plus: bool) -> InlineKeyboardMarkup:
    """После крутки (fragment): без «Оценить» — этот callback живёт только в v2-роутере."""
    ul = username.lower()
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔁 Крутить ещё раз", callback_data="roll:repeat:start")],
    ]
    if is_plus:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_roll_repeat_ready(username: str, *, is_plus: bool) -> InlineKeyboardMarkup:
    """После отсчёта: реальный запуск крутки."""
    ul = username.lower()
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔁 Крутить ещё раз", callback_data="roll:repeat:go")],
        [
            InlineKeyboardButton(
                text="📊 Оценить этот ник",
                callback_data=f"val:go:{ul}",
            )
        ],
    ]
    if is_plus:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_frag_roll_repeat_ready(username: str, *, is_plus: bool) -> InlineKeyboardMarkup:
    ul = username.lower()
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔁 Крутить ещё раз", callback_data="roll:repeat:go")],
    ]
    if is_plus:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _repeat_snapshot_html(cb: CallbackQuery) -> str:
    m = cb.message
    raw = getattr(m, "html_text", None) or m.text or ""
    raw = (raw or "").strip()
    if not raw:
        return f"✨ <b>{html.escape(AMNYAM)}</b>\n<i>Предыдущий результат крутки.</i>"
    if len(raw) > 3500:
        return raw[:3500] + "\n\n<i>…</i>"
    return raw


async def _repeat_countdown_edits(cb: CallbackQuery, *, snapshot_html: str) -> None:
    bot = cb.bot
    chat_id = cb.message.chat.id
    msg_id = cb.message.message_id
    for i, sec in enumerate(range(ROLL_REPEAT_COOLDOWN_S, 0, -1)):
        star = ROLL_REPEAT_STAR_FRAMES[i % len(ROLL_REPEAT_STAR_FRAMES)]
        body = (
            f"{snapshot_html}\n\n"
            f"{ROLL_RULE_LINE_HTML}\n\n"
            f"{star} <b>Подождите {sec}</b>"
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=body,
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def _handle_roll_repeat_start(
    cb: CallbackQuery,
    db: Database,
    settings: Settings,
    checker: Any,
    *,
    mode: str,
) -> None:
    assert cb.from_user and cb.message
    uid = cb.from_user.id
    ud = _sess[uid]
    if ud.get("roll_repeat_busy"):
        await cb.answer("Подождите, идёт отсчёт…", show_alert=True)
        return
    if mode == "fragment":
        if ud.get("frag_roll_last_len") is None:
            await cb.answer("Сначала сделайте подбор через «Крутить».", show_alert=True)
            return
        uname = str(ud.get("frag_roll_last_username") or "").strip().lower()
    else:
        if ud.get("roll_last_len") is None or ud.get("roll_last_tier") is None:
            await cb.answer("Сначала сделайте подбор через «Крутить».", show_alert=True)
            return
        uname = str(ud.get("roll_last_username") or "").strip().lower()
    if not uname:
        await cb.answer("Сначала сделайте подбор заново.", show_alert=True)
        return
    await cb.answer()
    ud["roll_repeat_busy"] = True
    try:
        snap = _repeat_snapshot_html(cb)
        await _repeat_countdown_edits(cb, snapshot_html=snap)
        is_plus = db.is_plus(uid)
        kb = (
            kb_frag_roll_repeat_ready(uname, is_plus=is_plus)
            if mode == "fragment"
            else kb_roll_repeat_ready(uname, is_plus=is_plus)
        )
        await cb.message.edit_text(
            "✨ <b>Можно крутить снова!</b>\n\n<i>Нажмите кнопку ниже.</i>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        log.exception("roll repeat countdown")
    finally:
        ud.pop("roll_repeat_busy", None)


async def _handle_roll_repeat_go_v2(
    cb: CallbackQuery, db: Database, settings: Settings, checker: Any
) -> None:
    assert cb.from_user
    uid = cb.from_user.id
    ud = _sess[uid]
    ln, tk = ud.get("roll_last_len"), ud.get("roll_last_tier")
    if ln is None or tk is None:
        await cb.answer("Сначала сделайте подбор через «Крутить».", show_alert=True)
        return
    await cb.answer()
    await perform_roll_v2(
        cb,
        uid=uid,
        length=int(ln),
        tier_key=str(tk),
        db=db,
        settings=settings,
        checker=checker,
    )


async def _handle_roll_repeat_go_fragment(
    cb: CallbackQuery, db: Database, settings: Settings, checker: Any
) -> None:
    assert cb.from_user
    uid = cb.from_user.id
    ud = _sess[uid]
    ln = ud.get("frag_roll_last_len")
    if ln is None:
        await cb.answer("Сначала сделайте подбор через «Крутить».", show_alert=True)
        return
    await cb.answer()
    await _frag_run_roll_at_length(cb, uid, int(ln), db, settings, checker)


async def _frag_run_roll_at_length(
    cb: CallbackQuery,
    uid: int,
    length: int,
    db: Database,
    settings: Settings,
    checker: Any,
) -> None:
    """Один цикл fragment-подбора по выбранной длине (из frag:len или roll:repeat:go)."""
    if db.is_search_globally_blocked() and uid not in settings.admin_ids:
        await cb.message.edit_text(
            "<b>Подбор на паузе</b>\n\n"
            "Поиск имён временно отключён администратором.",
            parse_mode="HTML",
        )
        return
    if not db.can_search(uid, settings.free_search_limit):
        await cb.message.edit_text(
            "<b>Лимит исчерпан</b>\n\n"
            "Бесплатные попытки закончились. Оформите <b>Подписка PLUS</b> для безлимита.",
            parse_mode="HTML",
        )
        return

    is_plus = db.is_plus(uid)
    max_attempts = 420 if is_plus else 140
    if getattr(checker, "uses_telethon", False):
        max_attempts = 900 if is_plus else 320
    lucky_spin = db.is_luck_roll_active(uid)
    flt = _roll_filters_obj(uid)
    if is_plus and flt.active():
        max_attempts = min(1400, int(max_attempts * 1.45))

    await cb.message.edit_text(
        _ui_frag_search_frame(0),
        parse_mode="HTML",
    )

    chat_id = cb.message.chat.id
    msg_id = cb.message.message_id
    bot = cb.bot

    try:
        found_name, attempts, timed_out = await _find_one_username_fragment(
            bot=bot,
            chat_id=chat_id,
            message_id=msg_id,
            length=length,
            max_attempts=max_attempts,
            delay_s=settings.fragment_request_delay_s,
            lucky=lucky_spin,
            checker=checker,
            filters=flt,
            fragment_timeout_s=settings.fragment_roll_timeout_s,
            is_plus=is_plus,
        )
    except Exception:
        log.exception("поиск ника fragment-режим")
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=_ui_plain_error(),
            parse_mode="HTML",
        )
        await bot.send_message(
            uid,
            "<b>Ошибка сервиса.</b> Нажмите <code>/start</code> или выберите действие внизу.",
            reply_markup=kb_fragment_main(uid=uid, settings=settings),
            parse_mode="HTML",
        )
        return

    if not timed_out:
        db.increment_search(uid)

    if not found_name:
        fail_text = _ui_frag_timeout_fail() if timed_out else _ui_frag_fail()
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=fail_text,
            parse_mode="HTML",
        )
        tail_hint = (
            "<b>Совет:</b> измените длину или фильтры (PLUS) и попробуйте снова."
            if timed_out
            else "<b>Можно повторить</b> — кнопки внизу экрана."
        )
        await bot.send_message(
            uid,
            tail_hint,
            reply_markup=kb_fragment_main(uid=uid, settings=settings),
            parse_mode="HTML",
        )
        return

    log.debug("fragment pick найден за %s шагов", attempts)
    rarity_name: str | None = None
    predicted_price: float | None = None
    why_r: str | None = None
    try:
        ri, predicted_price, why_r = _rarity_for_display(
            found_name, db, settings.ton_to_usd
        )
        rarity_name = ri.name
        db.add_roll_event(
            user_id=uid,
            username=found_name.lower(),
            rarity=rarity_name,
            predicted_price_usd=predicted_price,
        )
    except Exception:
        log.exception("rarity/roll_event for fragment pick")

    ud = _sess[uid]
    ud["frag_roll_last_len"] = length
    ud["frag_roll_last_username"] = found_name.lower()

    tail = (
        ""
        if is_plus
        else "\n\n<i>Сохранить в один тап — с подпиской PLUS.</i>"
    )
    if flt.active():
        tail += (
            "\n\n<i>🎛 Фильтры PLUS: "
            f"{html.escape(filters_summary_ru(flt))}</i>"
        )

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=msg_id,
        text=_ui_frag_found(
            found_name,
            rarity_name=rarity_name,
            predicted_price=predicted_price,
            why=why_r,
            luck_used=bool(lucky_spin and not flt.active()),
        )
        + tail,
        parse_mode="HTML",
        reply_markup=kb_frag_roll_post_result(found_name, is_plus=is_plus),
    )


def kb_valuation_post_single(username: str, *, is_plus: bool) -> InlineKeyboardMarkup:
    ul = username.lower()
    rows: list[list[InlineKeyboardButton]] = []
    if is_plus:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔮 Похожие никнеймы",
                callback_data=f"val:sim:{ul}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀ К подбору",
                callback_data="val:back",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _luck_cabinet_line(u: Any, db: Database, uid: int) -> str:
    if not db.is_luck(uid):
        return "🍀 <b>Режим «Удача»:</b> выключен"
    if int(getattr(u, "luck_forever", 0) or 0):
        core = "🍀 <b>Режим «Удача»:</b> включён <b>навсегда</b>"
    else:
        raw = getattr(u, "luck_expires_at", None)
        if raw:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                try:
                    loc = dt.astimezone(ZoneInfo("Europe/Moscow"))
                    when = loc.strftime("%d.%m.%Y %H:%M МСК")
                except Exception:
                    when = dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                core = f"🍀 <b>Режим «Удача»:</b> до <code>{when}</code>"
            except ValueError:
                core = "🍀 <b>Режим «Удача»:</b> включён"
        else:
            core = "🍀 <b>Режим «Удача»:</b> включён <i>(без даты окончания)</i>"
    if int(getattr(u, "is_plus", 0)) and int(getattr(u, "luck_roll_paused", 0)):
        core += (
            "\n └ <i>В подборе: <b>на паузе</b> — в меню «Удача» нажмите "
            "<b>«Снова в подборе»</b>.</i>"
        )
    return core


def _kb_luck_need_plus() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Подписка PLUS",
                    callback_data="plus:shop",
                )
            ]
        ]
    )


def _luck_menu_inline_kb(uid: int, db: Database, settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    u = db.get_or_create_user(uid)
    if db.is_plus(uid) and db.is_luck(uid):
        paused = int(getattr(u, "luck_roll_paused", 0))
        rows.append(
            [
                InlineKeyboardButton(
                    text="▶ Снова в подборе" if paused else "⏸ Пауза в подборе",
                    callback_data="luck:toggle",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="💳 Тарифы «Удача»",
                callback_data="luck:shop",
            )
        ]
    )
    if luck_promo_entry_available(settings, db):
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗝 Промокод",
                    callback_data="luck:enter",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _luck_menu_html(uid: int, db: Database, roll_btn: str) -> str:
    u = db.get_or_create_user(uid)
    luck_status = _luck_cabinet_line(u, db, uid)
    return (
        "<b>🍀 Режим «Удача»</b>\n\n"
        f"{luck_status}\n\n"
        "В подборе из каталога чаще всплывают симметричные и удачные сочетания "
        f"при той же цене — учитывается в «{html.escape(roll_btn)}».\n\n"
        "<i>При <b>PLUS</b> можно временно отключить учёт «Удачи» в подборе, не теряя срок тарифа.</i>\n\n"
        "<i>Срок — по тарифу или промокоду. Оплата тарифов — при активной подписке PLUS.</i>"
    )


def _format_username(username: str) -> str:
    return f"@{username.lower()}"


def _rarity_metrics_html(
    *,
    rarity_name: str,
    predicted_price: float | None,
    why: str,
    show_explanation: bool = True,
) -> str:
    """Блок «редкость + ориентир цены + пояснение» без заголовка крутки."""
    usd_txt = "нет данных" if predicted_price is None else f"${predicted_price:,.0f}"
    g = _rarity_glyph(rarity_name)
    core = (
        f"{g} <b>Редкость:</b> <b>{html.escape(rarity_name)}</b>\n"
        f"💵 <b>Ориентир цены:</b> <b>{usd_txt}</b>"
    )
    if not show_explanation or not (why or "").strip():
        return core
    return core + "\n\n" + f"<i>{html.escape(why)}</i>"


def _roll_result_card_html(
    *,
    uname: str,
    predicted_price: float | None,
    rarity_name: str,
    why: str,
    has_luck: bool,
) -> str:
    tail = (
        "\n\n<i>🍀 Учтён режим «Удача»: приоритет удачных комбинаций.</i>"
        if has_luck
        else ""
    )
    return (
        "<b>Вам выпало</b>\n"
        f"{_format_username(uname)}\n"
        "<code>──────────</code>\n\n"
        + _rarity_metrics_html(
            rarity_name=rarity_name,
            predicted_price=predicted_price,
            why=why,
            show_explanation=False,
        )
        + tail
    )


def kb_save(username: str) -> InlineKeyboardMarkup:
    ul = username.lower()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💾 Сохранить @{ul}",
                    callback_data=f"save:{ul}",
                )
            ]
        ]
    )


def kb_appraisal_saves_many(usernames: list[str]) -> InlineKeyboardMarkup | None:
    """Несколько кнопок «Сохранить» для пачки оценок (одна таблица saved_usernames)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in usernames:
        if not is_valid_telegram_username(raw):
            continue
        u = normalize_username(raw)
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)
        if len(ordered) >= 8:
            break
    if not ordered:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for u in ordered:
        row.append(
            InlineKeyboardButton(
                text=f"💾 @{u}",
                callback_data=f"save:{u}",
            )
        )
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def html_saved_usernames_panel(names: list[str]) -> str:
    n = len(names)
    head = f"<b>Сохранённые @ники</b> <code>({n}/{SAVED_USERNAMES_LIMIT})</code>"
    body = "\n".join(f"• <code>@{html.escape(x)}</code>" for x in names)
    tail = "\n\n<i>Нажмите 🗑 рядом с ником, чтобы удалить из списка.</i>"
    return f"{head}\n\n{body}{tail}"


def kb_saved_list_manage(names: list[str]) -> InlineKeyboardMarkup | None:
    if not names:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for x in names:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 @{x}",
                    callback_data=f"saved_del:{x}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_usernames_from_user_input(text: str) -> list[str]:
    """Делит ввод вида «crypto, @wolf; ninja» на отдельные @ники."""
    raw = text.strip()
    if not raw:
        return []
    parts = re.split(r"[\s,;|/]+", raw)
    out: list[str] = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        u = normalize_username(p)
        if u and u not in out:
            out.append(u)
    return out[:12]


def _valuation_block_html(
    report: UsernameValuation,
    *,
    fragment_line: str | None,
    telegram_status: str | None,
    title_line: str | None = None,
) -> str:
    usd_txt = (
        "нет данных"
        if report.estimated_price_usd is None
        else f"${report.estimated_price_usd:,.0f}"
    )
    pros_txt = "\n".join(f"• {html.escape(p)}" for p in report.pros) or "• —"
    cons_txt = "\n".join(f"• {html.escape(c)}" for c in report.cons) or "• —"
    stars = "⭐" * report.stars_5 + "☆" * (5 - report.stars_5)
    lines: list[str] = []
    if title_line:
        lines += [title_line, "<code>──────────</code>", ""]
    lines.append(f"<code>@{html.escape(report.username)}</code>")
    if telegram_status:
        lines.append(telegram_status)
    if fragment_line:
        lines.append(fragment_line)
    lines += [
        "",
        f"<b>Ориентировочная стоимость:</b> <b>{usd_txt}</b>",
    ]
    if report.length_market_band:
        lines.append(report.length_market_band)
    lines += [
        f"<b>Ранг:</b> <b>{report.rank_10}/10</b>",
        f"<b>Потенциал:</b> {stars} (<b>{report.stars_5}/5</b>)",
        f"<b>Метка редкости:</b> <b>{html.escape(report.rarity_name)}</b>",
        f"<b>Сделок в локальной базе по этому @нику:</b> <b>{report.exact_sales_count}</b>",
        f"<b>Как посчитали:</b> {html.escape(report.market_note)}",
        "",
        "<b>Плюсы:</b>",
        pros_txt,
        "",
        "<b>Минусы:</b>",
        cons_txt,
    ]
    return "\n".join(lines)


async def perform_appraisals_batch(
    message: Message,
    *,
    uid: int,
    raw: str,
    db: Database,
    settings: Settings,
    checker: Any,
) -> None:
    tokens = parse_usernames_from_user_input(raw)
    if not tokens:
        await message.answer(
            "<b>Не нашёл ники.</b> Пример: <code>crypto, @wolf, ninja</code>",
            parse_mode="HTML",
        )
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    listed_map: dict[str, bool | None] = {}
    if settings.bot_mode == "fragment" and len(tokens) <= 3:
        for t in tokens:
            if not is_valid_telegram_username(t):
                listed_map[t] = None
                continue
            try:
                listed_map[t] = await asyncio.to_thread(
                    username_listed_on_fragment,
                    t,
                    timeout_s=22,
                )
            except Exception:
                log.exception("fragment listing check %s", t)
                listed_map[t] = None

    telegram_map: dict[str, str] = {}
    if (
        settings.username_check_mode != "disabled"
        and len(tokens) == 1
        and getattr(checker, "uses_telethon", False)
    ):
        t0 = tokens[0]
        if is_valid_telegram_username(t0):
            try:
                av = await checker.is_available(t0)
                if av is True:
                    telegram_map[t0] = (
                        "<b>Telegram:</b> по ответу API ник <b>свободен</b> для смены."
                    )
                elif av is False:
                    telegram_map[t0] = "<b>Telegram:</b> ник уже <b>занят</b>."
                else:
                    telegram_map[t0] = "<b>Telegram:</b> статус не получен."
            except Exception:
                log.exception("telegram check %s", t0)
                telegram_map[t0] = "<b>Telegram:</b> проверка недоступна (ошибка)."

    header = (
        f"<b>Оценка никнеймов</b> · <b>{len(tokens)}</b>\n\n"
    )
    if len(tokens) > 3 and settings.bot_mode == "fragment":
        header += (
            "<i>Проверка «виден ли ник как лот на сайте Fragment» делается только "
            "для 1–3 ников за раз. Сейчас — оценка по базе и эвристика.</i>\n\n"
        )

    single_one = len(tokens) == 1 and is_valid_telegram_username(tokens[0])

    blocks: list[str] = []
    for t in tokens:
        if not is_valid_telegram_username(t):
            blocks.append(
                f"<b>@{html.escape(t)}</b>\n"
                "<i>Неверный формат: латиница <code>a–z</code>, цифры, <code>_</code>, "
                "длина 5–32.</i>"
            )
            continue
        rep = evaluate_username_market(t, db, ton_to_usd=settings.ton_to_usd)
        frag_line = None
        if t in listed_map and listed_map[t] is not None:
            frag_line = (
                "<b>Fragment:</b> на витрине виден как <b>лот</b>."
                if listed_map[t]
                else "<b>Fragment:</b> активного лота не видно. "
                "<i>При сомнении проверьте ник в приложении.</i>"
            )
        tg_line = telegram_map.get(t)
        title = (
            f"<b>Оценка</b> {_format_username(rep.username)}"
            if single_one
            else None
        )
        blocks.append(
            _valuation_block_html(
                rep,
                fragment_line=frag_line,
                telegram_status=tg_line,
                title_line=title,
            )
        )

    kb_main = (
        kb_fragment_main(uid=uid, settings=settings)
        if settings.bot_mode == "fragment"
        else kb_v2_main(uid=uid, settings=settings)
    )
    kb_inline: InlineKeyboardMarkup | None = None
    if single_one:
        t0 = tokens[0]
        if settings.bot_mode == "telethon":
            kb_inline = kb_valuation_post_single(t0, is_plus=db.is_plus(uid))
        elif db.is_plus(uid):
            kb_inline = kb_save(t0)
    elif db.is_plus(uid):
        kb_inline = kb_appraisal_saves_many(tokens)

    markup_reply: InlineKeyboardMarkup | ReplyKeyboardMarkup = (
        kb_inline if kb_inline is not None else kb_main
    )

    body = header + "\n\n".join(blocks)
    max_len = 3800
    if len(body) <= max_len:
        await message.answer(body, reply_markup=markup_reply, parse_mode="HTML")
        return

    for i, b in enumerate(blocks):
        h = header if i == 0 else "<b>📊 Оценка (продолжение)</b>\n\n"
        await message.answer(
            h + b,
            parse_mode="HTML",
            reply_markup=markup_reply if i == len(blocks) - 1 else None,
        )


async def cabinet_text_frag(db: Database, uid: int, settings: Settings) -> str:
    u = db.get_or_create_user(uid)
    rem = db.searches_remaining(uid, settings.free_search_limit)
    if u.is_plus:
        if u.plus_expires_at:
            try:
                dt = datetime.fromisoformat(
                    str(u.plus_expires_at).replace("Z", "+00:00")
                )
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                try:
                    loc = dt.astimezone(ZoneInfo("Europe/Moscow"))
                    when = loc.strftime("%d.%m.%Y %H:%M МСК")
                except Exception:
                    when = dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                tier = "♠ <b>Подписка PLUS</b> до " f"<code>{when}</code>"
            except ValueError:
                tier = "♠ <b>Подписка PLUS</b> активна"
        else:
            tier = "♠ <b>Подписка PLUS</b> без срока окончания"
    else:
        tier = "👤 <b>Гость</b> · лимит бесплатных подборов"
    luck_ln = _luck_cabinet_line(u, db, uid)
    ref_n = db.referral_count(uid)
    ref_line = (
        f"🤝 <b>Рефералы:</b> <code>{ref_n}</code> · "
        f"+{settings.referral_plus_hours} ч PLUS за каждого нового по вашей ссылке "
        f"<i>(кнопка «{html.escape(BTN_REFERRAL_F if settings.bot_mode == 'fragment' else BTN_REFERRAL)}»)</i>"
    )
    if rem is None:
        tries = "<b>Подбор имён:</b> без лимита"
    else:
        tries = (
            f"<b>Бесплатных подборов:</b> <code>{rem}</code> из "
            f"<code>{settings.free_search_limit}</code>"
        )
    return (
        f"<b>Личный кабинет</b> · {html.escape(AMNYAM)}\n\n"
        f"{tier}\n{luck_ln}\n{ref_line}\n{tries}\n\n"
        "<i>Список сохранённых @username — в кнопке ниже (нужна подписка PLUS).</i>"
    )


async def cabinet_text_v2(db: Database, uid: int, settings: Settings) -> str:
    return await cabinet_text_frag(db, uid, settings)  # тот же формат


async def decrement_search_ok(db: Database, uid: int, settings: Settings) -> bool:
    if db.is_search_globally_blocked() and uid not in settings.admin_ids:
        return False
    if not db.can_search(uid, settings.free_search_limit):
        return False
    db.increment_search(uid)
    return True


def _letters_ok(username: str, length: int) -> bool:
    return bool(re.fullmatch(r"[a-z]{%d}" % length, username))


def _rarity_for_display(username: str, db: Database, ton_to_usd: float):
    return combined_rarity(username, db, ton_to_usd=ton_to_usd)


def build_checker(settings: Settings) -> UsernameChecker | DisabledUsernameChecker:
    if settings.username_check_mode == "disabled":
        return DisabledUsernameChecker()
    if not settings.api_id or not settings.api_hash:
        return DisabledUsernameChecker()
    return UsernameChecker(
        settings.api_id,
        settings.api_hash,
        settings.telethon_session,
        delay_between_checks=settings.telethon_check_delay_s,
        timeout=settings.telethon_timeout,
        connection_retries=settings.telethon_connection_retries,
        connection=telethon_connection_class(settings.telethon_connection),
    )


def create_bot(settings: Settings) -> Bot:
    return Bot(settings.bot_token)


# ---------- Middleware ----------
class DependenciesMiddleware(BaseMiddleware):
    def __init__(self, *, db: Database, settings: Settings, checker: Any, bot: Bot) -> None:
        self.db = db
        self.settings = settings
        self.checker = checker
        self.bot = bot

    async def __call__(
        self,
        handler,
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self.db
        data["settings"] = self.settings
        data["checker"] = self.checker
        data["bot"] = self.bot
        return await handler(event, data)


async def _find_one_username_fragment(
    *,
    bot: Bot,
    chat_id: int,
    message_id: int,
    length: int,
    max_attempts: int,
    delay_s: float,
    lucky: bool = False,
    checker: Any,
    filters: RollFilters,
    fragment_timeout_s: int,
    is_plus: bool,
) -> tuple[str | None, int, bool]:
    """Подбирает один ник. Третий элемент — True, если сработал лимит по времени (3 мин)."""
    import time

    lucky_effective = bool(lucky) and not filters.active()
    retry_sleep = min(delay_s, 0.06) if delay_s > 0 else 0.0

    seen: set[str] = set()
    attempts = 0
    last_edit = 0.0
    deadline = time.monotonic() + float(FRAGMENT_USERNAME_SEARCH_WALL_S)
    while attempts < max_attempts:
        if time.monotonic() >= deadline:
            return None, attempts, True

        cand = generate_roll_candidate(
            length,
            lucky=lucky_effective,
            filters=filters,
            plus_full_cv=is_plus,
        )
        if cand in seen:
            continue
        seen.add(cand)
        attempts += 1

        now = time.monotonic()
        if attempts == 1 or now - last_edit >= 0.1:
            last_edit = now
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=_ui_frag_search_frame(attempts),
                    parse_mode="HTML",
                )
            except Exception:
                pass

        try:
            listed = await asyncio.to_thread(
                username_listed_on_fragment,
                cand,
                timeout_s=fragment_timeout_s,
            )
        except Exception:
            log.exception("проверка кандидата %s", cand)
            listed = True

        if not listed:
            if getattr(checker, "uses_telethon", False):
                try:
                    avail = await checker.is_available(cand)
                except Exception:
                    log.exception("telethon check %s", cand)
                    avail = False
                if avail is not True:
                    if retry_sleep > 0:
                        await asyncio.sleep(retry_sleep)
                    continue
            return cand, attempts, False

        if retry_sleep > 0:
            await asyncio.sleep(retry_sleep)

    return None, attempts, False


# ---------- Обработчики: Fragment режим ----------


async def cmd_start_frag(message: Message, db: Database, settings: Settings) -> None:
    assert message.from_user
    uid = message.from_user.id
    db.get_or_create_user(uid)
    _sess[uid].pop("await_valuation", None)
    roll_tg = ""
    if settings.api_id and settings.username_check_mode != "disabled":
        roll_tg = (
            "\n<i>При настроенном Telegram API дополнительно проверяется, "
            "свободен ли @ник для смены в приложении.</i>"
        )
    intro = (
        f"<b>{html.escape(AMNYAM)}</b>\n"
        "<b>Подбор и оценка username</b>\n\n"
        f"<b>{html.escape(BTN_SEARCH_F)}</b> — свободное имя <code>a–z</code>, длина 5–7."
        f"{roll_tg}\n"
        "<b>Подписка PLUS</b> — фильтры к имени, быстрее подбор, сохранение в список.\n"
        f"<b>{html.escape(BTN_VALUATE_F)}</b> — отчёт по рынку, до нескольких ников в одном сообщении, "
        "<b>без списания попыток</b>.\n"
        f"<b>{html.escape(BTN_LUCK_F)}</b> — по промокоду улучшает шанс «удачного» варианта.\n\n"
        f"Бесплатных подборов: <code>{settings.free_search_limit}</code>.\n\n"
        "<i>Памятка:</i> <code>/советы</code> · <code>/шпаргалка</code> · <code>/команды</code>"
    )
    extra_admin = ""
    if uid in settings.admin_ids:
        extra_admin = "\n\n<i>Админ:</i> кнопка <b>⚙️ Админ</b> внизу или <code>/admin</code>."
    await message.answer(
        intro + "\n\n<i>Дальше — кнопки внизу экрана.</i>" + extra_admin,
        reply_markup=kb_fragment_main(uid=uid, settings=settings),
        parse_mode="HTML",
    )


async def on_text_frag(message: Message, db: Database, settings: Settings, checker: Any) -> None:
    assert message.text and message.from_user
    uid = message.from_user.id
    text = message.text.strip()

    if await admin_try_handle_text(
        message,
        sess_uid=_sess[uid],
        db=db,
        settings=settings,
        kb_main=kb_fragment_main(uid=uid, settings=settings),
    ):
        return

    if text == BTN_ADMIN_F:
        if uid not in settings.admin_ids:
            await message.answer(
                "<b>Нет доступа.</b> Клавиша только для администратора.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        await cmd_admin(message, db=db, settings=settings)
        return

    if PENDING_LUCK_PROMO.pop(uid, None):
        if not luck_promo_entry_available(settings, db):
            await message.answer(
                "<b>Режим «Удача»</b>: активных промокодов нет. Напишите в поддержку.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        ok, reason = redeem_luck_code(text, uid, db=db, settings=settings)
        if ok:
            rem_txt = ""
            if (
                settings.luck_promo_code
                and text.strip().upper() == settings.luck_promo_code
                and settings.luck_promo_max_uses > 0
            ):
                used = db.luck_promo_uses_count(settings.luck_promo_code)
                rem = max(0, settings.luck_promo_max_uses - used)
                rem_txt = f"\n\n<i>Осталось активаций этого кода: <b>{rem}</b>.</i>"
            await message.answer(
                "<b>🍀 Удача активирована.</b>\n\n"
                "Режим «Удача» учтётся при следующем подборе имени."
                + rem_txt,
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        else:
            extra = ""
            if reason == "limit_env":
                extra = " (лимит активаций исчерпан)"
            elif reason == "already":
                extra = " (вы уже активировали этот код)"
            elif reason == "limit":
                extra = " (лимит активаций исчерпан)"
            await message.answer(
                f"<b>Отклонено.</b> Код не принят{extra}.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        return

    if PENDING_PROMO.pop(uid, None):
        ok, reason, plus_days = redeem_plus_code(text, uid, db=db, settings=settings)
        if ok:
            skip_timed_note = ""
            if plus_days is None:
                db.set_plus_forever_paid(uid)
            else:
                urow = db.get_or_create_user(uid)
                if int(urow.is_plus) and not urow.plus_expires_at:
                    skip_timed_note = (
                        "\n\n<i>У вас уже PLUS без даты окончания — срок по этому коду не меняли.</i>"
                    )
                else:
                    db.extend_plus_days(uid, plus_days)
            period_line = _plus_promo_period_user_ru(plus_days) + skip_timed_note
            await message.answer(
                "<b>Подписка PLUS активирована.</b>\n\n"
                f"{period_line}\n\n"
                f"<b>{html.escape(AMNYAM)}</b>: безлимит подборов и сохранение понравившихся @ников.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        else:
            extra = ""
            if reason == "already":
                extra = " (этот код вы уже использовали)"
            elif reason == "limit":
                extra = " (лимит исчерпан)"
            await message.answer(
                "<b>Отклонено</b> — код не принят"
                f"{extra}. Проверь ввод или возьми актуальный у администратора.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        return

    first = text.lower().split(maxsplit=1)[0]
    if first in _TEXT_START:
        _sess[uid].pop("await_valuation", None)
        await cmd_start_frag(message, db, settings)
        return

    _reserved_frag = frozenset(
        {
            BTN_SEARCH_F,
            BTN_VALUATE_F,
            BTN_CABINET_F,
            BTN_SUPPORT_F,
            BTN_PLUS_F,
            BTN_LUCK_F,
            BTN_DOCS_F,
            BTN_REFERRAL_F,
            BTN_ADMIN_F,
        }
    )
    if _sess[uid].get("await_valuation") and text not in _reserved_frag:
        _sess[uid]["await_valuation"] = False
        await perform_appraisals_batch(
            message,
            uid=uid,
            raw=text,
            db=db,
            settings=settings,
            checker=checker,
        )
        return

    if text == BTN_SEARCH_F:
        if db.is_search_globally_blocked() and uid not in settings.admin_ids:
            await message.answer(
                "<b>Стол на паузе.</b>\n\n"
                "Поиск никнеймов временно закрыт администратором для всех гостей. "
                "Оценка ников и остальное меню работают.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        if not db.can_search(uid, settings.free_search_limit):
            await message.answer(
                "<b>Нет попыток.</b>\n"
                "Бесплатные крутки закончились. Оформи <b>Подписка PLUS</b>.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        await message.answer(
            _ui_frag_pick_length(),
            reply_markup=kb_fragment_lengths(uid=uid, db=db),
            parse_mode="HTML",
        )
        return

    if text == BTN_VALUATE_F:
        _sess[uid]["await_valuation"] = True
        await message.answer(
            "<b>Оценка @ника</b>\n\n"
            "Пришли одним сообщением один или несколько ников, например:\n"
            "<code>crypto, @wolf, ninja</code>\n\n"
            "<i>Оценка не расходует бесплатные подборы.</i>",
            reply_markup=kb_fragment_main(uid=uid, settings=settings),
            parse_mode="HTML",
        )
        return

    if text == BTN_CABINET_F:
        await message.answer(
            await cabinet_text_frag(db, uid, settings),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📂 Сохранённые ники", callback_data="cab:saved"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="◀ В меню", callback_data="cab:back_frag"
                        )
                    ],
                ]
            ),
            parse_mode="HTML",
        )
        return

    if text == BTN_REFERRAL_F:
        await answer_referral_program(message, db, settings)
        return

    if text == BTN_SUPPORT_F:
        await message.answer(
            f"<b>Поддержка</b> · {html.escape(AMNYAM)}\n\n"
            "Если заметили проблему или у вас появился вопрос — напишите в нашу поддержку @amnyam_supportt.",
            reply_markup=kb_fragment_main(uid=uid, settings=settings),
            parse_mode="HTML",
        )
        return

    if text == BTN_DOCS_F:
        await message.answer(
            legal_documents_user_html(db),
            reply_markup=kb_fragment_main(uid=uid, settings=settings),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    if text == BTN_PLUS_F:
        await message.answer(
            plus_shop_intro_html(),
            reply_markup=kb_plus_tariffs(),
            parse_mode="HTML",
        )
        return

    if text == BTN_LUCK_F:
        await message.answer(
            _luck_menu_html(uid, db, BTN_SEARCH_F),
            reply_markup=_luck_menu_inline_kb(uid, db, settings),
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"<b>{html.escape(AMNYAM)}</b>: выберите действие кнопками внизу или "
        "<code>/start</code> / <code>старт</code>.",
        reply_markup=kb_fragment_main(uid=uid, settings=settings),
        parse_mode="HTML",
    )


async def on_callback_frag(
    cb: CallbackQuery, db: Database, settings: Settings, checker: Any
) -> None:
    assert cb.data and cb.from_user and cb.message
    uid = cb.from_user.id
    data = cb.data
    if data.startswith("adm:"):
        if uid not in settings.admin_ids:
            await cb.answer("Нет доступа", show_alert=True)
            return
        await cb.answer()
        await admin_handle_callback(cb, sess_uid=_sess[uid], db=db, settings=settings)
        return

    if data == "luck:toggle":
        u = db.get_or_create_user(uid)
        if not db.is_plus(uid) or not db.is_luck(uid):
            await cb.answer("Нужны PLUS и активная «Удача».", show_alert=True)
            return
        paused = int(getattr(u, "luck_roll_paused", 0))
        db.set_luck_roll_paused(uid, paused=not paused)
        now_paused = int(getattr(db.get_or_create_user(uid), "luck_roll_paused", 0))
        toast = (
            "В подборе без учёта «Удачи», срок тарифа не сгорает."
            if now_paused
            else "«Удача» снова учитывается в подборе."
        )
        await cb.answer(toast[:200])
        await cb.message.edit_text(
            _luck_menu_html(uid, db, BTN_SEARCH_F),
            parse_mode="HTML",
            reply_markup=_luck_menu_inline_kb(uid, db, settings),
        )
        return

    if data in ("roll:repeat:start", "roll:repeat"):
        await _handle_roll_repeat_start(cb, db, settings, checker, mode="fragment")
        return
    if data == "roll:repeat:go":
        await _handle_roll_repeat_go_fragment(cb, db, settings, checker)
        return

    await cb.answer()

    if data.startswith("saved_del:"):
        if not db.is_plus(uid):
            return
        nick = data.removeprefix("saved_del:").strip()
        uname = normalize_username(nick)
        if not is_valid_telegram_username(uname):
            await cb.message.answer("<b>Некорректный ник.</b>", parse_mode="HTML")
            return
        db.remove_saved(uid, uname)
        names = db.list_saved(uid)
        if not names:
            await cb.message.edit_text(
                "<b>Сохранённые @ники</b>\n\n"
                "<i>Список пуст. Можно сохранить новые из крутки или оценки "
                f"(до <b>{SAVED_USERNAMES_LIMIT}</b> шт.).</i>",
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await cb.message.edit_text(
                html_saved_usernames_panel(names),
                parse_mode="HTML",
                reply_markup=kb_saved_list_manage(names),
            )
        await cb.message.answer("🗑 Ник удалён из сохранённых.", parse_mode="HTML")
        return

    if data.startswith("plus:tariff:"):
        key = data.split(":", 2)[2]
        t = plus_tariff_by_key(key)
        if not t:
            await cb.message.edit_text(
                "<b>Тариф не найден.</b>",
                parse_mode="HTML",
            )
            return
        await cb.message.edit_text(
            plus_tariff_payment_html(t, payment_hint=settings.plus_payment_hint),
            parse_mode="HTML",
            reply_markup=kb_plus_payment_nav(),
        )
        return

    if data == "plus:shop":
        await cb.message.edit_text(
            plus_shop_intro_html(),
            parse_mode="HTML",
            reply_markup=kb_plus_tariffs(),
        )
        return

    if data == "plus:enter":
        PENDING_PROMO[uid] = True
        await cb.message.edit_text(
            "<b>Промокод PLUS</b>\n\n"
            "Отправьте код <b>одним сообщением</b>.\n"
            "<code>/cancel</code> — отмена.",
            parse_mode="HTML",
        )
        return

    if data == "luck:enter":
        if not luck_promo_entry_available(settings, db):
            await cb.message.edit_text(
                "<b>Режим «Удача»</b>: активных промокодов нет. Напишите в поддержку.",
                parse_mode="HTML",
            )
            return
        PENDING_LUCK_PROMO[uid] = True
        await cb.message.edit_text(
            "<b>Промокод «Удача»</b>\n\n"
            "Отправьте код <b>одним сообщением</b>.\n"
            "<code>/cancel</code> — отмена.",
            parse_mode="HTML",
        )
        return

    if data == "luck:shop":
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Тарифы «Удача»</b> доступны с активной подпиской <b>PLUS</b>.\n\n"
                "Сначала оформите PLUS — затем можно продлить «Удачу».",
                parse_mode="HTML",
                reply_markup=_kb_luck_need_plus(),
            )
            return
        await cb.message.edit_text(
            luck_shop_intro_html(),
            parse_mode="HTML",
            reply_markup=kb_luck_tariffs(),
        )
        return

    if data.startswith("luck:tariff:"):
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Тарифы «Удача»</b> доступны с активной подпиской <b>PLUS</b>.\n\n"
                "Сначала оформите PLUS — затем можно продлить «Удачу».",
                parse_mode="HTML",
                reply_markup=_kb_luck_need_plus(),
            )
            return
        key = data.split(":", 2)[2]
        t_luck = luck_tariff_by_key(key)
        if not t_luck:
            await cb.message.edit_text(
                "<b>Тариф не найден.</b>",
                parse_mode="HTML",
            )
            return
        await cb.message.edit_text(
            luck_tariff_payment_html(
                t_luck, payment_hint=settings.luck_payment_hint
            ),
            parse_mode="HTML",
            reply_markup=kb_luck_payment_nav(),
        )
        return

    if data == "cab:back_frag":
        await cb.message.edit_text(
            "<b>Готово.</b> Продолжайте с клавиатуры внизу экрана.",
            parse_mode="HTML",
        )
        return

    if data == "cab:saved":
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Список сохранённых ников</b> доступен с <b>Подписка PLUS</b>.",
                parse_mode="HTML",
            )
            return
        names = db.list_saved(uid)
        if not names:
            await cb.message.edit_text(
                f"<b>Пока пусто.</b> Сохраняйте @ники кнопкой <b>«Сохранить»</b> после "
                f"«{html.escape(BTN_SEARCH_F)}» или после <b>оценки</b> в «{html.escape(BTN_VALUATE_F)}».\n\n"
                f"<i>Можно хранить до <b>{SAVED_USERNAMES_LIMIT}</b> ников.</i>",
                parse_mode="HTML",
                reply_markup=None,
            )
            return
        await cb.message.edit_text(
            html_saved_usernames_panel(names),
            parse_mode="HTML",
            reply_markup=kb_saved_list_manage(names),
        )
        return

    if data.startswith("save:"):
        if not db.is_plus(uid):
            await cb.message.answer(
                "Сохранение никнеймов — только с подпиской PLUS",
                parse_mode="HTML",
            )
            return
        name = data.split(":", 1)[1].lower()
        res = db.save_username(uid, name)
        if res == "saved":
            await cb.message.answer("✅ Юзернейм сохранён!", parse_mode="HTML")
        elif res == "duplicate":
            await cb.message.answer("Этот ник уже в сохранённых.", parse_mode="HTML")
        elif res == "limit":
            await cb.message.answer(
                f"Достигнут лимит <b>{SAVED_USERNAMES_LIMIT}</b> сохранённых ников. "
                "Удалите лишние в «Сохранённые ники».",
                parse_mode="HTML",
            )
        return

    if data == "frag:filters" or data.startswith("frag:f:"):
        if not db.is_plus(uid):
            await cb.answer("Фильтры — только с подпиской PLUS", show_alert=True)
            return
        st = _frag_roll_state(uid)
        rf = st["roll_f"]
        parts = data.split(":")
        op = parts[2] if len(parts) > 2 else ""

        async def _filters_panel() -> None:
            await cb.message.edit_text(
                _html_frag_filters_panel(uid),
                parse_mode="HTML",
                reply_markup=_kb_frag_filters_panel(uid),
            )

        if data == "frag:filters" or op == "pm":
            await _filters_panel()
            return
        if op == "bak":
            await cb.message.edit_text(
                _ui_frag_pick_length(),
                reply_markup=kb_fragment_lengths(uid=uid, db=db),
                parse_mode="HTML",
            )
            return
        if op == "rst":
            st["roll_f"] = {"pre": "", "suf": "", "dig": "any"}
            st["roll_draft_pre"].clear()
            st["roll_draft_suf"].clear()
            await _filters_panel()
            return
        if op == "pre":
            await cb.message.edit_text(
                "<b>Префикс</b> — до 2 букв в начале ника. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_pre']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="pre"),
            )
            return
        if op == "suf":
            await cb.message.edit_text(
                "<b>Суффикс</b> — до 2 букв в конце. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_suf']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="suf"),
            )
            return
        if op == "dg" and len(parts) > 3 and parts[3] in ("any", "yes", "no"):
            rf["dig"] = parts[3]
            await _filters_panel()
            return
        if op == "pok":
            raw = "".join(st["roll_draft_pre"])
            rf["pre"] = re.sub(r"[^a-z]", "", raw.lower())[:2]
            st["roll_draft_pre"].clear()
            await _filters_panel()
            return
        if op == "sok":
            raw = "".join(st["roll_draft_suf"])
            rf["suf"] = re.sub(r"[^a-z]", "", raw.lower())[:2]
            st["roll_draft_suf"].clear()
            await _filters_panel()
            return
        if op == "pex":
            if st["roll_draft_pre"]:
                st["roll_draft_pre"].pop()
            await cb.message.edit_text(
                "<b>Префикс</b>. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_pre']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="pre"),
            )
            return
        if op == "sex":
            if st["roll_draft_suf"]:
                st["roll_draft_suf"].pop()
            await cb.message.edit_text(
                "<b>Суффикс</b>. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_suf']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="suf"),
            )
            return
        if op == "pz":
            rf["pre"] = ""
            st["roll_draft_pre"].clear()
            await _filters_panel()
            return
        if op == "sz":
            rf["suf"] = ""
            st["roll_draft_suf"].clear()
            await _filters_panel()
            return
        if op == "pl" and len(parts) > 3:
            ch = parts[3]
            if len(ch) == 1 and ch in string.ascii_lowercase and len(st["roll_draft_pre"]) < 2:
                st["roll_draft_pre"].append(ch)
            await cb.message.edit_text(
                "<b>Префикс</b>. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_pre']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="pre"),
            )
            return
        if op == "sl" and len(parts) > 3:
            ch = parts[3]
            if len(ch) == 1 and ch in string.ascii_lowercase and len(st["roll_draft_suf"]) < 2:
                st["roll_draft_suf"].append(ch)
            await cb.message.edit_text(
                "<b>Суффикс</b>. Черновик: <code>"
                f"{html.escape(''.join(st['roll_draft_suf']) or '—')}</code>",
                parse_mode="HTML",
                reply_markup=_kb_frag_letter_grid(kind="suf"),
            )
            return
        return

    if data.startswith("frag:len:"):
        length = int(data.split(":")[2])
        await _frag_run_roll_at_length(cb, uid, length, db, settings, checker)
        return

    if data == "frag:cancel":
        await cb.message.edit_text(
            "<b>Отменено.</b> Подбор остановлен. Можно начать заново.",
            parse_mode="HTML",
        )
        return


# ---------- Обработчики: MVP v2 ----------


async def cmd_start_v2(message: Message, db: Database, settings: Settings) -> None:
    assert message.from_user
    uid = message.from_user.id
    db.get_or_create_user(uid)
    _sess[uid].pop("await_username", None)
    subtle = ""
    if settings.username_check_mode == "disabled":
        subtle = (
            "\n\n<i>Режим без проверки Telegram: свободен ли @ник — смотри в настройках профиля сам.</i>"
        )

    intro = (
        f"<b>{html.escape(AMNYAM)}</b>\n"
        "<b>Подбор и оценка никнеймов</b>\n\n"
        f"<b>{html.escape(BTN_ROLL)}</b> — случайный свободный @ из витрины, с проверкой доступности "
        "<i>(если включена у владельца бота)</i>.\n"
        f"<b>{html.escape(BTN_VALUATE)}</b> — отчёт по рынку, несколько ников в одном сообщении, "
        "<b>без списания попыток</b>.\n"
        f"<b>{html.escape(BTN_LUCK)}</b> — приоритет «удачных» сочетаний в подборе "
        "<i>(тарифы — с PLUS)</i>."
        + subtle
    )
    extra_admin = ""
    if uid in settings.admin_ids:
        extra_admin = "\n\n<i>Админ:</i> кнопка <b>⚙️ Админ</b> внизу или <code>/admin</code>."
    intro_full = intro + extra_admin
    await message.answer(
        intro_full,
        reply_markup=kb_v2_main(uid=uid, settings=settings),
        parse_mode="HTML",
    )
    await message.answer(
        f"Бесплатных подборов: <b>{settings.free_search_limit}</b>. "
        "<b>Подписка PLUS</b> — без лимита и сохранение @ников. "
        "<b>Режим «Удача»</b> — тарифы в кабинете или промокод.\n\n"
        "<i>Памятка:</i> <code>/советы</code> · <code>/шпаргалка</code> · <code>/команды</code>.",
        parse_mode="HTML",
    )


async def perform_roll_v2(
    cb: CallbackQuery,
    *,
    uid: int,
    length: int,
    tier_key: str,
    db: Database,
    settings: Settings,
    checker: Any,
) -> None:
    assert cb.message

    ok = await decrement_search_ok(db, uid, settings)
    if not ok:
        if db.is_search_globally_blocked() and uid not in settings.admin_ids:
            await cb.message.edit_text(
                "<b>Подбор на паузе</b>\n\n"
                "Поиск имён временно отключён администратором.",
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await cb.message.edit_text(
                "<b>Лимит исчерпан</b>\n\n"
                "Бесплатные попытки закончились. Оформите <b>Подписка PLUS</b>.",
                parse_mode="HTML",
                reply_markup=None,
            )
        return

    desired_min = TIER_MIN_USD.get(tier_key, 0.0)
    is_plus = db.is_plus(uid)
    luck_subscribed = db.is_luck(uid)
    luck_roll_active = db.is_luck_roll_active(uid)
    ton_usd = settings.ton_to_usd

    if luck_roll_active:
        lucky_note = (
            "\n\n<i>🍀 Режим «Удача»: приоритет удачных имён при той же цене.</i>"
        )
    elif luck_subscribed and not luck_roll_active:
        lucky_note = (
            "\n\n<i>🍀 «Удача» оформлена, но в подборе <b>на паузе</b> "
            "(меню «Удача»).</i>"
        )
    else:
        lucky_note = ""
    tier_hint = (
        f"\n\n<i>Каталог: от <b>${desired_min:,.0f}</b> по оценке Fragment.</i>" + lucky_note
    )
    try:
        await cb.message.edit_text(
            f"✨ <b>{html.escape(AMNYAM)}</b> крутит витрину…\n"
            f"{ROLL_RULE_LINE_HTML}"
            + tier_hint,
            parse_mode="HTML",
        )
    except Exception:
        pass

    candidates: list[tuple[str, float]] = []
    for it in db.iter_fragment_items(limit=5000):
        if it.price_usd is None:
            continue
        if float(it.price_usd) < desired_min:
            continue
        if len(it.username) != length:
            continue
        if not _letters_ok(it.username, length):
            continue
        candidates.append((it.username, float(it.price_usd)))

    jittered = [(u, p, random.random()) for u, p in candidates]
    if luck_roll_active:
        jittered.sort(
            key=lambda t: (t[1], luck_score(t[0]), t[2]),
            reverse=True,
        )
    else:
        jittered.sort(key=lambda t: (t[1], t[2]), reverse=True)
    candidates = [(u, p) for u, p, _ in jittered]

    found: list[tuple[str, float | None, str, str]] = []
    checked = 0
    max_candidates_to_check = 25
    want = 1

    for uname, _price in candidates:
        if len(found) >= want or checked >= max_candidates_to_check:
            break
        checked += 1
        try:
            avail = await checker.is_available(uname)
            if avail is True:
                rarity_info, predicted_price, why = _rarity_for_display(
                    uname, db, ton_usd
                )
                found.append((uname, predicted_price, rarity_info.name, why))
            elif avail is False:
                continue
            elif settings.username_check_mode == "disabled":
                rarity_info, predicted_price, why = _rarity_for_display(
                    uname, db, ton_usd
                )
                found.append(
                    (
                        uname,
                        predicted_price,
                        rarity_info.name,
                        why
                        + " | <i>(проверка занятости в Telegram отключена — смотри вручную)</i>",
                    )
                )
        except Exception:
            log.exception("checker failed %s", uname)
            continue

    attempts = 0
    if not found:
        if settings.username_check_mode == "disabled":
            while attempts < 30 and len(found) < want:
                attempts += 1
                cand = username_roll_random(
                    length,
                    lucky=luck_roll_active,
                    plus_full_cv=is_plus,
                )
                if not is_valid_telegram_username_for_roll(
                    cand, min_len=length, max_len=length
                ):
                    continue
                try:
                    rarity_info, predicted_price, why = _rarity_for_display(
                        cand, db, ton_usd
                    )
                    found.append(
                        (
                            cand,
                            predicted_price,
                            rarity_info.name,
                            why
                            + " | <i>(случайный вариант — занятость в Telegram проверьте сами)</i>",
                        )
                    )
                except Exception:
                    log.exception("roll fallback disabled")
                    continue
        else:
            while attempts < 80 and not found:
                attempts += 1
                cand = username_roll_random(
                    length,
                    lucky=luck_roll_active,
                    plus_full_cv=is_plus,
                )
                if not is_valid_telegram_username_for_roll(
                    cand, min_len=length, max_len=length
                ):
                    continue
                try:
                    if await checker.is_available(cand) is True:
                        rarity_info, predicted_price, why = _rarity_for_display(
                            cand, db, ton_usd
                        )
                        found.append((cand, predicted_price, rarity_info.name, why))
                except Exception:
                    log.exception("roll checker")
                    continue

    if not found:
        await cb.message.edit_text(
            _ui_frag_fail(),
            parse_mode="HTML",
            reply_markup=None,
        )
        return

    for uname, predicted_price, rarity_name, _why in found:
        db.add_roll_event(
            user_id=uid,
            username=uname,
            rarity=rarity_name,
            predicted_price_usd=predicted_price,
        )

    ud = _sess[uid]
    ud["roll_last_len"] = length
    ud["roll_last_tier"] = tier_key
    ud["roll_last_username"] = found[0][0].lower()

    parts: list[str] = []
    for uname, predicted_price, rarity_name, why in found:
        parts.append(
            _roll_result_card_html(
                uname=uname,
                predicted_price=predicted_price,
                rarity_name=rarity_name,
                why=why,
                has_luck=luck_roll_active,
            )
        )
    plus_tail = (
        ""
        if is_plus
        else "\n\n<i>Сохранение одним нажатием — в подписке <b>PLUS</b>.</i>"
    )
    await cb.message.edit_text(
        "\n\n".join(parts).strip() + plus_tail,
        parse_mode="HTML",
        reply_markup=kb_roll_post_result(found[0][0], is_plus=is_plus),
    )


async def perform_analysis_v2(
    message: Message,
    *,
    uid: int,
    raw: str,
    db: Database,
    settings: Settings,
    checker: Any,
) -> None:
    """Оценка @ника (не списывает крутки; поддерживает пачку через запятую)."""
    await perform_appraisals_batch(
        message,
        uid=uid,
        raw=raw,
        db=db,
        settings=settings,
        checker=checker,
    )


async def cmd_grant_plus(
    message: Message, command: CommandObject, db: Database, settings: Settings
) -> None:
    assert message.from_user
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Только админ.")
        return
    args = (command.args or "").split(maxsplit=1)
    if not args or not args[0].isdigit():
        await message.answer("/grant_plus <user_id>")
        return
    db.set_plus(int(args[0]), True)
    await message.answer(f"<b>Подписка PLUS</b> выдана пользователю <code>{args[0]}</code>.")


async def cmd_activate_plus(
    message: Message, command: CommandObject, db: Database, settings: Settings
) -> None:
    """После оплаты: выдать PLUS на срок по тарифу (только админ)."""
    assert message.from_user
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Только админ.")
        return
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer(
            "/activate_plus <user_id> <тариф>\n"
            "Тарифы: <code>1d 3d 7d 30d 183d 365d forever</code>",
            parse_mode="HTML",
        )
        return
    uid = int(parts[0])
    t = plus_tariff_by_key(parts[1])
    if not t:
        await message.answer(
            "Неизвестный тариф. Допустимо: "
            "<code>1d 3d 7d 30d 183d 365d forever</code>.",
            parse_mode="HTML",
        )
        return
    if t.days is None:
        db.set_plus_forever_paid(uid)
    else:
        db.extend_plus_days(uid, t.days)
    await message.answer(
        f"PLUS для <code>{uid}</code>: <b>{html.escape(t.title_ru)}</b> "
        f"(прайс <code>{t.price_rub} ₽</code>).",
        parse_mode="HTML",
    )


async def cmd_activate_luck(
    message: Message, command: CommandObject, db: Database, settings: Settings
) -> None:
    """После оплаты: выдать «Удачу» на срок по тарифу (только админ)."""
    assert message.from_user
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Только админ.")
        return
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer(
            "/activate_luck <user_id> <тариф>\n"
            "Тарифы: <code>1h 3h 1d 7d forever</code>",
            parse_mode="HTML",
        )
        return
    uid_act = int(parts[0])
    lt = luck_tariff_by_key(parts[1])
    if not lt:
        await message.answer(
            "Неизвестный тариф. Допустимо: "
            "<code>1h 3h 1d 7d forever</code>.",
            parse_mode="HTML",
        )
        return
    if not db.is_plus(uid_act):
        await message.answer(
            f"У пользователя <code>{uid_act}</code> нет активной <b>PLUS</b>. "
            "Сначала выдайте PLUS, затем тариф «Удача».",
            parse_mode="HTML",
        )
        return
    if lt.delta is None:
        db.set_luck_forever_paid(uid_act)
    else:
        db.extend_luck_delta(uid_act, lt.delta)
    await message.answer(
        f"«Удача» для <code>{uid_act}</code>: <b>{html.escape(lt.title_ru)}</b> "
        f"(прайс <code>{lt.price_rub} ₽</code>).",
        parse_mode="HTML",
    )


async def cmd_grant_luck(
    message: Message, command: CommandObject, db: Database, settings: Settings
) -> None:
    assert message.from_user
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Только админ.")
        return
    args = (command.args or "").split(maxsplit=1)
    if not args or not args[0].isdigit():
        await message.answer("/grant_luck <user_id>")
        return
    uid = int(args[0])
    db.set_luck(uid, True)
    await message.answer(f"<b>Удача</b> включена для <code>{uid}</code>.")


async def cmd_import_fragment(
    message: Message, command: CommandObject, db: Database, settings: Settings
) -> None:
    assert message.from_user
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Только админ.")
        return
    url = (command.args or "").strip()
    if not url:
        await message.answer("/import_fragment <url>")
        return
    await message.answer("Импорт…")
    try:
        gift = await asyncio.to_thread(
            partial(
                fetch_fragment_gift_price,
                url,
                ton_to_usd=settings.ton_to_usd,
            )
        )
    except Exception as e:
        log.exception("import")
        await message.answer(f"Ошибка: {e}")
        return
    db.upsert_fragment_item(
        username=gift.username,
        price_usd=gift.price_usd,
        source_url=gift.source_url,
    )
    if gift.price_usd is None:
        await message.answer(f"@{gift.username} импорт (цена ?).")
    else:
        await message.answer(f"@{gift.username} ~ ${gift.price_usd:,.0f}")


async def cmd_support_v2(message: Message, settings: Settings) -> None:
    assert message.from_user
    uid = message.from_user.id
    await message.answer(
        f"<b>Поддержка</b> · {html.escape(AMNYAM)}\n\n"
        "Технический сбой или вопрос по сервису — напишите владельцу бота.",
        reply_markup=kb_v2_main(uid=uid, settings=settings),
        parse_mode="HTML",
    )


async def on_text_v2(
    message: Message, db: Database, settings: Settings, checker: Any
) -> None:
    assert message.text and message.from_user
    uid = message.from_user.id
    text = message.text.strip()
    ud = _sess[uid]

    if await admin_try_handle_text(
        message,
        sess_uid=ud,
        db=db,
        settings=settings,
        kb_main=kb_v2_main(uid=uid, settings=settings),
    ):
        return

    if text == BTN_ADMIN:
        if uid not in settings.admin_ids:
            await message.answer(
                "<b>Нет доступа.</b> Клавиша только для администратора.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        await cmd_admin(message, db=db, settings=settings)
        return

    _reserved_v2 = frozenset(
        {
            BTN_ROLL,
            BTN_VALUATE,
            BTN_TOP,
            BTN_CABINET,
            BTN_SUPPORT,
            BTN_PLUS,
            BTN_LUCK,
            BTN_DOCS,
            BTN_REFERRAL,
            BTN_ADMIN,
        }
    )
    if ud.get("await_username") and text in _reserved_v2:
        ud.pop("await_username", None)

    if ud.get("await_username"):
        ud["await_username"] = False
        await perform_analysis_v2(
            message,
            uid=uid,
            raw=text,
            db=db,
            settings=settings,
            checker=checker,
        )
        return

    if PENDING_LUCK_PROMO.pop(uid, None):
        if not luck_promo_entry_available(settings, db):
            await message.answer(
                "<b>Режим «Удача»</b>: активных промокодов нет. Напишите в поддержку.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        ok, reason = redeem_luck_code(text, uid, db=db, settings=settings)
        if ok:
            rem_txt = ""
            if (
                settings.luck_promo_code
                and text.strip().upper() == settings.luck_promo_code
                and settings.luck_promo_max_uses > 0
            ):
                used = db.luck_promo_uses_count(settings.luck_promo_code)
                rem = max(0, settings.luck_promo_max_uses - used)
                rem_txt = f"\n\n<i>Осталось активаций этого кода: <b>{rem}</b>.</i>"
            await message.answer(
                "<b>🍀 Удача активирована.</b>\n\n"
                "При подборе будет учитываться приоритет удачных имён в каталоге."
                + rem_txt,
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        else:
            extra = ""
            if reason == "limit_env":
                extra = " (лимит активаций исчерпан)"
            elif reason == "already":
                extra = " (вы уже активировали этот код)"
            elif reason == "limit":
                extra = " (лимит исчерпан)"
            await message.answer(
                f"<b>Отклонено.</b> Код не принят{extra}.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        return

    if PENDING_PROMO.pop(uid, None):
        ok, _reason, plus_days = redeem_plus_code(text, uid, db=db, settings=settings)
        if ok:
            skip_timed_note = ""
            if plus_days is None:
                db.set_plus_forever_paid(uid)
            else:
                urow = db.get_or_create_user(uid)
                if int(urow.is_plus) and not urow.plus_expires_at:
                    skip_timed_note = (
                        "\n\n<i>У вас уже PLUS без даты окончания — срок по этому коду не меняли.</i>"
                    )
                else:
                    db.extend_plus_days(uid, plus_days)
            period_line = _plus_promo_period_user_ru(plus_days) + skip_timed_note
            await message.answer(
                "<b>Подписка PLUS активирована.</b>\n\n"
                f"{period_line}\n\n"
                f"<b>{html.escape(AMNYAM)}</b>: безлимит подборов и сохранение понравившихся @ников.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        else:
            extra = ""
            if _reason == "already":
                extra = " (этот код вы уже использовали)"
            elif _reason == "limit":
                extra = " (лимит исчерпан)"
            await message.answer(
                "<b>Отклонено.</b> Код не принят"
                f"{extra}. Проверь ввод.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        return

    first = text.lower().split(maxsplit=1)[0]
    if first in _TEXT_START:
        ud.pop("await_username", None)
        await cmd_start_v2(message, db, settings)
        return

    if text == BTN_ROLL:
        if db.is_search_globally_blocked() and uid not in settings.admin_ids:
            await message.answer(
                "<b>Подбор на паузе.</b>\n\n"
                "Поиск никнеймов временно закрыт администратором. Оценка и остальное доступны.",
                reply_markup=kb_v2_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
            return
        ud.pop("roll_len", None)
        ud.pop("roll_tier", None)
        await message.answer(
            _roll_pick_intro_html(),
            reply_markup=kb_v2_lengths(),
            parse_mode="HTML",
        )
        return

    if text == BTN_VALUATE:
        ud["await_username"] = True
        await message.answer(
            "<b>Оценка никнейма</b>\n\n"
            "Отправьте один или несколько ников в сообщении, например:\n"
            "<code>crypto, @wolf, ninja</code>\n\n"
            "<i>Не расходует бесплатные подборы. Для одного ника — карточка с кнопками внизу.</i>",
            parse_mode="HTML",
        )
        return

    if text == BTN_TOP:
        top = db.top_roll_month(days=30, limit=10)
        if not top:
            await message.answer(
                f"<b>Рейтинг пуст.</b> Сделайте несколько подборов через «{html.escape(BTN_ROLL)}».",
                parse_mode="HTML",
            )
            return
        lines = [
            "<b>Топ за 30 дней</b>",
            "",
        ]
        for i, (uname, rarity, pred) in enumerate(top, start=1):
            usd_txt = "?" if pred is None else f"${pred:,.0f}"
            lines.append(
                f"{i}. @{html.escape(uname)} — <b>{html.escape(rarity)}</b> ({usd_txt})"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    if text == BTN_CABINET:
        await message.answer(
            await cabinet_text_v2(db, uid, settings),
            reply_markup=kb_v2_cabinet(),
            parse_mode="HTML",
        )
        return

    if text == BTN_REFERRAL:
        await answer_referral_program(message, db, settings)
        return

    if text == BTN_SUPPORT:
        await cmd_support_v2(message, settings)
        return

    if text == BTN_DOCS:
        await message.answer(
            legal_documents_user_html(db),
            reply_markup=kb_v2_main(uid=uid, settings=settings),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    if text == BTN_PLUS:
        await message.answer(
            plus_shop_intro_html(),
            reply_markup=kb_plus_tariffs(),
            parse_mode="HTML",
        )
        return

    if text == BTN_LUCK:
        await message.answer(
            _luck_menu_html(uid, db, BTN_ROLL),
            reply_markup=_luck_menu_inline_kb(uid, db, settings),
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"<b>{html.escape(AMNYAM)}</b>: выберите действие кнопками внизу или "
        "<code>/start</code>.",
        reply_markup=kb_v2_main(uid=uid, settings=settings),
        parse_mode="HTML",
    )


async def on_callback_v2(cb: CallbackQuery, db: Database, settings: Settings, checker: Any) -> None:
    assert cb.data and cb.from_user and cb.message
    uid = cb.from_user.id
    data = cb.data
    if data.startswith("adm:"):
        if uid not in settings.admin_ids:
            await cb.answer("Нет доступа", show_alert=True)
            return
        await cb.answer()
        await admin_handle_callback(cb, sess_uid=_sess[uid], db=db, settings=settings)
        return
    ud = _sess[uid]

    if data in ("roll:repeat:start", "roll:repeat"):
        await _handle_roll_repeat_start(cb, db, settings, checker, mode="v2")
        return

    if data == "roll:repeat:go":
        await _handle_roll_repeat_go_v2(cb, db, settings, checker)
        return

    if data == "luck:toggle":
        u = db.get_or_create_user(uid)
        if not db.is_plus(uid) or not db.is_luck(uid):
            await cb.answer("Нужны PLUS и активная «Удача».", show_alert=True)
            return
        paused = int(getattr(u, "luck_roll_paused", 0))
        db.set_luck_roll_paused(uid, paused=not paused)
        now_paused = int(getattr(db.get_or_create_user(uid), "luck_roll_paused", 0))
        toast = (
            "В подборе без учёта «Удачи», срок тарифа не сгорает."
            if now_paused
            else "«Удача» снова учитывается в подборе."
        )
        await cb.answer(toast[:200])
        await cb.message.edit_text(
            _luck_menu_html(uid, db, BTN_ROLL),
            parse_mode="HTML",
            reply_markup=_luck_menu_inline_kb(uid, db, settings),
        )
        return

    await cb.answer()

    if data.startswith("saved_del:"):
        if not db.is_plus(uid):
            return
        nick = data.removeprefix("saved_del:").strip()
        uname = normalize_username(nick)
        if not is_valid_telegram_username(uname):
            await cb.message.answer("<b>Некорректный ник.</b>", parse_mode="HTML")
            return
        db.remove_saved(uid, uname)
        names = db.list_saved(uid)
        if not names:
            await cb.message.edit_text(
                "<b>Сохранённые @ники</b>\n\n"
                "<i>Список пуст. Можно сохранить новые из крутки или оценки "
                f"(до <b>{SAVED_USERNAMES_LIMIT}</b> шт.).</i>",
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await cb.message.edit_text(
                html_saved_usernames_panel(names),
                parse_mode="HTML",
                reply_markup=kb_saved_list_manage(names),
            )
        await cb.message.answer("🗑 Ник удалён из сохранённых.", parse_mode="HTML")
        return

    if data == "cab:saved":
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Список сохранённых ников</b> доступен с <b>Подписка PLUS</b>.",
                parse_mode="HTML",
            )
            return
        names = db.list_saved(uid)
        if not names:
            await cb.message.edit_text(
                "<b>Пока пусто.</b> Сохраняйте @ники кнопкой <b>«Сохранить»</b> после крутки "
                f"или после <b>оценки</b> («{html.escape(BTN_VALUATE)}»).\n\n"
                f"<i>Можно хранить до <b>{SAVED_USERNAMES_LIMIT}</b> ников.</i>",
                parse_mode="HTML",
                reply_markup=None,
            )
            return
        await cb.message.edit_text(
            html_saved_usernames_panel(names),
            parse_mode="HTML",
            reply_markup=kb_saved_list_manage(names),
        )
        return

    if data == "cab:back":
        await cb.message.edit_text(
            "<b>Готово.</b> Продолжайте с клавиатуры внизу.",
            parse_mode="HTML",
        )
        return

    if data.startswith("plus:tariff:"):
        key = data.split(":", 2)[2]
        t = plus_tariff_by_key(key)
        if not t:
            await cb.message.edit_text(
                "<b>Тариф не найден.</b>",
                parse_mode="HTML",
            )
            return
        await cb.message.edit_text(
            plus_tariff_payment_html(t, payment_hint=settings.plus_payment_hint),
            parse_mode="HTML",
            reply_markup=kb_plus_payment_nav(),
        )
        return

    if data == "plus:shop":
        await cb.message.edit_text(
            plus_shop_intro_html(),
            parse_mode="HTML",
            reply_markup=kb_plus_tariffs(),
        )
        return

    if data == "plus:enter":
        PENDING_PROMO[uid] = True
        await cb.message.edit_text(
            "<b>Промокод PLUS</b>\n\n"
            "Отправьте код одним сообщением.\n"
            "<code>/cancel</code> — отмена.",
            parse_mode="HTML",
        )
        return

    if data == "luck:enter":
        if not luck_promo_entry_available(settings, db):
            await cb.message.edit_text(
                "<b>Режим «Удача»</b>: активных промокодов нет. Напишите в поддержку.",
                parse_mode="HTML",
            )
            return
        PENDING_LUCK_PROMO[uid] = True
        await cb.message.edit_text(
            "<b>Промокод «Удача»</b>\n\n"
            "Отправьте код одним сообщением.\n"
            "<code>/cancel</code> — отмена.",
            parse_mode="HTML",
        )
        return

    if data == "luck:shop":
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Тарифы «Удача»</b> доступны с активной подпиской <b>PLUS</b>.\n\n"
                "Сначала оформите PLUS — затем можно продлить «Удачу».",
                parse_mode="HTML",
                reply_markup=_kb_luck_need_plus(),
            )
            return
        await cb.message.edit_text(
            luck_shop_intro_html(),
            parse_mode="HTML",
            reply_markup=kb_luck_tariffs(),
        )
        return

    if data.startswith("luck:tariff:"):
        if not db.is_plus(uid):
            await cb.message.edit_text(
                "<b>Тарифы «Удача»</b> доступны с активной подпиской <b>PLUS</b>.\n\n"
                "Сначала оформите PLUS — затем можно продлить «Удачу».",
                parse_mode="HTML",
                reply_markup=_kb_luck_need_plus(),
            )
            return
        key = data.split(":", 2)[2]
        t = luck_tariff_by_key(key)
        if not t:
            await cb.message.edit_text(
                "<b>Тариф не найден.</b>",
                parse_mode="HTML",
            )
            return
        await cb.message.edit_text(
            luck_tariff_payment_html(
                t, payment_hint=settings.luck_payment_hint
            ),
            parse_mode="HTML",
            reply_markup=kb_luck_payment_nav(),
        )
        return

    if data.startswith("val:go:"):
        raw = data.split(":", 2)[2]
        uname = normalize_username(raw)
        if not is_valid_telegram_username(uname):
            await cb.message.edit_text(
                "<b>Некорректный никнейм.</b>",
                parse_mode="HTML",
            )
            return
        await cb.message.edit_text("<b>Считаю оценку…</b>", parse_mode="HTML")
        listed_map2: dict[str, bool | None] = {}
        if settings.bot_mode == "fragment":
            try:
                listed_map2[uname] = await asyncio.to_thread(
                    username_listed_on_fragment,
                    uname,
                    timeout_s=22,
                )
            except Exception:
                log.exception("val:go fragment")
                listed_map2[uname] = None
        telegram_map2: dict[str, str] = {}
        if (
            settings.username_check_mode != "disabled"
            and getattr(checker, "uses_telethon", False)
        ):
            try:
                av = await checker.is_available(uname)
                if av is True:
                    telegram_map2[uname] = (
                        "<b>Telegram:</b> по ответу API ник <b>свободен</b> для смены."
                    )
                elif av is False:
                    telegram_map2[uname] = (
                        "<b>Telegram:</b> ник уже <b>занят</b>."
                    )
                else:
                    telegram_map2[uname] = (
                        "<b>Telegram:</b> статус не получен."
                    )
            except Exception:
                log.exception("val:go telethon")
                telegram_map2[uname] = (
                    "<b>Telegram:</b> проверка недоступна (ошибка)."
                )
        rep = evaluate_username_market(
            uname, db, ton_to_usd=settings.ton_to_usd
        )
        frag_line = None
        if uname in listed_map2 and listed_map2[uname] is not None:
            frag_line = (
                "<b>Fragment:</b> на витрине виден как <b>лот</b>."
                if listed_map2[uname]
                else "<b>Fragment:</b> активного лота не видно. "
                "<i>При сомнении проверьте ник в приложении.</i>"
            )
        title = f"<b>Оценка</b> {_format_username(rep.username)}"
        body = _valuation_block_html(
            rep,
            fragment_line=frag_line,
            telegram_status=telegram_map2.get(uname),
            title_line=title,
        )
        await cb.message.edit_text(
            body,
            parse_mode="HTML",
            reply_markup=kb_valuation_post_single(
                uname, is_plus=db.is_plus(uid)
            ),
        )
        return

    if data.startswith("val:sim:"):
        uname = data.split(":", 2)[2].lower()
        sims = suggest_similar_usernames(uname, limit=8)
        sim_txt = (
            "\n".join(f"• <code>@{html.escape(s)}</code>" for s in sims)
            or "<i>Не удалось сгенерировать список — попробуйте другой ник.</i>"
        )
        await cb.message.edit_text(
            f"<b>Похожие никнеймы</b> {_format_username(uname)}\n"
            "<code>──────────</code>\n\n"
            f"{sim_txt}\n\n"
            "<i>Идеи без проверки свободы. Полная карточка — через «Оценить этот ник» или меню оценки.</i>",
            parse_mode="HTML",
            reply_markup=kb_valuation_post_single(
                uname, is_plus=db.is_plus(uid)
            ),
        )
        return

    if data == "val:back":
        await cb.message.edit_text(
            _roll_pick_intro_html(),
            reply_markup=kb_v2_lengths(),
            parse_mode="HTML",
        )
        return

    if data.startswith("save:"):
        if not db.is_plus(uid):
            await cb.message.answer(
                "Сохранение никнеймов — только с подпиской PLUS",
                parse_mode="HTML",
            )
            return
        uname = data.split(":", 1)[1].lower()
        res = db.save_username(uid, uname)
        if res == "saved":
            await cb.message.answer("✅ Юзернейм сохранён!", parse_mode="HTML")
        elif res == "duplicate":
            await cb.message.answer("Этот ник уже в сохранённых.", parse_mode="HTML")
        elif res == "limit":
            await cb.message.answer(
                f"Достигнут лимит <b>{SAVED_USERNAMES_LIMIT}</b> сохранённых ников. "
                "Удалите лишние в «Сохранённые ники».",
                parse_mode="HTML",
            )
        return

    if data == "roll:cancel":
        ud.pop("roll_len", None)
        ud.pop("roll_tier", None)
        await cb.message.edit_text(
            f"<b>Отменено.</b> Можно снова нажать «{html.escape(BTN_ROLL)}».",
            parse_mode="HTML",
        )
        return

    if data.startswith("roll:len:"):
        ln = int(data.split(":", 2)[2])
        ud["roll_len"] = ln
        await cb.message.edit_text(
            _roll_tier_pick_html(),
            reply_markup=kb_v2_rarity(),
            parse_mode="HTML",
        )
        return

    if data.startswith("roll:tier:"):
        tier = data.split(":", 2)[2]
        if "roll_len" not in ud:
            await cb.message.edit_text(
                f"<b>⚠️ Сначала длину.</b> Нажми «{html.escape(BTN_ROLL)}» и выбери 🔹 5 или 6.",
                parse_mode="HTML",
            )
            return
        length = int(ud.pop("roll_len"))
        ud.pop("roll_tier", None)
        await perform_roll_v2(
            cb,
            uid=uid,
            length=length,
            tier_key=tier,
            db=db,
            settings=settings,
            checker=checker,
        )
        return

    await cb.message.edit_text(
        "<b>Неизвестная кнопка.</b> Нажми <code>/start</code> для сброса.",
        parse_mode="HTML",
    )


async def cmd_cancel(message: Message, settings: Settings) -> None:
    assert message.from_user
    uid = message.from_user.id
    PENDING_PROMO.pop(uid, None)
    PENDING_LUCK_PROMO.pop(uid, None)
    _sess[uid].pop("await_username", None)
    _sess[uid].pop("await_valuation", None)
    _sess[uid].pop("roll_len", None)
    _sess[uid].pop("roll_tier", None)
    admin_clear_session(_sess[uid])

    kb = (
        kb_fragment_main(uid=uid, settings=settings)
        if settings.bot_mode == "fragment"
        else kb_v2_main(uid=uid, settings=settings)
    )
    await message.answer(
        f"<b>Сброс.</b> Ввод отменён. Продолжайте с кнопками внизу — {html.escape(AMNYAM)}.",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def router_entry(
    message: Message, db: Database, settings: Settings, checker: Any
) -> None:
    if message.text:
        await on_text_router(message, db, settings, checker)


async def start_entry(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    assert message.from_user
    uid = message.from_user.id
    payload = (command.args or "").strip()
    ref_uid = parse_referrer_id_from_start_payload(payload)
    if ref_uid is None:
        ref_uid = take_pending_referrer(uid)
    else:
        clear_pending_referrer(uid)
    existed = db.user_exists(uid)
    db.get_or_create_user(uid)
    if not existed and ref_uid is not None:
        if db.try_register_referral(
            referred_user_id=uid,
            referrer_user_id=ref_uid,
            bonus_hours=settings.referral_plus_hours,
        ):
            try:
                await message.bot.send_message(
                    ref_uid,
                    f"<b>🎁 Реферал!</b> Новый пользователь зашёл по вашей ссылке.\n"
                    f"Начислено <b>+{settings.referral_plus_hours} ч</b> подписки PLUS.",
                    parse_mode="HTML",
                )
            except Exception:
                log.debug("referrer notify failed", exc_info=True)
    if settings.bot_mode == "fragment":
        await cmd_start_frag(message, db, settings)
    else:
        await cmd_start_v2(message, db, settings)


async def on_text_router(
    message: Message, db: Database, settings: Settings, checker: Any
) -> None:
    assert message.text
    if settings.bot_mode == "fragment":
        await on_text_frag(message, db, settings, checker)
    else:
        await on_text_v2(message, db, settings, checker)


async def cb_router(cb: CallbackQuery, db: Database, settings: Settings, checker: Any) -> None:
    data = cb.data or ""
    if settings.bot_mode == "fragment":
        await on_callback_frag(cb, db, settings, checker)
        return
    await on_callback_v2(cb, db, settings, checker)


async def errors_handler(event: ErrorEvent) -> None:
    log.error("Ошибка в обработчике", exc_info=event.exception)
    message = getattr(event.update, "message", None)
    cb = getattr(event.update, "callback_query", None)
    txt = _ui_plain_error()
    try:
        if message:
            await message.answer(txt, parse_mode="HTML")
        elif cb and cb.message:
            await cb.answer(
                f"{html.escape(AMNYAM)}: ошибка. Нажмите /start.",
                show_alert=True,
            )
    except Exception:
        log.exception("Не удалось уведомить пользователя")


async def answer_slash_docs(message: Message, settings: Settings) -> None:
    raw = (message.text or "").strip()
    if not raw.startswith("/"):
        return
    cmd = raw.lstrip("/").split()[0].lower()
    aid = message.from_user.id if message.from_user else None
    kb = (
        kb_fragment_main(uid=aid, settings=settings)
        if settings.bot_mode == "fragment"
        else kb_v2_main(uid=aid, settings=settings)
    )
    if cmd in ("советы", "sovety"):
        await message.answer(text_sovety(), reply_markup=kb, parse_mode="HTML")
    elif cmd in ("шпаргалка", "shpargalka"):
        await message.answer(text_shpargalka(), reply_markup=kb, parse_mode="HTML")
    elif cmd in ("команды", "komandy"):
        await message.answer(text_komandy(), reply_markup=kb, parse_mode="HTML")


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )


async def aiogram_main() -> None:
    setup_logging()
    settings = load_settings()
    db = Database(settings.db_path.resolve())
    checker = build_checker(settings)

    if settings.use_mtproto_bot:
        from mtproxy_bot_runner import run_telethon_mtproto_bot_stack

        run_telethon_mtproto_bot_stack(settings, db, checker)
        return

    if settings.bot_mode == "fragment":
        log.info("Aiogram: режим Fragment (простой поиск)")
    elif settings.username_check_mode == "disabled":
        log.warning(
            "Aiogram MVP: USERNAME_CHECK_MODE=disabled — Telethon-проверка username выключена."
        )

    bot = create_bot(settings)
    dp = Dispatcher()
    dp.update.middleware(
        DependenciesMiddleware(db=db, settings=settings, checker=checker, bot=bot)
    )
    dp.message.middleware(AiogramChannelGateMiddleware())
    dp.callback_query.middleware(AiogramChannelGateMiddleware())

    @dp.message(CommandStart())
    async def _start(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await start_entry(m, command, db, settings)

    @dp.message(Command("cancel"))
    async def _cancel(m: Message, settings: Settings) -> None:
        await cmd_cancel(m, settings)

    @dp.message(Command("admin"))
    async def _admin_panel(m: Message, db: Database, settings: Settings) -> None:
        await cmd_admin(m, db=db, settings=settings)

    @dp.message(Command("grant_plus"))
    async def _grant(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await cmd_grant_plus(m, command, db, settings)

    @dp.message(Command("activate_plus"))
    async def _activate_plus(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await cmd_activate_plus(m, command, db, settings)

    @dp.message(Command("activate_luck"))
    async def _activate_luck_cmd(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await cmd_activate_luck(m, command, db, settings)

    @dp.message(Command("grant_luck"))
    async def _grant_luck_cmd(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await cmd_grant_luck(m, command, db, settings)

    @dp.message(Command("import_fragment"))
    async def _imp(
        m: Message, command: CommandObject, db: Database, settings: Settings
    ) -> None:
        await cmd_import_fragment(m, command, db, settings)

    @dp.message(Command("support"))
    async def _sup(m: Message, settings: Settings) -> None:
        assert m.from_user
        uid = m.from_user.id
        if settings.bot_mode == "fragment":
            await m.answer(
                f"<b>Поддержка</b> · {html.escape(AMNYAM)}\n\n"
                "Технический сбой или вопрос по сервису — напишите владельцу бота.",
                reply_markup=kb_fragment_main(uid=uid, settings=settings),
                parse_mode="HTML",
            )
        else:
            await cmd_support_v2(m, settings)

    @dp.message(F.text.regexp(r"(?i)^/(советы|sovety|шпаргалка|shpargalka|команды|komandy)\b"))
    async def _slash_docs(m: Message, settings: Settings) -> None:
        await answer_slash_docs(m, settings)

    # Текст не-команды
    @dp.message(F.text)
    async def _txt(m: Message, db: Database, settings: Settings, checker: Any) -> None:
        await router_entry(m, db, settings, checker)

    @dp.callback_query()
    async def _cb(
        c: CallbackQuery, db: Database, settings: Settings, checker: Any
    ) -> None:
        await cb_router(c, db, settings, checker)

    @dp.errors()
    async def _err(event: ErrorEvent) -> None:
        await errors_handler(event)

    async def startup() -> None:
        if getattr(checker, "uses_telethon", False):
            await checker.start()
        mode = settings.bot_mode
        log.info(
            "Aiogram запущен, BOT_MODE=%s",
            mode,
        )

    async def shutdown() -> None:
        if getattr(checker, "uses_telethon", False):
            await checker.stop()

    dp.startup.register(startup)
    dp.shutdown.register(shutdown)

    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(aiogram_main())


if __name__ == "__main__":
    main()
