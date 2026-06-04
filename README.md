<p align="center">
  <img src="https://raw.githubusercontent.com/qTox/qTox/master/img/icons/qtox.svg" height="84" alt="qTox">
  &nbsp;&nbsp;&nbsp;➜&nbsp;&nbsp;&nbsp;
  <img src="https://upload.wikimedia.org/wikipedia/commons/8/82/Telegram_logo.svg" height="84" alt="Telegram">
</p>

<h1 align="center">qTox &rarr; Telegram Bridge</h1>

<p align="center">
  Читай и отвечай на свою Tox-переписку прямо из Telegram.
</p>

<p align="center">
  <a href="https://github.com/stabbyjs/qtox-telegram-bridge/actions/workflows/ci.yml"><img src="https://github.com/stabbyjs/qtox-telegram-bridge/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License: GPLv3"></a>
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white" alt="Telegram Bot API">
</p>

---

Это демон, который встаёт вместо qTox: поднимает твою Tox-ноду, держит её в
сети и зеркалит переписку в Telegram. Каждый собеседник получает свой топик
прямо в личном чате с ботом, без отдельной супергруппы (так умеет Telegram Bot
API начиная с 9.4). Пишешь в топик, и сообщение уходит собеседнику по Tox; он
отвечает, и ответ возвращается в тот же топик. Текст, файлы и картинки ходят в
обе стороны.

> [!IMPORTANT]
> Tox разрешает держать профиль онлайн только в одном месте. Пока работает
> мост, не запускай qTox с тем же `.tox`-профилем.

## Что умеет

- Личные сообщения в обе стороны, с защитой от дублей и поддержкой действий `/me`.
- Файлы и картинки в обе стороны (фото уходит как фото, остальное как документ).
- Статусы собеседника (в сети, отошёл, занят) и индикатор набора текста.
- Отметки о прочтении.
- Приём аватаров собеседников.
- Заявки в друзья с кнопками "Принять" и "Отклонить".
- Настройки прямо в чате: меню `/settings` с кнопками-переключателями
  (уведомления о статусах, индикатор набора текста, отметки о прочтении,
  приём файлов, беззвучный режим). Отдельные диалоги можно глушить.
- Управление контактами и своим профилем командами.

Команды бота:

| Команда | Что делает |
|---------|------------|
| `/settings` | меню настроек с кнопками |
| `/help` | список команд |
| `/toxid` | показать свой Tox ID |
| `/friends` | контакты со статусом онлайн |
| `/status` | сколько контактов и сколько в сети |
| `/add <ToxID> [текст]` | отправить заявку в друзья |
| `/del <pubkey>` | удалить контакт |
| `/setname <имя>` | сменить свой ник |
| `/setstatus <текст>` | сменить статусное сообщение |
| `/online` `/away` `/busy` | сменить свой статус |
| `/mute` `/unmute` | заглушить или вернуть уведомления в этом диалоге |

## Где работает

Мост запускается в Docker, поэтому одинаково идёт на Linux, macOS (Docker
Desktop) и Windows (Docker Desktop или WSL2). На том же образе можно держать
его локально для проверки, а потом перенести на VPS без изменений.

Профиль qTox лежит в разных местах в зависимости от системы:

| Система | Где искать `.tox`-профиль |
|---------|---------------------------|
| Linux | `~/.config/tox/` |
| macOS | `~/Library/Application Support/Tox/` |
| Windows | `%APPDATA%\tox\` |

## Что нужно перед запуском

1. **Профиль и пароль.** Скопируй свой `.tox`-профиль в `./data/` и впиши пароль
   шифрования в `.env`.
2. **Бот.** Заведи бота через [@BotFather](https://t.me/BotFather), возьми токен
   и включи у него Threaded Mode. Без этого Telegram не даст боту создавать
   топики в личке и вернёт `BOT_FORUM_CREATE_FORBIDDEN`.
3. **Свой Telegram ID.** Либо впиши `TELEGRAM_OWNER_ID`, либо просто отправь боту
   `/start` после запуска, и он запомнит тебя сам.

## Запуск

```bash
cp .env.example .env
# впиши TOX_PROFILE_PASSWORD и TELEGRAM_BOT_TOKEN
mkdir -p data
# Linux:
cp ~/.config/tox/<твой-профиль>.tox data/
# macOS:
cp "$HOME/Library/Application Support/Tox/<твой-профиль>.tox" data/

docker compose up --build -d
docker compose logs -f
```

В логах появится строка `Tox ID: ...`. После этого отправь боту `/start`.

Первая сборка занимает несколько минут: образ собирает `libtoxcore` и
`py-toxcore-c` из исходников.

Полезное под рукой:

```bash
docker compose logs -f     # живые логи
docker compose restart     # перезапуск
docker compose down        # остановить
```

## Разработка

Ядро (`config`, `store`, `router`, `file_transfer`) и адаптеры тестируются без
системного `libtoxcore`. Нативная библиотека нужна только для реального запуска.

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q --cov=bridge
```

Подробнее про внутреннее устройство: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Как присылать правки: [CONTRIBUTING.md](CONTRIBUTING.md).

## Настройки

Всё задаётся переменными окружения, полный список лежит в
[`.env.example`](.env.example). Главное:

| Переменная | Назначение |
|------------|-----------|
| `TOX_PROFILE_PATH` | путь к профилю внутри контейнера |
| `TOX_PROFILE_PASSWORD` | пароль шифрования профиля |
| `TELEGRAM_BOT_TOKEN` | токен бота (Threaded Mode включён) |
| `TELEGRAM_OWNER_ID` | твой Telegram ID (или `/start`) |
| `TELEGRAM_TOPIC_MODE` | `private` по умолчанию, либо `supergroup` |
| `MAX_FILE_MB` | лимит размера файла (бот тянет из Telegram до 20 МБ) |

## Чего пока нет

Групповые чаты Tox, звонки и импорт старой истории из qTox. Звонки через
текстовый мост невозможны по своей природе; группы и историю можно добавить,
это в планах.

## Лицензия

[GPL-3.0-or-later](LICENSE). Проект линкуется с `libtoxcore` под GPL.
