# Пошаговая инструкция: перенос бота с Windows на Ubuntu 22.04

Речь про запуск **`python bot_aiogram.py`** (основной бот в этом репозитории).

На одном сервере можно держать **несколько ботов** — у каждого свой каталог, свой `.env`, свой `BOT_TOKEN` и своя база/сессии. Главное — **не** запускать два процесса с **одним и тем же** токеном (Telegram отключит старый long polling).

---

## Что подготовить на Windows перед переносом

1. **Папка проекта** целиком (без тяжёлых мусорных каталогов, если есть):  
   можно заархивировать в `.zip` или использовать `scp`/`rsync` (см. ниже).

2. **Файл `.env`** — скопируйте отдельно (в архив его лучше **не** класть в публичные места). На сервере создадите его заново или перенесёте вручную.

3. **Файлы сессии Telethon** (если используете проверку через Telethon, не `USERNAME_CHECK_MODE=disabled`):  
   в каталоге проекта это обычно файлы вида  
   `ИМЯ.session` и иногда `ИМЯ.session-journal`  
   где `ИМЯ` = значение `TELETHON_SESSION_NAME` из `.env`.  
   **Скопируйте их на сервер** в ту же папку, где лежит код — иначе бот снова попросит логин в консоли.

4. **База SQLite** (если нужны текущие пользователи и данные):  
   файл из `.env` как `BOT_DB_PATH`, по умолчанию часто `bot_data.sqlite` в корне проекта — скопируйте его на сервер.

---

## Шаг 1. Сервер Ubuntu 22.04: базовые пакеты

Зайдите по SSH под своим пользователем (не обязательно root).

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

Проверьте версию Python (нужен **3.10+**):

```bash
python3 --version
```

---

## Шаг 2. Куда положить проект

Удобный вариант — отдельный каталог под этого бота, например:

```bash
mkdir -p ~/bots
cd ~/bots
```

Дальше предполагается путь **`~/bots/telegram_username_bot`** (имя папки можете сменить).

### Вариант A: скопировать архив с Windows

На Windows заархивируйте папку проекта (без `.venv`, чтобы не тащить Windows-бинарники).  
На сервер загрузите архив (`scp`, WinSCP, FileZilla и т.д.), затем:

```bash
cd ~/bots
unzip telegram_username_bot.zip -d telegram_username_bot
cd ~/bots/telegram_username_bot
```

### Вариант B: клонировать из Git (если проект в репозитории)

```bash
cd ~/bots
git clone <URL_вашего_репозитория> telegram_username_bot
cd telegram_username_bot
```

---

## Шаг 3. Виртуальное окружение Python

```bash
cd ~/bots/telegram_username_bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Проверка:

```bash
python -c "import aiogram; print('ok')"
```

---

## Шаг 4. Файл `.env` на сервере

```bash
cd ~/bots/telegram_username_bot
nano .env
```

- Скопируйте содержимое с Windows или соберите заново по шаблону **`.env.example`** в корне проекта.
- Обязательно проверьте:
  - **`BOT_TOKEN`** — токен **этого** бота от BotFather.
  - **`BOT_DB_PATH`** — лучше явно указать абсолютный путь, чтобы не путаться:
    - пример: `BOT_DB_PATH=/home/ВАШ_ЮЗЕР/bots/telegram_username_bot/bot_data.sqlite`
  - **`TELETHON_SESSION_NAME`** — уникальное имя, если на сервере уже есть другой бот с Telethon:  
    например `username_checker_shop` вместо `username_checker`, чтобы **не перезаписать чужой `.session`**.
  - При необходимости: **`BOT_USERNAME_FOR_LINKS`** (имя бота без `@`) для реферальных ссылок.

Права на `.env` (чтобы не читали посторонние):

```bash
chmod 600 .env
```

Положите рядом скопированные **`.session`** / **`bot_data.sqlite`**, если переносите с Windows.

---

## Шаг 5. Первый запуск вручную (проверка)

```bash
cd ~/bots/telegram_username_bot
source .venv/bin/activate
python bot_aiogram.py
```

- Если Telethon **ещё не** авторизован на этом сервере, в консоли спросят телефон и код — введите один раз; появится файл `ИМЯ.session`.
- Остановка: `Ctrl+C`.

Если всё ок — переходите к автозапуску (шаг 6).

---

## Шаг 6. Автозапуск через systemd (рекомендуется)

Создайте unit-файл (подставьте своего пользователя вместо `YOURUSER` и путь, если отличается):

```bash
sudo nano /etc/systemd/system/telegram-username-bot.service
```

Содержимое:

```ini
[Unit]
Description=Telegram username bot (aiogram)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOURUSER
Group=YOURUSER
WorkingDirectory=/home/YOURUSER/bots/telegram_username_bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/YOURUSER/bots/telegram_username_bot/.venv/bin/python bot_aiogram.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Команды:

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-username-bot
sudo systemctl start telegram-username-bot
sudo systemctl status telegram-username-bot
```

Логи:

```bash
journalctl -u telegram-username-bot -f
```

---

## Шаг 7. Второй (и третий) бот на том же сервере

**Да, это нормально:** несколько ботов на одной машине.

Сделайте **отдельную папку** и **отдельный** `systemd` unit:

| Что | Бот 1 | Бот 2 |
|-----|--------|--------|
| Каталог | `~/bots/telegram_username_bot` | `~/bots/telegram_username_bot_2` |
| `.env` | свой | свой |
| `BOT_TOKEN` | токен бота 1 | токен бота 2 |
| `BOT_DB_PATH` | свой файл `.sqlite` | **другой** файл `.sqlite` |
| `TELETHON_SESSION_NAME` + `.session` | своё имя | **другое** имя |
| systemd | `telegram-username-bot.service` | например `telegram-username-bot-2.service` |

**Нельзя:** два процесса с одним `BOT_TOKEN` (конфликт long polling).  
**Можно:** один и тот же `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` у разных ботов (лимиты Telegram общие — имейте в виду).

---

## Шаг 8. Обновление кода после деплоя

```bash
cd ~/bots/telegram_username_bot
source .venv/bin/activate
git pull   # если используете Git
pip install -r requirements.txt
sudo systemctl restart telegram-username-bot
```

---

## Частые проблемы

1. **`Conflict: terminated by other getUpdates`**  
   Запущено **два** процесса с **одним** `BOT_TOKEN` (Windows и Linux, или два systemd). Оставьте один процесс на токен.

2. **`database is locked` у Telethon**  
   Не запускайте два процесса, которые одновременно пишут в **один и тот же** `.session`. Разные боты — разные имена `TELETHON_SESSION_NAME`.

3. **Бот не видит `.env`**  
   `WorkingDirectory` в systemd должен указывать на корень проекта, где лежит `.env`.

4. **Прокси**  
   Если на сервере другие сетевые условия, проверьте переменные `PROXY` / `BOT_API_USE_PROXY` так же, как на Windows.

---

## Краткий чеклист

- [ ] Python 3.10+, venv, `pip install -r requirements.txt`
- [ ] `.env` с уникальным `BOT_TOKEN` и своим `BOT_DB_PATH`
- [ ] Скопированы `.session` (если нужен Telethon) и при необходимости `bot_data.sqlite`
- [ ] Ручной запуск `python bot_aiogram.py` прошёл успешно
- [ ] systemd unit с правильным `User` и `WorkingDirectory`
- [ ] Второй бот — отдельная папка, БД, сессия, unit и токен

Если нужно, следующим шагом можно вынести в инструкцию пример **nginx** не для этого бота (long polling не требует открытого веб-порта), только если вы вешаете отдельный вебхук — у вас сейчас по умолчанию **polling**, входящих HTTPS на сервер для самого бота не требуется.
