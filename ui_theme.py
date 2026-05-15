"""
Фиолетово-зелёная палитра эмодзи для Telegram UI.
Тексты кнопок и сообщений не меняем — только декоративные символы.
"""

from __future__ import annotations

# Основные акценты
PURPLE = "🟣"
GREEN = "💚"
PURPLE_ALT = "🟪"

# Анимация «крутки»
SPARKS: tuple[str, ...] = (PURPLE, GREEN, PURPLE_ALT, GREEN, PURPLE)
ROLL_STAR_FRAMES: tuple[str, ...] = (PURPLE, GREEN, PURPLE_ALT, GREEN)

# Статусы
OK = GREEN
FAIL = PURPLE
WARN = PURPLE_ALT
GIFT = GREEN
LINK = PURPLE
CHART = PURPLE
LUCK = GREEN
PLUS = PURPLE
TOP = GREEN
ADMIN = PURPLE
DOCS = GREEN
SUPPORT = PURPLE
REF = GREEN
VALUATE = GREEN
SEARCH = PURPLE
CABINET = PURPLE
FOUND = GREEN
REFERRAL = GREEN
