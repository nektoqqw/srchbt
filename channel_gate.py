"""Обязательная подписка на канал: PTB, Telethon (без зависимости от aiogram)."""

from __future__ import annotations

import html
import logging
from typing import Any

log = logging.getLogger(__name__)

SUB_CHECK_CALLBACK = "sub:check"


def subscription_prompt_html(channel_username: str) -> str:
    un = channel_username.strip().lstrip("@")
    return (
        "<b>Нужна подписка на канал</b>\n\n"
        "Чтобы пользоваться ботом, подпишитесь на канал "
        f"<b>@{html.escape(un)}</b>.\n\n"
        "После подписки нажмите <b>«Я подписался — проверить»</b> "
        "или отправьте <code>/start</code>."
    )


def ptb_subscribe_markup(channel_username: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    ch = channel_username.strip().lstrip("@")
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подписаться на канал", url=f"https://t.me/{ch}")],
            [
                InlineKeyboardButton(
                    "Я подписался — проверить", callback_data=SUB_CHECK_CALLBACK
                )
            ],
        ]
    )


async def ptb_user_is_channel_member(bot: Any, channel_username: str, user_id: int) -> bool:
    from telegram.error import TelegramError
    from telegram.constants import ChatMemberStatus

    ch = channel_username.strip().lstrip("@")
    if not ch:
        return True
    try:
        m = await bot.get_chat_member(chat_id=f"@{ch}", user_id=user_id)
        return m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except TelegramError as e:
        log.warning("get_chat_member @%s user=%s: %s", ch, user_id, e)
        return False
    except Exception:
        log.exception("get_chat_member @%s user=%s", ch, user_id)
        return False


async def telethon_user_is_channel_member(
    client: Any, channel_username: str, user_id: int
) -> bool:
    from telethon.errors import UserNotParticipantError
    from telethon.tl.functions.channels import GetParticipantRequest

    ch = channel_username.strip().lstrip("@")
    if not ch:
        return True
    try:
        ent_ch = await client.get_entity(ch)
        user_peer = await client.get_input_entity(user_id)
        await client(GetParticipantRequest(ent_ch, user_peer))
        return True
    except UserNotParticipantError:
        return False
    except Exception:
        log.exception("telethon channel membership @%s user=%s", ch, user_id)
        return False


async def ptb_user_may_use_bot(update: Any, context: Any) -> bool:
    """True — можно обрабатывать запрос; False — уже отправлено напоминание о канале."""
    settings = context.bot_data["settings"]
    ch = settings.required_channel_username
    if not ch:
        return True
    user = update.effective_user
    if not user or user.id in settings.admin_ids:
        return True
    if await ptb_user_is_channel_member(context.bot, ch, user.id):
        return True
    msg = update.effective_message
    if msg:
        await msg.reply_html(
            subscription_prompt_html(ch),
            reply_markup=ptb_subscribe_markup(ch),
        )
    return False
