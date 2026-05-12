"""Middleware Aiogram: обязательная подписка на канал (зависит от aiogram)."""

from __future__ import annotations

from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

from channel_gate import SUB_CHECK_CALLBACK, subscription_prompt_html


def aiogram_subscribe_markup(channel_username: str) -> InlineKeyboardMarkup:
    ch = channel_username.strip().lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подписаться на канал", url=f"https://t.me/{ch}")],
            [
                InlineKeyboardButton(
                    text="Я подписался — проверить", callback_data=SUB_CHECK_CALLBACK
                )
            ],
        ]
    )


async def aiogram_user_is_channel_member(bot: Any, channel_username: str, user_id: int) -> bool:
    import logging

    from aiogram.exceptions import TelegramBadRequest

    log = logging.getLogger(__name__)
    ch = channel_username.strip().lstrip("@")
    if not ch:
        return True
    try:
        m = await bot.get_chat_member(chat_id=f"@{ch}", user_id=user_id)
        return m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except TelegramBadRequest as e:
        log.warning("get_chat_member @%s user=%s: %s", ch, user_id, e)
        return False
    except Exception:
        log.exception("get_chat_member @%s user=%s", ch, user_id)
        return False


def _aiogram_main_reply_keyboard(uid: int, settings: Any):
    import bot_aiogram as m

    if settings.bot_mode == "fragment":
        return m.kb_fragment_main(uid=uid, settings=settings)
    return m.kb_v2_main(uid=uid, settings=settings)


class AiogramChannelGateMiddleware(BaseMiddleware):
    """Блокирует сообщения и callback до подписки; обрабатывает «Я подписался — проверить»."""

    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from config import Settings

        settings: Settings = data["settings"]
        ch = settings.required_channel_username
        if not ch:
            return await handler(event, data)

        bot = data["bot"]
        if isinstance(event, Message):
            user = event.from_user
            if not user or user.id in settings.admin_ids:
                return await handler(event, data)
            if await aiogram_user_is_channel_member(bot, ch, user.id):
                return await handler(event, data)
            await event.answer(
                subscription_prompt_html(ch),
                reply_markup=aiogram_subscribe_markup(ch),
                parse_mode="HTML",
            )
            return None

        if isinstance(event, CallbackQuery):
            user = event.from_user
            if not user or user.id in settings.admin_ids:
                return await handler(event, data)
            data_cb = event.data or ""
            if data_cb == SUB_CHECK_CALLBACK:
                ok = await aiogram_user_is_channel_member(bot, ch, user.id)
                if ok:
                    await event.answer("✅ Подписка подтверждена!")
                    assert event.message
                    await event.message.answer(
                        "<b>✅ Канал подписан.</b> Дальше — кнопки меню или <code>/start</code>.",
                        reply_markup=_aiogram_main_reply_keyboard(user.id, settings),
                        parse_mode="HTML",
                    )
                else:
                    await event.answer(
                        "Сначала вступите в канал, затем нажмите снова.",
                        show_alert=True,
                    )
                return None
            if await aiogram_user_is_channel_member(bot, ch, user.id):
                return await handler(event, data)
            await event.answer(
                "Сначала подпишитесь на канал бота.",
                show_alert=True,
            )
            return None

        return await handler(event, data)
