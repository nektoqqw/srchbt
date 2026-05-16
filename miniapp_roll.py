"""Подбор @username для Mini App (без edit_message в Telegram)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from admin_dict_roll import admin_dict_roll_get
from english_dictionary import dictionary_words_for_roll
from fragment_scraper import username_listed_on_fragment
from roll_filters import RollFilters, generate_roll_candidate

log = logging.getLogger(__name__)

FRAGMENT_USERNAME_SEARCH_WALL_S = 180
# Словарный режим: больше времени — слов много, свободных мало.
DICT_ROLL_SEARCH_WALL_S = 600


async def find_one_username_fragment_miniapp(
    *,
    length: int,
    max_attempts: int,
    delay_s: float,
    lucky: bool,
    checker: Any,
    filters: RollFilters,
    fragment_timeout_s: int,
    is_plus: bool,
    dictionary_length: int | None = None,
    dict_popular_first: bool = True,
    dict_roll_uid: int | None = None,
    on_progress: Callable[[int], Awaitable[None]] | None = None,
) -> tuple[str | None, int, bool]:
    """(username | None, attempts, timed_out)."""
    dict_mode = dictionary_length in (5, 6, 7)
    lucky_effective = bool(lucky) and not dict_mode
    retry_sleep = min(delay_s, 0.04) if delay_s > 0 else 0.0
    seen: set[str] = set()
    attempts = 0
    last_progress = 0.0
    wall_s = DICT_ROLL_SEARCH_WALL_S if dict_mode else FRAGMENT_USERNAME_SEARCH_WALL_S
    deadline = time.monotonic() + float(wall_s)
    uses_th = getattr(checker, "uses_telethon", False)

    dict_pool: list[str] = []
    dict_i = 0
    if dict_mode:
        popular_first = dict_popular_first
        if dict_roll_uid is not None:
            popular_first = admin_dict_roll_get(dict_roll_uid).popular_first
        dict_pool = dictionary_words_for_roll(
            dictionary_length, popular_first=popular_first
        )
        max_attempts = min(max_attempts, max(len(dict_pool), 1))

    while attempts < max_attempts:
        if time.monotonic() >= deadline:
            return None, attempts, True

        if dict_mode:
            if dict_i >= len(dict_pool):
                return None, attempts, False
            cand = dict_pool[dict_i]
            dict_i += 1
        else:
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
        if on_progress and (attempts == 1 or now - last_progress >= 0.35):
            last_progress = now
            try:
                await on_progress(attempts)
            except Exception:
                pass

        try:
            listed = await asyncio.to_thread(
                username_listed_on_fragment,
                cand,
                timeout_s=fragment_timeout_s,
            )
        except Exception:
            log.exception("miniapp fragment check %s", cand)
            listed = True

        if not listed:
            if uses_th:
                try:
                    avail = await checker.is_available(cand)
                except Exception:
                    log.exception("miniapp telethon check %s", cand)
                    avail = False
                if avail is not True:
                    continue
            return cand, attempts, False

        if retry_sleep > 0:
            await asyncio.sleep(retry_sleep)

    return None, attempts, False
