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
from asyncio import IncompleteReadError
from typing import Any, ClassVar

from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.errors import FloodWaitError, RPCError
from telethon.network.connection import (
    ConnectionTcpAbridged,
    ConnectionTcpFull,
    ConnectionTcpIntermediate,
    ConnectionTcpMTProxyAbridged,
    ConnectionTcpMTProxyIntermediate,
    ConnectionTcpMTProxyRandomizedIntermediate,
    ConnectionTcpObfuscated,
)
from telethon.tl.functions.account import CheckUsernameRequest

from proxy_config import MtProxySettings
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


def mtproxy_telethon_connection_class(name: str) -> type:
    """Режим TCP к MTProxy (секрет dd/ee и серверы по-разному поддерживают классы)."""
    m: dict[str, type] = {
        "randomized": ConnectionTcpMTProxyRandomizedIntermediate,
        "intermediate": ConnectionTcpMTProxyIntermediate,
        "abridged": ConnectionTcpMTProxyAbridged,
    }
    return m.get(name.lower().strip(), ConnectionTcpMTProxyRandomizedIntermediate)


async def telethon_graceful_disconnect(client: TelegramClient | None) -> None:
    """Отпустить TCP и закрыть SQLite-сессию (иначе при быстром пересоздании клиента — database is locked)."""
    if client is None:
        return
    try:
        if client.is_connected():
            await client.disconnect()
    except Exception:
        pass
    try:
        sess = getattr(client, "session", None)
        if sess is not None and hasattr(sess, "close"):
            sess.close()
    except Exception:
        pass
    await asyncio.sleep(0.6)


def iter_mtproxy_connection_types(primary: str) -> list[type]:
    """
    Порядок попыток к MTProxy.

    Ссылки ``tg://proxy`` из Telegram почти всегда рассчитаны на **Randomized** intermediate;
    если первым взять plain **Intermediate**, Telethon может получить мусор в длине пакета
    (``readexactly size can not be less than zero``). Поэтому **Randomized всегда первый**,
    затем значение из ``MTPROXY_TELETHON_CONNECTION``, затем оставшиеся режимы.
    """
    preferred = mtproxy_telethon_connection_class(primary)
    order = (
        ConnectionTcpMTProxyRandomizedIntermediate,
        preferred,
        ConnectionTcpMTProxyIntermediate,
        ConnectionTcpMTProxyAbridged,
    )
    out: list[type] = []
    seen: set[type] = set()
    for t in order:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


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
        proxy: dict[str, Any] | None = None,
        *,
        mtproxy: MtProxySettings | None = None,
        mtproxy_tcp_mode: str = "randomized",
        timeout: int = 120,
        connection_retries: int = 10,
        connection: type | None = None,
    ) -> None:
        self._mtproxy = mtproxy
        self._mtp_tcp_mode = (mtproxy_tcp_mode or "randomized").strip().lower()
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name
        self._timeout = timeout
        self._retries = connection_retries
        if mtproxy is not None:
            self._client = None
            self._proxy = {
                "kind": "mtproxy",
                "addr": mtproxy.host,
                "port": mtproxy.port,
            }
        else:
            conn = connection or ConnectionTcpFull
            self._client = TelegramClient(
                session_name,
                api_id,
                api_hash,
                proxy=proxy,
                timeout=timeout,
                connection_retries=connection_retries,
                connection=conn,
            )
            self._proxy = proxy
        self._delay = delay_between_checks
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        if self._mtproxy is not None:
            mp = self._mtproxy
            last: Exception | None = None
            winning_cls: type | None = None
            for conn_cls in iter_mtproxy_connection_types(self._mtp_tcp_mode):
                probe = TelegramClient(
                    MemorySession(),
                    self._api_id,
                    self._api_hash,
                    connection=conn_cls,
                    proxy=(mp.host, mp.port, mp.secret),
                    timeout=self._timeout,
                    connection_retries=self._retries,
                )
                try:
                    await probe.connect()
                    winning_cls = conn_cls
                    log.info("Telethon MTProxy: тест TCP %s прошёл (MemorySession)", conn_cls.__name__)
                    break
                except (
                    OSError,
                    ConnectionError,
                    IncompleteReadError,
                    ConnectionResetError,
                    EOFError,
                    ValueError,
                ) as e:
                    last = e
                    log.warning(
                        "MTProxy TCP %s: тест не прошёл (%s), следующий режим",
                        conn_cls.__name__,
                        e,
                    )
                finally:
                    await telethon_graceful_disconnect(probe)
            else:
                raise RuntimeError(
                    "Telethon: MTProxy на всех режимах TCP (randomized / intermediate / abridged) "
                    "не принял соединение. Проверьте tg://proxy в официальном Telegram и секрет."
                ) from last

            assert winning_cls is not None
            await telethon_graceful_disconnect(self._client)
            self._client = TelegramClient(
                self._session_name,
                self._api_id,
                self._api_hash,
                connection=winning_cls,
                proxy=(mp.host, mp.port, mp.secret),
                timeout=self._timeout,
                connection_retries=self._retries,
            )
            await self._client.connect()
            log.info("Telethon MTProxy: основная сессия %s (TCP %s)", self._session_name, winning_cls.__name__)
        else:
            try:
                assert self._client is not None
                await self._client.connect()
            except (OSError, ConnectionError, IncompleteReadError, ConnectionResetError, EOFError):
                raise

        assert self._client is not None
        if not await self._client.is_user_authorized():
            await self._client.start()
        self._started = True
        if self._proxy:
            if self._proxy.get("kind") == "mtproxy":
                log.info(
                    "Telethon: подключено и авторизовано (MTProxy %s:%s)",
                    self._proxy.get("addr"),
                    self._proxy.get("port"),
                )
            else:
                log.info(
                    "Telethon: подключено и авторизовано (прокси %s %s:%s, rdns=%s)",
                    self._proxy.get("proxy_type"),
                    self._proxy.get("addr"),
                    self._proxy.get("port"),
                    self._proxy.get("rdns"),
                )
        else:
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
