"""Клиент Telethon для проверки свободности Telegram username.

Мы считаем username "валидным для установки", если он проходит MTProto
проверку `account.checkUsername` и возвращается статус acceptable/True.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import string
from typing import ClassVar

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.network.connection import (
    ConnectionTcpAbridged,
    ConnectionTcpFull,
    ConnectionTcpIntermediate,
    ConnectionTcpObfuscated,
)
from telethon.tl.functions.account import CheckUsernameRequest

from username_cv import random_cv_username

log = logging.getLogger(__name__)


def telethon_connection_class(name: str) -> type:
    """Режим TCP для Telethon (иногда obfuscated помогает за фильтрами)."""
    m: dict[str, type] = {
        "full": ConnectionTcpFull,
        "obfuscated": ConnectionTcpObfuscated,
        "intermediate": ConnectionTcpIntermediate,
        "abridged": ConnectionTcpAbridged,
    }
    return m.get(name.lower().strip(), ConnectionTcpFull)


# Допустимые символы для «простых» ников в пробной версии: только буквы a-z
_LETTERS = string.ascii_lowercase
_USERNAME_RE = re.compile(r"^[a-z0-9_]{5,32}$")


def random_letters_username(length: int) -> str:
    """Генерация простых кандидатов: a–z, чередование согласная–гласная (С с позиции 0)."""
    return random_cv_username(length)


def normalize_username(name: str) -> str:
    """Нормализуем ввод: убираем '@' и приводим к нижнему регистру."""
    name = name.strip()
    if name.startswith("@"):
        name = name[1:]
    return name.lower()


def is_valid_telegram_username(name: str) -> bool:
    return bool(_USERNAME_RE.match(normalize_username(name)))


def is_valid_telegram_username_for_roll(name: str, *, min_len: int = 5, max_len: int = 6) -> bool:
    """Ограничение под ролл v0: только латинские буквы, длина 5-6."""
    name = normalize_username(name)
    return bool(re.match(r"^[a-z]{%d,%d}$" % (min_len, max_len), name))


class UsernameChecker:
    """Проверка через MTProto ``account.checkUsername`` (нужен аккаунт + Telethon)."""

    uses_telethon: ClassVar[bool] = True

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str,
        delay_between_checks: float = 0.4,
        *,
        timeout: int = 120,
        connection_retries: int = 10,
        connection: type | None = None,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name
        self._timeout = timeout
        self._retries = connection_retries
        conn = connection or ConnectionTcpFull
        self._client = TelegramClient(
            session_name,
            api_id,
            api_hash,
            proxy=None,
            timeout=timeout,
            connection_retries=connection_retries,
            connection=conn,
        )
        self._delay = delay_between_checks
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        try:
            assert self._client is not None
            await self._client.connect()
        except (OSError, ConnectionError, ConnectionResetError, EOFError):
            raise

        assert self._client is not None
        if not await self._client.is_user_authorized():
            await self._client.start()
        self._started = True
        log.info("Telethon: подключено и авторизовано")

    async def stop(self) -> None:
        if self._client is not None:
            try:
                if self._client.is_connected():
                    await self._client.disconnect()
            except Exception:
                pass
        self._started = False

    async def is_available(self, username: str) -> bool | None:
        username = normalize_username(username)
        if not is_valid_telegram_username(username):
            return False
        await self.start()
        async with self._lock:
            await asyncio.sleep(self._delay)
            try:
                result = await self._client(CheckUsernameRequest(username=username))
            except FloodWaitError as e:
                log.warning("FloodWait %s с — ждём", e.seconds)
                await asyncio.sleep(int(e.seconds) + 1)
                result = await self._client(CheckUsernameRequest(username=username))
            except RPCError as e:
                log.warning("RPC при проверке %s: %s", username, e)
                return False

        return _interpret_check_username_result(result)


class DisabledUsernameChecker:
    """Режим без Telethon: занятость username в Telegram не проверяется (Bot API этого не умеет)."""

    uses_telethon: ClassVar[bool] = False

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def is_available(self, username: str) -> bool | None:
        return None


def _interpret_check_username_result(result: object) -> bool:
    """Совместимость с разными версиями API (bool или UsernameStatus*)."""
    if isinstance(result, bool):
        return result
    cls = result.__class__.__name__
    if cls == "UsernameStatusAcceptable":
        return True
    if cls in (
        "UsernameStatusInvalid",
        "UsernameStatusOccupied",
        "UsernameStatusUnacceptable",
        "UsernameStatusModified",
    ):
        return False
    # Неизвестный тип — консервативно «занят»
    log.warning("Неизвестный ответ CheckUsername: %s", cls)
    return False


async def find_available_batch(
    checker: UsernameChecker,
    *,
    length: int,
    max_attempts: int,
    max_found: int,
) -> tuple[list[str], int]:
    """
    Перебирает случайные ники заданной длины.
    Возвращает (список свободных, число выполненных проверок).
    """
    found: list[str] = []
    attempts = 0
    seen: set[str] = set()
    while attempts < max_attempts and len(found) < max_found:
        candidate = random_letters_username(length)
        if candidate in seen:
            continue
        seen.add(candidate)
        attempts += 1
        try:
            if await checker.is_available(candidate) is True:
                found.append(candidate)
        except Exception:
            log.exception("Ошибка проверки %s", candidate)
    return found, attempts
