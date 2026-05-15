"""
Фиолетово-зелёная палитра эмодзи для Telegram UI.
Тексты кнопок и сообщений не меняем — только декоративные символы.
"""

from __future__ import annotations

# Основные акценты (фиолетовый/зелёный)
PURPLE = "🟣"
PURPLE_ALT = "🟪"
PURPLE_HEART = "💜"
GREEN = "💚"
GREEN_LEAF = "🌿"
GREEN_DOT = "🟢"

# Анимация «крутки»
SPARKS: tuple[str, ...] = (
    PURPLE_HEART,
    GREEN_LEAF,
    PURPLE,
    GREEN_DOT,
    PURPLE_ALT,
    GREEN,
)
ROLL_STAR_FRAMES: tuple[str, ...] = (PURPLE_HEART, GREEN_DOT, PURPLE_ALT, GREEN_LEAF)

# Статусы
OK = GREEN_DOT
FAIL = PURPLE_HEART
WARN = PURPLE_ALT
GIFT = GREEN_LEAF
LINK = PURPLE
CHART = PURPLE_HEART
LUCK = "🍀"
PLUS = PURPLE_ALT
TOP = GREEN_LEAF
ADMIN = PURPLE
DOCS = GREEN_DOT
SUPPORT = PURPLE_HEART
REF = GREEN_LEAF
VALUATE = PURPLE_HEART
SEARCH = PURPLE_ALT
CABINET = PURPLE_HEART
FOUND = GREEN_LEAF
REFERRAL = GREEN
