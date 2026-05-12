"""Парсинг настроек прокси из .env для Telethon, requests и python-telegram-bot."""

from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, quote, unquote, urlparse

_LOGICAL_SPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(frozen=True)
class MtProxySettings:
    """MTProto-прокси (MTProxy): хост, порт и секрет (hex, base64 или base64url из tg://proxy / t.me/proxy)."""

    host: str
    port: int
    secret: str


@dataclass(frozen=True)
class ProxySettings:
    """Единая конфигурация для всех исходящих соединений бота."""

    # URL для httpx (python-telegram-bot): socks5h://... или http://...
    httpx_proxy_url: str
    # dict для requests.get(..., proxies=...)
    requests_proxies: dict[str, str]
    # dict для Telethon TelegramClient(..., proxy=...) — без модуля PySocks / socks
    telethon_proxy: dict[str, Any]


def _scheme_for_requests(scheme: str) -> str:
    """Для DNS через прокси у SOCKS используем socks5h (рекомендуется для HTTPS)."""
    s = scheme.lower().strip()
    if s in ("socks5", "socks"):
        return "socks5h"
    if s == "socks5h":
        return "socks5h"
    if s in ("http", "https"):
        return "http"
    raise ValueError(f"Неподдерживаемый PROXY_TYPE: {scheme!r}. Используйте socks5, socks5h, http или https.")


def _telethon_proxy_type(scheme: str) -> str:
    s = scheme.lower().strip()
    if s in ("http", "https"):
        return "http"
    if s in ("socks5", "socks5h", "socks"):
        return "socks5"
    raise ValueError(f"Неподдерживаемый тип прокси для Telethon: {scheme!r}")


def _build_urls(
    *,
    scheme: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
) -> tuple[str, dict[str, str]]:
    req_scheme = _scheme_for_requests(scheme)
    user = username or ""
    pwd = password or ""

    if user or pwd:
        u = quote(user, safe="")
        p = quote(pwd, safe="")
        url = f"{req_scheme}://{u}:{p}@{host}:{port}"
    else:
        url = f"{req_scheme}://{host}:{port}"

    proxies = {"http": url, "https": url}
    return url, proxies


def _resolve_rdns_for_scheme(scheme: str) -> bool:
    """
    rdns=True — резолвить имена через DNS прокси (часто нужно для SOCKS).
    Для HTTP/HTTPS CONNECT к дата-центрам Telegram чаще стабильнее rdns=False
    (резолв на клиенте). Переопределение: PROXY_RDNS=true|false
    """
    raw = (os.environ.get("PROXY_RDNS") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    s = scheme.lower().strip()
    if s in ("http", "https"):
        return False
    return True


def _build_telethon_proxy(
    *,
    scheme: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    rdns: bool,
) -> dict[str, Any]:
    """Формат из документации Telethon (dict), без зависимости от пакета PySocks."""
    ptype = _telethon_proxy_type(scheme)
    d: dict[str, Any] = {
        "proxy_type": ptype,
        "addr": host,
        "port": port,
        "rdns": rdns,
    }
    if username:
        d["username"] = username
        d["password"] = password or ""
    return d


def _parse_host_port(token: str) -> tuple[str, int]:
    token = token.strip()
    if ":" not in token:
        raise ValueError("В PROXY ожидается host:port (например 192.168.0.1:1080).")
    host, _, port_s = token.rpartition(":")
    host = host.strip()
    port_s = port_s.strip()
    if not host or not port_s.isdigit():
        raise ValueError(f"Некорректный host:port: {token!r}")
    port = int(port_s)
    if not (1 <= port <= 65535):
        raise ValueError(f"Некорректный порт: {port}")
    return host, port


def _normalize_mtproxy_secret(raw: str) -> str:
    """
    Должен совпадать с тем, что потом разберёт Telethon ``TcpMTProxy.normalize_secret``:
    опционально префикс ``ee``/``dd`` (регистр не важен — в библиотеке проверка только на нижний),
    дальше либо hex, либо base64.
    """
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        raise ValueError("MTPROXY secret пустой.")
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if not s:
        raise ValueError("MTPROXY secret пустой.")

    # Telethon сравнивает префикс только с "ee" / "dd" (нижний регистр).
    if len(s) >= 2 and s[:2].upper() in ("EE", "DD"):
        s = s[:2].lower() + s[2:]

    rest = s[2:] if len(s) >= 2 and s[:2] in ("ee", "dd") else s
    if rest and re.fullmatch(r"[0-9A-Fa-f]+", rest):
        if len(rest) % 2:
            raise ValueError(
                "MTPROXY secret: после префикса ee/dd должно быть чётное число hex-символов."
            )
        return s

    try:
        padded = s + "=" * (-len(s) % 4)
        base64.b64decode(padded.encode("ascii"), validate=True)
        return s
    except (ValueError, binascii.Error, TypeError):
        pass

    # В tg://proxy секрет часто в «URL-safe» base64 (- и _ вместо + и /). Telethon декодирует только обычный b64.
    std = s.translate(str.maketrans("-_", "+/"))
    try:
        padded = std + "=" * (-len(std) % 4)
        base64.b64decode(padded.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, TypeError) as e:
        raise ValueError(
            "MTPROXY secret: нужен hex, base64 или base64url (как в tg://proxy). "
            "Уберите кавычки и лишние пробелы; не обрезайте строку."
        ) from e
    return std


def _parse_mtproxy_from_env_link(raw: str) -> MtProxySettings | None:
    """
    Полная ссылка из Telegram: ``tg://proxy?server=...&port=...&secret=...``
    или ``https://t.me/proxy?server=...`` (и аналоги).
    """
    raw = raw.strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("tg://proxy"):
        u = urlparse(raw)
        if u.netloc.lower() != "proxy":
            return None
        qs = parse_qs(u.query, keep_blank_values=False)
    elif low.startswith(("http://", "https://")):
        u = urlparse(raw)
        host_key = (u.hostname or "").lower()
        if host_key not in ("t.me", "telegram.me", "telegram.dog"):
            return None
        if not u.path.rstrip("/").endswith("proxy"):
            return None
        qs = parse_qs(u.query, keep_blank_values=False)
    else:
        return None

    def first(name: str) -> str | None:
        for key in (name, name.upper()):
            vals = qs.get(key)
            if vals and vals[0]:
                return unquote(vals[0].strip())
        return None

    server = first("server")
    port_raw = first("port")
    secret_raw = first("secret")
    if not server or not port_raw or not secret_raw:
        raise ValueError(
            "В ссылке tg://proxy / t.me/proxy должны быть параметры server, port и secret."
        )
    if not port_raw.isdigit():
        raise ValueError("В ссылке proxy некорректный port.")
    return MtProxySettings(server, int(port_raw), _normalize_mtproxy_secret(secret_raw))


def load_mtproxy_settings() -> MtProxySettings | None:
    """
    MTProxy для Telethon (отдельно от HTTP/SOCKS ``PROXY``).

    Вариант A — целиком ссылка в ``MTPROXY`` / ``MT_PROXY``::

        tg://proxy?server=1.2.3.4&port=443&secret=...

    или ``https://t.me/proxy?server=...&port=...&secret=...``

    Вариант B — три переменные:
    - ``MTPROXY_HOST``
    - ``MTPROXY_PORT``
    - ``MTPROXY_SECRET`` (hex, base64 или base64url)

    Вариант C — одна строка ``MTPROXY``::
        host:port dd0123abcd...
    (один пробел между host:port и секретом)
    """
    for key in ("MTPROXY", "MT_PROXY"):
        link = (os.environ.get(key) or "").strip()
        if link:
            from_link = _parse_mtproxy_from_env_link(link)
            if from_link is not None:
                return from_link

    host = (os.environ.get("MTPROXY_HOST") or "").strip()
    port_s = (os.environ.get("MTPROXY_PORT") or "").strip()
    sec_raw = (os.environ.get("MTPROXY_SECRET") or "").strip()

    if host and port_s and sec_raw:
        if not port_s.isdigit():
            raise ValueError("MTPROXY_PORT должен быть числом.")
        return MtProxySettings(host, int(port_s), _normalize_mtproxy_secret(sec_raw))

    line = (os.environ.get("MTPROXY") or os.environ.get("MT_PROXY") or "").strip()
    if not line:
        return None

    line = _LOGICAL_SPACE_RE.sub(" ", line).strip()
    parts = line.split(" ", 1)
    if len(parts) != 2:
        raise ValueError(
            'MTPROXY: ожидается строка вида «host:port секрет_hex» '
            "или задайте MTPROXY_HOST, MTPROXY_PORT, MTPROXY_SECRET."
        )
    hp, sec_raw = parts[0].strip(), parts[1].strip()
    h, p = _parse_host_port(hp)
    return MtProxySettings(h, p, _normalize_mtproxy_secret(sec_raw))


def _parse_login_password(cred: str) -> tuple[str | None, str | None]:
    """
    Поддерживаемые форматы cred (всё после host:port через пробел):

    - ``user:password``
    - ``user password`` (один пробел между логином и паролем — как у многих провайдеров)
    """
    cred = cred.strip()
    if not cred:
        return None, None
    if ":" in cred:
        user, pwd = cred.split(":", 1)
        user, pwd = user.strip(), pwd.strip()
        return user or None, pwd or None
    if " " in cred:
        user, pwd = cred.split(" ", 1)
        user, pwd = user.strip(), pwd.strip()
        return user or None, pwd or None
    return cred, None


def load_proxy_settings() -> ProxySettings | None:
    """
    Читает переменные окружения:

    - PROXY_URL — полный URL, например socks5://user:pass@1.2.3.4:1080 или http://...
    - PROXY — одна строка: ``host:port user:password`` (пробел между host:port и логином)
    - PROXY_TYPE — socks5 | socks5h | http | https (по умолчанию socks5; для PROXY_URL схема из URL имеет приоритет).
      «HTTPS-прокси» у провайдеров обычно = HTTP CONNECT: укажите ``http`` или ``https`` (оба дадут ``http://...`` для клиентов).
    - PROXY_RDNS — ``true`` / ``false`` (опционально). Если не задано: для http/https по умолчанию ``false``,
      для socks — ``true``. При таймаутах Telethon через HTTP-прокси попробуйте явно ``PROXY_RDNS=false``.

    Для SOCKS в ``requests`` нужен пакет PySocks (см. requirements.txt). Telethon при dict-прокси SOCKS может
    подтянуть зависимости сам; для HTTP-прокси PySocks не обязателен.
    """
    raw_url = (os.environ.get("PROXY_URL") or "").strip()
    raw_line = (os.environ.get("PROXY") or "").strip()
    proxy_type = (os.environ.get("PROXY_TYPE") or "socks5").strip().lower()

    if raw_url:
        from urllib.parse import urlparse

        parsed = urlparse(raw_url)
        if parsed.scheme not in ("socks5", "socks5h", "socks4", "http", "https"):
            raise ValueError(
                f"PROXY_URL: неподдерживаемая схема {parsed.scheme!r}. "
                "Используйте socks5, socks5h, http или https."
            )
        scheme = parsed.scheme
        if not parsed.hostname or parsed.port is None:
            raise ValueError("PROXY_URL: нужны host и port в URL.")
        host = parsed.hostname
        port = int(parsed.port)
        user = unquote(parsed.username) if parsed.username else None
        pwd = unquote(parsed.password) if parsed.password else None
        httpx_url, req_proxies = _build_urls(scheme=scheme, host=host, port=port, username=user, password=pwd)
        rdns = _resolve_rdns_for_scheme(scheme)
        telethon_proxy = _build_telethon_proxy(
            scheme=scheme,
            host=host,
            port=port,
            username=user,
            password=pwd,
            rdns=rdns,
        )
        return ProxySettings(
            httpx_proxy_url=httpx_url,
            requests_proxies=req_proxies,
            telethon_proxy=telethon_proxy,
        )

    if not raw_line:
        return None

    line = _LOGICAL_SPACE_RE.sub(" ", raw_line).strip()
    parts = line.split(" ", 1)
    hostport = parts[0].strip()
    cred_part = parts[1].strip() if len(parts) > 1 else ""

    host, port = _parse_host_port(hostport)
    user, pwd = _parse_login_password(cred_part)

    httpx_url, req_proxies = _build_urls(
        scheme=proxy_type,
        host=host,
        port=port,
        username=user,
        password=pwd,
    )
    rdns = _resolve_rdns_for_scheme(proxy_type)
    telethon_proxy = _build_telethon_proxy(
        scheme=proxy_type,
        host=host,
        port=port,
        username=user,
        password=pwd,
        rdns=rdns,
    )
    return ProxySettings(
        httpx_proxy_url=httpx_url,
        requests_proxies=req_proxies,
        telethon_proxy=telethon_proxy,
    )
