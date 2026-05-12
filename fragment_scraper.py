from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from username_cv import random_cv_username

log = logging.getLogger(__name__)


def random_letters_username_length(length: int) -> str:
    """Случайный ник a–z: чередование согласная–гласная, с первой — согласная."""
    return random_cv_username(length)


def username_listed_on_fragment(
    username: str,
    *,
    timeout_s: int = 25,
    proxies: dict[str, str] | None = None,
) -> bool:
    """
    True, если @username фигурирует на Fragment (продажа, аукцион, «занят», уже куплен и т.п.).

    False, если страница общая (ник не в каталоге Fragment для этого URL) — тогда по Fragment
    «продажи» не числится.
    """
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
        return True

    url = f"https://fragment.com/username/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FragmentUsernameBot/0.2)",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout_s, proxies=proxies)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Fragment запрос для %s: %s", username, e)
        return True

    html = resp.text
    title_m = re.search(r"<title>([^<]+)</title>", html, flags=re.IGNORECASE)
    raw_title = title_m.group(1) if title_m else ""
    title_norm = raw_title.replace("\xa0", " ").strip().lower()

    if title_norm == "fragment":
        return False

    og_m = re.search(r'property="og:description"\s+content="([^"]*)"', html)
    og = (og_m.group(1) if og_m else "").lower()
    html_l = html.lower()

    if "get the username @" in og:
        return True
    if "is taken" in og or "send an offer" in og:
        return True
    if "purchased on" in html_l:
        return True
    if "check the current availability" in og:
        return True
    if "find active auctions for telegram usernames" in og:
        return False

    return True


def collect_usernames_not_on_fragment(
    *,
    length: int,
    max_attempts: int,
    max_found: int,
    proxies: dict[str, str] | None = None,
    timeout_s: int = 25,
    delay_between_requests_s: float = 0.12,
) -> tuple[list[str], int]:
    """
    Подбирает случайные ники из букв a–z заданной длины, пока Fragment не показывает их в продажах.

    Возвращает (список подходящих, число проверок Fragment).
    """
    found: list[str] = []
    attempts = 0
    seen: set[str] = set()
    while attempts < max_attempts and len(found) < max_found:
        cand = random_letters_username_length(length)
        if cand in seen:
            continue
        seen.add(cand)
        attempts += 1
        if not username_listed_on_fragment(cand, timeout_s=timeout_s, proxies=proxies):
            found.append(cand)
        if delay_between_requests_s > 0:
            time.sleep(delay_between_requests_s)
    return found, attempts


@dataclass(frozen=True)
class FragmentGiftPrice:
    username: str
    price_usd: float | None
    source_url: str


_RE_GIFT_SLUG = re.compile(r"/gift/([^/?#]+)", re.IGNORECASE)
_RE_TRAILING_ID = re.compile(r"-(\d+)$")
_RE_USD = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)")
_RE_TON = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*TON\b", re.IGNORECASE)


def _slug_to_username(slug: str) -> str:
    slug = slug.strip()
    # Пример: "plushpepe-84" -> "plushpepe"
    slug = _RE_TRAILING_ID.sub("", slug)
    return slug.lower()


def extract_username_from_url(url: str) -> Optional[str]:
    m = _RE_GIFT_SLUG.search(url)
    if not m:
        return None
    slug = m.group(1)
    username = _slug_to_username(slug)
    return username or None


def extract_price_usd_from_html(html: str) -> float | None:
    # MVP: самый простой извлекатель, берём максимальную $-цену на странице.
    prices: list[float] = []
    for m in _RE_USD.finditer(html):
        raw = m.group(1).replace(",", "")
        try:
            prices.append(float(raw))
        except ValueError:
            continue
    if not prices:
        return None
    return max(prices)


def extract_price_ton_from_html(html: str) -> float | None:
    tons: list[float] = []
    for m in _RE_TON.finditer(html):
        raw = m.group(1).replace(",", "")
        try:
            tons.append(float(raw))
        except ValueError:
            continue
    if not tons:
        return None
    return max(tons)


def extract_best_price_usd(html: str, *, ton_to_usd: float) -> float | None:
    usd = extract_price_usd_from_html(html)
    if usd is not None:
        return usd
    ton = extract_price_ton_from_html(html)
    if ton is None:
        return None
    return ton * ton_to_usd


def fetch_fragment_gift_price(
    url: str,
    *,
    timeout_s: int = 20,
    ton_to_usd: float = 2.0,
    proxies: dict[str, str] | None = None,
) -> FragmentGiftPrice:
    # NOTE: Fragment может менять HTML. В MVP делаем "best-effort" с fallback.
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Fragment URL должен начинаться с http/https")

    username = extract_username_from_url(url)
    if not username:
        raise ValueError("Не удалось извлечь username из URL (ожидался /gift/<slug>)")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FragmentUsernameBot/0.1)",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    resp = requests.get(url, headers=headers, timeout=timeout_s, proxies=proxies)
    resp.raise_for_status()
    html = resp.text

    # Пробуем сначала через regex (быстрее), параллельно можно не парсить DOM.
    price_usd = extract_best_price_usd(html, ton_to_usd=ton_to_usd)
    if price_usd is not None:
        return FragmentGiftPrice(username=username, price_usd=price_usd, source_url=url)

    # Fallback: попробуем вытянуть text и снова поискать.
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    price_usd = extract_best_price_usd(text, ton_to_usd=ton_to_usd)
    return FragmentGiftPrice(username=username, price_usd=price_usd, source_url=url)

