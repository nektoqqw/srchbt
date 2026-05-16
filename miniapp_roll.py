"""Подбор @username для Mini App (без edit_message в Telegram)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from fragment_scraper import username_listed_on_fragment
from roll_filters import RollFilters, generate_roll_candidate

log = logging.getLogger(__name__)

FRAGMENT_USERNAME_SEARCH_WALL_S = 180


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
    on_progress: Callable[[int], Awaitable[None]] | None = None,
) -> tuple[str | None, int, bool]:
    """(username | None, attempts, timed_out)."""
    lucky_effective = bool(lucky) and dictionary_length not in (5, 6, 7)
    retry_sleep = min(delay_s, 0.04) if delay_s > 0 else 0.0
    seen: set[str] = set()
    attempts = 0
    last_progress = 0.0
    deadline = time.monotonic() + float(FRAGMENT_USERNAME_SEARCH_WALL_S)
    uses_th = getattr(checker, "uses_telethon", False)

    while attempts < max_attempts:
        if time.monotonic() >= deadline:
            return None, attempts, True

        cand = generate_roll_candidate(
            length,
            lucky=lucky_effective,
            filters=filters,
            plus_full_cv=is_plus,
            dictionary_length=dictionary_length,
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
