#!/usr/bin/env python3
"""Однократный вход второго аккаунта Telethon для Mini App (отдельный .session)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from checker import build_telethon_checker
from config import load_settings


async def main() -> None:
    settings = load_settings()
    checker = build_telethon_checker(
        settings, session_name=settings.telethon_session_miniapp
    )
    if not getattr(checker, "uses_telethon", False):
        print(
            "Telethon недоступен: TELEGRAM_API_ID/HASH и USERNAME_CHECK_MODE не disabled."
        )
        sys.exit(1)
    print(f"Сессия: {settings.telethon_session_miniapp}.session")
    print("Введите телефон и код второго аккаунта (не тот, что у бота).")
    await checker.start()
    print("OK — авторизация сохранена.")
    await checker.stop()


if __name__ == "__main__":
    asyncio.run(main())
