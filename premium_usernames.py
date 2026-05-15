"""Узнаваемые @ники с оценкой от ~100 000 TON и пояснением цены."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PremiumUsername:
    min_ton: float
    title_ru: str
    why_price_ru: str


# Минимальная оценка в TON (как на Fragment для топ-брендов)
PREMIUM_USERNAMES: dict[str, PremiumUsername] = {
    "durov": PremiumUsername(
        min_ton=250_000,
        title_ru="Павел Durov — основатель Telegram",
        why_price_ru=(
            "Исторически главный бренд мессенджера. Такие @ники почти не продаются; "
            "цена от 100 000 TON — за статус и узнаваемость, а не за «5 букв»."
        ),
    ),
    "telegram": PremiumUsername(
        min_ton=180_000,
        title_ru="Название мессенджера Telegram",
        why_price_ru=(
            "Прямое совпадение с названием платформы. На Fragment редкость уровня "
            "корпоративного бренда — от 100 000 TON."
        ),
    ),
    "pavel": PremiumUsername(
        min_ton=120_000,
        title_ru="Имя основателя (Pavel)",
        why_price_ru=(
            "Связано с основателем TG; короткое имя с сильным брендом — "
            "типичный диапазон 100 000+ TON."
        ),
    ),
    "ton": PremiumUsername(
        min_ton=150_000,
        title_ru="Блокчейн TON",
        why_price_ru=(
            "Тикер экосистемы Telegram/Fragment. Высокий спрос у коллекционеров — "
            "оценка от 100 000 TON."
        ),
    ),
    "wallet": PremiumUsername(
        min_ton=110_000,
        title_ru="Кошелёк TON / Telegram Wallet",
        why_price_ru=(
            "Ключевое слово крипто-инфраструктуры TG. Короткое словарное слово "
            "премиум-класса на Fragment."
        ),
    ),
    "crypto": PremiumUsername(
        min_ton=100_000,
        title_ru="Крипто-тематика",
        why_price_ru=(
            "Топовое слово нишеи; на аукционах короткие «crypto»-подобные ники "
            "часто стартуют от 100 000 TON."
        ),
    ),
    "bitcoin": PremiumUsername(
        min_ton=130_000,
        title_ru="Главная криптовалюта",
        why_price_ru=(
            "Максимально узнаваемый термин рынка. Длина 7, но бренд перевешивает "
            "обычную шкалу — от 100 000 TON."
        ),
    ),
    "nft": PremiumUsername(
        min_ton=100_000,
        title_ru="NFT / цифровые активы",
        why_price_ru=(
            "Короткий тикер целой индустрии; на Fragment 3-буквенные бренды "
            "оцениваются сотнями тысяч TON."
        ),
    ),
    "ceo": PremiumUsername(
        min_ton=100_000,
        title_ru="CEO / статус руководителя",
        why_price_ru=(
            "Универсальный статусный @ник; редкие 3-символьные и брендовые "
            "имена — премиум-сегмент от 100 000 TON."
        ),
    ),
    "fragment": PremiumUsername(
        min_ton=120_000,
        title_ru="Маркетплейс Fragment",
        why_price_ru=(
            "Официальная площадка продажи @ников в Telegram. Брендовое совпадение "
            "— от 100 000 TON."
        ),
    ),
    "premium": PremiumUsername(
        min_ton=100_000,
        title_ru="Telegram Premium",
        why_price_ru=(
            "Прямая отсылка к подписке Telegram Premium; сильный коммерческий "
            "интерес — от 100 000 TON."
        ),
    ),
    "stickers": PremiumUsername(
        min_ton=100_000,
        title_ru="Стикеры Telegram",
        why_price_ru=(
            "Ключевая функция мессенджера; длинное, но узнаваемое бренд-слово "
            "в премиум-сегменте."
        ),
    ),
    "casino": PremiumUsername(
        min_ton=110_000,
        title_ru="Казино / гемблинг-ниша",
        why_price_ru=(
            "Высокомаржинальная тематика для рекламы; короткие нишевые слова "
            "часто от 100 000 TON."
        ),
    ),
    "admin": PremiumUsername(
        min_ton=100_000,
        title_ru="Администратор / admin",
        why_price_ru=(
            "Универсальное слово для сервисов и ботов; спрос на короткие "
            "ролевые @ники — от 100 000 TON."
        ),
    ),
    "support": PremiumUsername(
        min_ton=100_000,
        title_ru="Служба поддержки",
        why_price_ru=(
            "Идеально для официальных аккаунтов; брендовые сервисные имена "
            "на Fragment — премиум."
        ),
    ),
    "security": PremiumUsername(
        min_ton=100_000,
        title_ru="Безопасность",
        why_price_ru=(
            "Востребовано финтехом и IT; узнаваемое английское слово "
            "в сегменте 100 000+ TON."
        ),
    ),
}


def get_premium_username(username: str) -> PremiumUsername | None:
    u = username.lower().lstrip("@")
    return PREMIUM_USERNAMES.get(u)


def premium_price_usd(username: str, *, ton_to_usd: float) -> tuple[float, str] | None:
    """USD-оценка и текст, если ник в списке премиум."""
    if ton_to_usd <= 0:
        return None
    p = get_premium_username(username)
    if not p:
        return None
    usd = p.min_ton * ton_to_usd
    text = f"{p.title_ru}. {p.why_price_ru} Ориентир: от ~{p.min_ton:,.0f} TON (~${usd:,.0f})."
    return usd, text
