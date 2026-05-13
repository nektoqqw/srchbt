"""Загрузка настроек из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REQUIRED_CHANNEL_DEFAULT = "amnyam_search"


def parse_required_channel_username(raw: str | None) -> str:
    """Пустая строка / off / 0 — отключить проверку (для разработки)."""
    if raw is None:
        return REQUIRED_CHANNEL_DEFAULT
    s = raw.strip().lstrip("@")
    if not s or s.lower() in ("0", "off", "false", "none", "disable", "disabled"):
        return ""
    return s


def _parse_admin_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Settings:
    bot_token: str
    api_id: int
    api_hash: str
    telethon_session: str
    plus_promo_code: str
    luck_promo_code: str
    admin_ids: set[int]
    db_path: Path
    free_search_limit: int = 4
    ton_to_usd: float = 2.0
    telethon_timeout: int = 120
    telethon_connection_retries: int = 10
    # telethon | disabled — см. USERNAME_CHECK_MODE
    username_check_mode: str = "telethon"
    # True — бот к Telegram только через Telethon (MTProto), без long polling Bot API
    use_mtproto_bot: bool = False
    # full | obfuscated | intermediate | abridged — см. TELETHON_CONNECTION
    telethon_connection: str = "full"
    # fragment — только BOT_TOKEN + проверка ников через fragment.com (без Telethon)
    # telethon — проверка через MTProto (нужны TELEGRAM_API_ID / TELEGRAM_API_HASH)
    bot_mode: str = "fragment"
    # пауза между HTTP-запросами к Fragment (сек.); 0 = без паузы (риск лимитов у Fragment)
    fragment_request_delay_s: float = 0.07
    # таймаут HTTP к Fragment на один запрос подбора (сек.)
    fragment_roll_timeout_s: int = 12
    # пауза после account.checkUsername в Telethon (сек.); меньше — быстрее, выше риск FloodWait
    telethon_check_delay_s: float = 0.10
    # HTML-фрагмент: реквизиты / как оплатить PLUS (подставляется под выбранный тариф)
    plus_payment_hint: str = ""
    # HTML-фрагмент: оплата режима «Удача» (отдельно от PLUS)
    luck_payment_hint: str = ""
    # глобальный лимит активаций LUCK_PROMO_CODE (0 = без лимита)
    luck_promo_max_uses: int = 0
    # часы PLUS за одного нового пользователя, пришедшего по реф. ссылке
    referral_plus_hours: int = 1
    # без @ — если задан, реф. ссылка строится от него (запас, если Bot API не отдаёт username)
    bot_username_for_links: str = ""
    # без @; пусто — не требовать канал. Бот должен быть админом канала, иначе getChatMember не сработает.
    required_channel_username: str = "amnyam_search"
    # Platega.io: если заданы MERCHANT_ID и SECRET — при выборе тарифа создаётся платёж и кнопка «Оплатить»
    platega_merchant_id: str = ""
    platega_secret: str = ""
    platega_api_base: str = "https://app.platega.io"
    platega_payment_method: int = 2
    # полные URL после оплаты (если пусто — подставляется https://t.me/<бот>?start=platega_ok)
    platega_return_url: str = ""
    platega_failed_url: str = ""


def load_settings() -> Settings:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Укажите BOT_TOKEN в .env")

    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()

    explicit_mode = (os.environ.get("BOT_MODE") or "").strip().lower()
    if explicit_mode in ("fragment", "telethon"):
        bot_mode = explicit_mode
    elif not api_id_raw or not api_hash:
        bot_mode = "fragment"
    else:
        bot_mode = "telethon"

    if bot_mode == "telethon" and (not api_id_raw or not api_hash.strip()):
        raise RuntimeError("BOT_MODE=telethon: укажите TELEGRAM_API_ID и TELEGRAM_API_HASH (my.telegram.org)")

    session = os.environ.get("TELETHON_SESSION_NAME", "username_checker").strip()
    promo = os.environ.get("PLUS_PROMO_CODE", "DEMOPLUS2026").strip()
    luck_promo = os.environ.get("LUCK_PROMO_CODE", "").strip()
    admins = _parse_admin_ids(os.environ.get("ADMIN_IDS"))
    admin_single = (os.environ.get("ADMIN_TELEGRAM_ID") or "").strip()
    if admin_single.isdigit():
        admins = set(admins) | {int(admin_single)}

    db = Path(os.environ.get("BOT_DB_PATH", "bot_data.sqlite"))
    ton_to_usd_raw = os.environ.get("TON_TO_USD", "2.0").strip()
    try:
        ton_to_usd = float(ton_to_usd_raw)
    except ValueError:
        ton_to_usd = 2.0

    try:
        telethon_timeout = int((os.environ.get("TELETHON_TIMEOUT") or "120").strip())
    except ValueError:
        telethon_timeout = 120
    try:
        telethon_connection_retries = int((os.environ.get("TELETHON_CONNECTION_RETRIES") or "10").strip())
    except ValueError:
        telethon_connection_retries = 10

    username_check_mode = (os.environ.get("USERNAME_CHECK_MODE") or "telethon").strip().lower()
    if username_check_mode not in ("telethon", "disabled"):
        username_check_mode = "telethon"

    mtbot_raw = (os.environ.get("USE_MTProto_BOT") or "0").strip().lower()
    use_mtproto_bot = mtbot_raw in ("1", "true", "yes", "on")
    if bot_mode == "fragment" and use_mtproto_bot:
        raise RuntimeError("USE_MTProto_BOT=1 совместим только с BOT_MODE=telethon и API my.telegram.org")

    telethon_connection = (os.environ.get("TELETHON_CONNECTION") or "full").strip().lower()
    if telethon_connection not in ("full", "obfuscated", "intermediate", "abridged"):
        telethon_connection = "full"

    # В fragment API не обязателен; если ID/HASH не заданы — Telethon-чекер отключён.
    api_id_int = 0
    api_hash_val = ""
    if api_id_raw.strip():
        try:
            api_id_int = int(api_id_raw.strip())
        except ValueError:
            api_id_int = 0
    if api_id_int and api_hash.strip():
        api_hash_val = api_hash.strip()

    if bot_mode == "fragment" and (not api_id_int or not api_hash_val):
        username_check_mode = "disabled"

    try:
        fragment_delay = float((os.environ.get("FRAGMENT_REQUEST_DELAY_S") or "0.07").strip())
    except ValueError:
        fragment_delay = 0.07
    fragment_delay = max(0.0, fragment_delay)

    try:
        fragment_roll_timeout_s = int(
            (os.environ.get("FRAGMENT_ROLL_TIMEOUT_S") or "12").strip()
        )
    except ValueError:
        fragment_roll_timeout_s = 12
    fragment_roll_timeout_s = max(5, min(45, fragment_roll_timeout_s))

    try:
        telethon_check_delay_s = float(
            (os.environ.get("TELETHON_CHECK_DELAY_S") or "0.10").strip()
        )
    except ValueError:
        telethon_check_delay_s = 0.10
    telethon_check_delay_s = max(0.04, min(2.5, telethon_check_delay_s))

    try:
        luck_promo_max_uses = int((os.environ.get("LUCK_PROMO_MAX_USES") or "0").strip())
    except ValueError:
        luck_promo_max_uses = 0
    luck_promo_max_uses = max(0, luck_promo_max_uses)

    try:
        referral_plus_hours = int((os.environ.get("REFERRAL_PLUS_HOURS") or "1").strip())
    except ValueError:
        referral_plus_hours = 1
    referral_plus_hours = max(1, min(168, referral_plus_hours))

    bot_username_for_links = (os.environ.get("BOT_USERNAME_FOR_LINKS") or "").strip().lstrip("@")

    required_channel_username = parse_required_channel_username(
        os.environ.get("REQUIRED_CHANNEL_USERNAME")
    )

    plus_hint_raw = (os.environ.get("PLUS_PAYMENT_HINT") or "").strip()
    if not plus_hint_raw:
        plus_payment_hint = (
            "<b>Оплата</b>: переведите указанную сумму и пришлите скрин чека в поддержку "
            "(кнопка «Поддержка») с пометкой <code>PLUS</code> и вашим <code>user_id</code> "
            "из раздела «Аккаунт» (если виден) или перешлите это сообщение.\n\n"
            "<i>После проверки администратор включит подписку вручную.</i>"
        )
    else:
        plus_payment_hint = plus_hint_raw.replace("\\n", "\n")

    luck_hint_raw = (os.environ.get("LUCK_PAYMENT_HINT") or "").strip()
    if not luck_hint_raw:
        luck_payment_hint = plus_payment_hint
    else:
        luck_payment_hint = luck_hint_raw.replace("\\n", "\n")

    platega_merchant_id = (os.environ.get("PLATEGA_MERCHANT_ID") or "").strip()
    platega_secret = (os.environ.get("PLATEGA_SECRET") or "").strip()
    platega_api_base = (os.environ.get("PLATEGA_API_BASE") or "https://app.platega.io").strip().rstrip("/")
    if not platega_api_base.startswith("http"):
        platega_api_base = "https://app.platega.io"
    try:
        platega_payment_method = int((os.environ.get("PLATEGA_PAYMENT_METHOD") or "2").strip())
    except ValueError:
        platega_payment_method = 2
    platega_return_url = (os.environ.get("PLATEGA_RETURN_URL") or "").strip()
    platega_failed_url = (os.environ.get("PLATEGA_FAILED_URL") or "").strip()

    return Settings(
        bot_token=token,
        api_id=api_id_int,
        api_hash=api_hash_val,
        telethon_session=session,
        plus_promo_code=promo.upper(),
        luck_promo_code=luck_promo.upper(),
        admin_ids=admins,
        db_path=db,
        ton_to_usd=ton_to_usd,
        telethon_timeout=max(10, telethon_timeout),
        telethon_connection_retries=max(1, telethon_connection_retries),
        username_check_mode=username_check_mode,
        use_mtproto_bot=use_mtproto_bot,
        telethon_connection=telethon_connection,
        bot_mode=bot_mode,
        fragment_request_delay_s=fragment_delay,
        fragment_roll_timeout_s=fragment_roll_timeout_s,
        telethon_check_delay_s=telethon_check_delay_s,
        plus_payment_hint=plus_payment_hint,
        luck_payment_hint=luck_payment_hint,
        luck_promo_max_uses=luck_promo_max_uses,
        referral_plus_hours=referral_plus_hours,
        bot_username_for_links=bot_username_for_links,
        required_channel_username=required_channel_username,
        platega_merchant_id=platega_merchant_id,
        platega_secret=platega_secret,
        platega_api_base=platega_api_base,
        platega_payment_method=platega_payment_method,
        platega_return_url=platega_return_url,
        platega_failed_url=platega_failed_url,
    )
