# Пробный Telegram-бот: поиск свободных username (5–6 букв)

Бот ищет **свободные** `@username` из латинских букв `a–z` длины 5 или 6. Проверка выполняется через **Telethon** (метод `account.checkUsername`), то есть нужен **пользовательский** аккаунт Telegram (API id/hash и сессия), не только токен бота.

## Возможности (пробная версия)

- Меню: **Ролл по ценности**, **Проверить и оценить**, **Топ за месяц**, **Личный кабинет**, **Подписка PLUS**
- Бесплатно: **4 поиска** (один запуск = один списанный поиск)
- **PLUS**: безлимитные поиски, кнопки **«Сохранить»** у найденных username, раздел **«Сохранённые имена»** в кабинете
- Активация PLUS в пробной сборке: **промокод** из `.env` (`PLUS_PROMO_CODE`, по умолчанию `DEMOPLUS2026`)
- Админы из `ADMIN_IDS` могут выдать PLUS командой: `/grant_plus <user_id>`
- Админ-команда для наполнения базы ценности: `/import_fragment <fragment_url>`

## Запуск

**Перенос на Ubuntu 22.04 (сервер, systemd, несколько ботов):** пошаговая инструкция — [docs/DEPLOY_UBUNTU_22.md](docs/DEPLOY_UBUNTU_22.md).

### 1. Python 3.10+

```bash
cd telegram_username_bot
python -m venv .venv
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Переменные окружения

Скопируйте `.env.example` в `.env` и заполните **именно `.env`** (это рабочий файл; `.env.example` — только шаблон, бот его сам не читает):

- `BOT_TOKEN` — у [@BotFather](https://t.me/BotFather)
- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` — с [https://my.telegram.org](https://my.telegram.org)
- `TELETHON_SESSION_NAME` — любое имя файла сессии (например `username_checker`)
- при желании смените `PLUS_PROMO_CODE` и укажите `ADMIN_IDS` (через запятую, ваш числовой `user_id`)
- `TON_TO_USD` (опционально): курс для конвертации TON в USD, если на Fragment цена распознана только в TON (по умолчанию `2.0`)

**Прокси (опционально):** внешний HTTP/SOCKS (`PROXY`) используется для **Fragment** и, при обычном режиме, для **long polling** Bot API; для **Telethon** (проверка ников) — по флагам ниже. Fragment **всегда** остаётся на HTTP(S), не на MTProto.

**Весь бот к Telegram через MTProto (Telethon), без long polling:** `USE_MTProto_BOT=1` в `.env`. Тогда сообщения и кнопки обрабатывает `mtproxy_bot_runner.py` (тот же функционал, что в `bot_v2.py`). К **Fragment** это не относится — импорт по-прежнему HTTP(S) и может идти через `PROXY`.

- `PROXY=192.168.0.1:1080 myuser:secret` — после `host:port` пробел, затем `логин:пароль`
- или `PROXY=192.168.0.1:1080 myuser secret` — логин и пароль **через один пробел** (без двоеточия)
- или `PROXY_URL=http://myuser:secret@192.168.0.1:8080` (также `socks5`, `socks5h`, `https` в URL)
- `PROXY_TYPE` — для строки `PROXY` без URL: `socks5` (по умолчанию), `socks5h`, `http`, `https`. У провайдерских **«HTTPS-прокси»** обычно нужно `http` или `https` (не `socks5`).

**MTProxy (отдельно от `PROXY`):** **MTProto** — это протокол клиента Telegram; **MTProxy** — отдельный TCP-прокси, который оборачивает MTProto-трафик (его нельзя подставить вместо HTTP/SOCKS для Bot API long polling).

**Два варианта с `MTPROXY`:**

1. **Только Telethon через MTProxy** (обычный бот, long polling): задайте **`MTPROXY`** и **`PROXY`** (или только `MTPROXY`, если Bot API без прокси вас устраивает), **не** включайте **`USE_MTProto_BOT`**. Тогда **проверка @username** (Telethon) идёт через **MTProxy**, а **long polling** и **Fragment** — через **`PROXY`** (HTTP/SOCKS), как у `python-telegram-bot` и `requests`.

2. **Весь бот через Telethon:** **`USE_MTProto_BOT=1`** (и при желании **`MTPROXY`**) — тогда поднимается `mtproxy_bot_runner.py`, без long polling.

- `MTPROXY=host:port секрет_hex` — один пробел между `host:port` и секретом (как в параметрах ссылки `t.me/proxy?...&secret=...`).
- или `MTPROXY_HOST`, `MTPROXY_PORT`, `MTPROXY_SECRET` (секрет — строка hex, без пробелов).

У Telethon поддержка MTProxy помечена как экспериментальная; если секрет в необычном формате и клиент ругается — обновите Telethon или уточните у провайдера «классический» hex-секрет.

Если при подключении к MTProxy в логах **«Server closed the connection»**, **`IncompleteReadError`** или **`readexactly size can not be less than zero`**: часто несовпадение типа TCP и секрета (ссылки из Telegram рассчитаны на **randomized**). Код **сам** перебирает режимы; **randomized всегда пробуется первым**, затем значение **`MTPROXY_TELETHON_CONNECTION`**, затем остальные. Если всё падает — проверьте `tg://proxy` в приложении и **секрет**. Два TCP (бот + проверка ников) часть прокси не держит. Подбор режима MTProxy делается через **MemorySession**, чтобы не блокировать файл ``.session`` (ошибка ``database is locked``).

Если **`MTPROXY` и `USE_MTProto_BOT` не заданы**, используется **long polling** (`python-telegram-bot`). Если при этом **`telegram.error.TimedOut`** на `get_me` — это HTTP/SOCKS **`PROXY`** до `api.telegram.org`. Оставьте `PROXY` для **Fragment**, а Bot API пусть идёт напрямую: **`BOT_API_USE_PROXY=0`**.

### 3. Первый вход Telethon

При **первом** запуске скрипт попросит номер телефона и код из Telegram (интерактивно в консоли). Сессия сохранится в файл `ИМЯ.session` в текущей папке.

```bash
python bot_v2.py
```

Дальше бот работает в режиме **long polling** (если **не** задан `USE_MTProto_BOT=1`) или целиком через **Telethon** (`mtproxy_bot_runner`). Остановка: `Ctrl+C`.

### Важно перед роллом

Ролл “по ценности” опирается на импортированные страницы Fragment.

- Админ: `/import_fragment <fragment_url>`
- Импортируйте хотя бы 20-30 страниц, чтобы прогноз по редкости был реалистичнее.

## Ограничения и замечания

### Telethon + прокси: `TimeoutError`

У Telethon по умолчанию короткий таймаут на установку соединения. В `.env` можно поднять:

- `TELETHON_TIMEOUT=120`
- `TELETHON_CONNECTION_RETRIES=10`

Если прокси **HTTP/HTTPS**, часто помогает явно:

- `PROXY_RDNS=false`

Если после этого всё ещё таймаут — прокси может **не пускать** исходящие соединения к дата-центрам Telegram (или неверные логин/порт/тип). Проверьте `PROXY_TYPE=http` для «HTTPS-прокси» у провайдера.

### Страница my.telegram.org: «Available MTProto servers»

Там указаны **production** IP/порт и публичный ключ — это **та же сеть**, к которой Telethon подключается по умолчанию. Вручную подставлять эти IP **обычно не нужно** и **не лечит** прокси, который режет MTProto.

**Test configuration** — отдельная **тестовая** сеть Telegram, для реальных `@username` не подходит.

Иногда за фильтрами помогает сменить **тип TCP-соединения** Telethon (в `.env`):

- `TELETHON_CONNECTION=obfuscated` (по умолчанию `full`)

Допустимые значения: `full`, `obfuscated`, `intermediate`, `abridged`.

### Без Telethon или Telethon без прокси

Официальной замены **MTProto `account.checkUsername`** через **только Bot API** нет: «свободен ли username для установки на аккаунт» без пользовательской сессии Telegram не проверить честно.

Варианты в `.env`:

- **`USE_MTProto_BOT=1`** — весь бот к Telegram через **Telethon (MTProto)**; **Fragment** только по HTTP(S) (`PROXY` при необходимости). Дополнительно можно задать **`MTPROXY`** для туннеля MTProto.
- **`TELETHON_USE_PROXY=0`** — при **long polling**-режиме Bot API и Fragment идут через `PROXY`, а **Telethon** для проверки ников — напрямую (если прокси режет MTProto).
- **`BOT_API_USE_PROXY=0`** — только для **long polling**: `getUpdates` без `PROXY`; `PROXY` остаётся для Fragment/`requests`.
- **`USERNAME_CHECK_MODE=disabled`** — Telethon **не запускается**; бот даёт только **оценку/ролл по данным** с пометкой, что **занятость в Telegram не проверялась**. Финальную проверку нужно делать вручную в приложении Telegram.

- Telegram может ограничивать частоту запросов (**FloodWait**); в коде есть пауза между проверками и одна повторная попытка при FloodWait.
- Ищутся только **простые** ники из `a–z` (как в ТЗ); цифры и подчёркивания здесь не перебираются.
- Массовый перебор может нарушать правила сервиса — используйте умеренные лимиты и только на свой страх и риск.

## Структура

| Файл        | Назначение                          |
|------------|--------------------------------------|
| `bot_v2.py`| Логика бота (PTB long polling или делегирование в `mtproxy_bot_runner`) |
| `mtproxy_bot_runner.py` | Telethon (MTProto) для всего трафика бота к Telegram при `MTPROXY` или `USE_MTProto_BOT` |
| `checker.py` | Telethon, проверка username      |
| `db.py`    | SQLite: лимиты, PLUS, сохранённые имена |
| `config.py`| Загрузка `.env`                     |
| `fragment_scraper.py` | Импорт данных Fragment по веб-страницам |
| `value_model.py` | Прогноз $ и маппинг в редкость |
| `proxy_config.py` | Парсинг `PROXY` / `PROXY_URL` и `MTPROXY*` для Telethon, requests и Bot API |
