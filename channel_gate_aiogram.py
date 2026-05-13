"""Middleware Aiogram: обязательная подписка на канал (зависит от aiogram)."""

from __future__ import annotations

from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

from channel_gate import SUB_CHECK_CALLBACK, subscription_prompt_html
from pending_referral import stash_pending_referrer, take_pending_referrer
from referral_start import referrer_id_from_start_message_text


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


async def aiogram_get_channel_membership(
    bot: Any, channel_username: str, user_id: int
) -> tuple[bool, str | None]:
    """(подписан?, ключ_ошибки). Ключ: bot_cannot_check — бот не админ канала и не видит участников."""
    import logging

    from aiogram.exceptions import TelegramBadRequest

    log = logging.getLogger(__name__)
    ch = channel_username.strip().lstrip("@")
    if not ch:
        return True, None
    try:
        m = await bot.get_chat_member(chat_id=f"@{ch}", user_id=user_id)
        ok = m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
        return ok, None
    except TelegramBadRequest as e:
        raw = str(e).lower()
        log.warning("get_chat_member @%s user=%s: %s", ch, user_id, e)
        if any(
            x in raw
            for x in (
                "not enough rights",
                "chat_admin_required",
                "participant list is inaccessible",
                "need administrator",
            )
        ):
            return False, "bot_cannot_check"
        return False, None
    except Exception:
        log.exception("get_chat_member @%s user=%s", ch, user_id)
        return False, None


async def aiogram_user_is_channel_member(bot: Any, channel_username: str, user_id: int) -> bool:
    ok, _ = await aiogram_get_channel_membership(bot, channel_username, user_id)
    return ok


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

        bot = data.get("bot")
        if bot is None and isinstance(event, (Message, CallbackQuery)):
            bot = event.bot
        if bot is None:
            import logging

            logging.getLogger(__name__).error(
                "AiogramChannelGateMiddleware: нет экземпляра Bot в data/event — проверка канала пропущена."
            )
            return await handler(event, data)
        if isinstance(event, Message):
            user = event.from_user
            if not user or user.id in settings.admin_ids:
                return await handler(event, data)
            ok_msg, gate_err_msg = await aiogram_get_channel_membership(bot, ch, user.id)
            if ok_msg:
                return await handler(event, data)
            if event.chat.type == ChatType.PRIVATE:
                rid = referrer_id_from_start_message_text(event.text)
                if rid is not None:
                    stash_pending_referrer(user.id, rid)
            await event.answer(
                subscription_prompt_html(ch)
                + (
                    "\n\n<i>Если вы уже в канале, а кнопка «Проверить» не помогает — "
                    "бот должен быть администратором канала (напишите владельцу).</i>"
                    if gate_err_msg == "bot_cannot_check"
                    else ""
                ),
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
                ok, gate_err = await aiogram_get_channel_membership(bot, ch, user.id)
                if ok:
                    await event.answer("✅ Подписка подтверждена!")
                    assert event.message
                    db = data.get("db")
                    uid = user.id
                    if db is not None:
                        ref_uid = take_pending_referrer(uid)
                        existed = db.user_exists(uid)
                        db.get_or_create_user(uid)
                        if ref_uid is not None and not existed and db.try_register_referral(
                            referred_user_id=uid,
                            referrer_user_id=ref_uid,
                            bonus_hours=settings.referral_plus_hours,
                        ):
                            try:
                                await bot.send_message(
                                    ref_uid,
                                    "<b>🎁 Реферал!</b> Новый пользователь зашёл по вашей ссылке.\n"
                                    f"Начислено <b>+{settings.referral_plus_hours} ч</b> подписки PLUS.",
                                    parse_mode="HTML",
                                )
                            except Exception:
                                import logging

                                logging.getLogger(__name__).debug(
                                    "referrer notify failed", exc_info=True
                                )
                    else:
                        import logging

                        logging.getLogger(__name__).error(
                            "AiogramChannelGateMiddleware: нет db в data — реферал после подписки не применён."
                        )
                    await event.message.answer(
                        "<b>✅ Канал подписан.</b> Дальше — кнопки меню или <code>/start</code>.",
                        reply_markup=_aiogram_main_reply_keyboard(user.id, settings),
                        parse_mode="HTML",
                    )
                elif gate_err == "bot_cannot_check":
                    await event.answer(
                        "Проверка подписки не настроена: добавьте бота администратором канала.",
                        show_alert=True,
                    )
                else:
                    await event.answer(
                        "Сначала вступите в канал, затем нажмите снова.",
                        show_alert=True,
                    )
                return None
            ok_member, gate_err_cb = await aiogram_get_channel_membership(bot, ch, user.id)
            if ok_member:
                return await handler(event, data)
            if gate_err_cb == "bot_cannot_check":
                await event.answer(
                    "Проверка подписки не настроена: добавьте бота администратором канала.",
                    show_alert=True,
                )
            else:
                await event.answer(
                    "Сначала подпишитесь на канал бота.",
                    show_alert=True,
                )
            return None

        return await handler(event, data)
