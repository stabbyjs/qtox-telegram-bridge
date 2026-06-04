"""Адаптер Telegram на aiogram v3: топики в личном чате с ботом.

Реализует TopicSink и принимает апдейты владельца, передавая их в колбэки
роутера. Режим private держит топики прямо в личке (Bot API 9.4+), режим
supergroup - в forum-супергруппе.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from aiogram.types import FSInputFile, ReactionTypeEmoji
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import BridgeConfig, TopicMode
from .file_transfer import safe_filename, unique_path

log = logging.getLogger(__name__)

# getFile в Bot API отдаёт не больше 20 МБ.
DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024

SettingsView = list  # list[tuple[str, str, bool]]


@dataclass
class TelegramCallbacks:
    on_outbound_text: Callable[[int, str, int], Awaitable[None]]
    on_outbound_file: Callable[[int, str, str], Awaitable[None]]
    on_friend_decision: Callable[[int, bool], Awaitable[None]]
    on_owner_start: Callable[[int], Awaitable[None]]
    on_command: Callable[[str, str, int], Awaitable[str]]
    on_settings: Callable[[], Awaitable[SettingsView]]
    on_toggle: Callable[[str], Awaitable[SettingsView]]


def _settings_markup(view: SettingsView):
    builder = InlineKeyboardBuilder()
    for key, label, enabled in view:
        mark = "✅" if enabled else "⬜"
        builder.button(text=f"{mark} {label}", callback_data=f"set:{key}")
    builder.adjust(1)
    return builder.as_markup()


class TelegramService:
    def __init__(
        self,
        bot,
        config: BridgeConfig,
        callbacks: TelegramCallbacks,
        tmp_dir: str = "./data/tmp",
    ):
        self._bot = bot
        self._config = config
        self._cb = callbacks
        self._tmp_dir = tmp_dir
        self._owner_id: Optional[int] = config.telegram_owner_id
        os.makedirs(tmp_dir, exist_ok=True)

    def set_owner_id(self, owner_id: int) -> None:
        self._owner_id = owner_id

    # адресация и доступ

    def _target(self) -> int:
        if self._config.topic_mode is TopicMode.SUPERGROUP:
            assert self._config.telegram_chat_id is not None
            return self._config.telegram_chat_id
        if self._owner_id is None:
            raise RuntimeError("owner id неизвестен - нужен /start")
        return self._owner_id

    def _is_owner(self, user_id: Optional[int]) -> bool:
        return self._owner_id is not None and user_id == self._owner_id

    def _is_allowed(self, message) -> bool:
        if not self._is_owner(getattr(message.from_user, "id", None)):
            return False
        if self._config.topic_mode is TopicMode.SUPERGROUP:
            return message.chat.id == self._config.telegram_chat_id
        return message.chat.id == self._owner_id

    # TopicSink

    async def create_topic(self, name: str) -> int:
        topic = await self._bot.create_forum_topic(
            chat_id=self._target(), name=(name or "chat")[:128]
        )
        return topic.message_thread_id

    async def edit_topic_name(self, thread_id: int, name: str) -> None:
        try:
            await self._bot.edit_forum_topic(
                chat_id=self._target(), message_thread_id=thread_id, name=name[:128]
            )
        except Exception:
            log.debug("не удалось переименовать топик %s", thread_id, exc_info=True)

    async def send_text(self, thread_id: int, text: str, silent: bool = False) -> Optional[int]:
        msg = await self._bot.send_message(
            chat_id=self._target(),
            message_thread_id=thread_id,
            text=text,
            disable_notification=silent,
        )
        return getattr(msg, "message_id", None)

    async def send_file(self, thread_id: int, path: str, as_photo: bool, silent: bool = False) -> None:
        target = self._target()
        if as_photo:
            await self._bot.send_photo(
                chat_id=target, message_thread_id=thread_id,
                photo=FSInputFile(path), disable_notification=silent,
            )
        else:
            await self._bot.send_document(
                chat_id=target, message_thread_id=thread_id,
                document=FSInputFile(path), disable_notification=silent,
            )

    async def notify(self, thread_id: int, text: str, silent: bool = False) -> None:
        await self._bot.send_message(
            chat_id=self._target(), message_thread_id=thread_id,
            text=text, disable_notification=silent,
        )

    async def send_typing(self, thread_id: int) -> None:
        try:
            await self._bot.send_chat_action(
                chat_id=self._target(), action="typing", message_thread_id=thread_id
            )
        except Exception:
            log.debug("send_chat_action не прошёл", exc_info=True)

    async def mark_read(self, thread_id: int, message_id: int) -> None:
        try:
            await self._bot.set_message_reaction(
                chat_id=self._target(),
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji="👀")],
            )
        except Exception:
            log.debug("реакция о прочтении не поставлена", exc_info=True)

    async def send_friend_request(self, rid: int, pubkey: str, message: str) -> None:
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Принять", callback_data=f"fr:a:{rid}")
        builder.button(text="❌ Отклонить", callback_data=f"fr:r:{rid}")
        builder.adjust(2)
        text = f"Заявка в друзья\nTox ID: {pubkey}\nСообщение: {message or '(пусто)'}"
        await self._bot.send_message(
            chat_id=self._target(), text=text, reply_markup=builder.as_markup()
        )

    # приём апдейтов

    async def handle_start(self, message) -> None:
        owner_id = message.from_user.id
        configured = self._config.telegram_owner_id
        if configured is not None and owner_id != configured:
            log.warning("/start от постороннего %s - игнор", owner_id)
            return
        self._owner_id = owner_id
        await self._cb.on_owner_start(owner_id)
        try:
            await self._bot.send_message(
                chat_id=owner_id,
                text="Мост запущен. Каждый диалог появится отдельным топиком. /help - команды.",
            )
        except Exception:
            log.debug("приветствие не ушло", exc_info=True)

    async def handle_command(self, message) -> None:
        if not self._is_allowed(message):
            return
        raw = (message.text or "")[1:]
        name, _, args = raw.partition(" ")
        name = name.split("@", 1)[0].lower()
        thread = getattr(message, "message_thread_id", None)
        if name == "settings":
            view = await self._cb.on_settings()
            await self._bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=thread,
                text="Настройки. Нажми, чтобы переключить:",
                reply_markup=_settings_markup(view),
            )
            return
        reply = await self._cb.on_command(name, args.strip(), thread or 0)
        if reply:
            await self._bot.send_message(
                chat_id=message.chat.id, message_thread_id=thread, text=reply
            )

    async def handle_message(self, message) -> None:
        if not self._is_allowed(message):
            return
        thread = getattr(message, "message_thread_id", None)
        if getattr(message, "document", None) is not None:
            await self._incoming_file(thread, message.document, None)
            return
        if getattr(message, "photo", None):
            await self._incoming_file(thread, message.photo[-1], "photo.jpg")
            return
        text = getattr(message, "text", None)
        if text and getattr(message, "is_topic_message", False) and thread is not None:
            await self._cb.on_outbound_text(thread, text, getattr(message, "message_id", 0))

    async def _incoming_file(self, thread, item, default_name) -> None:
        if thread is None:
            return
        declared = getattr(item, "file_size", None)
        if declared is not None and declared > DOWNLOAD_LIMIT_BYTES:
            await self.notify(thread, "Файл больше 20 МБ - Telegram не отдаёт его боту.")
            return
        filename = default_name or safe_filename(getattr(item, "file_name", None) or "file.bin")
        dest = unique_path(self._tmp_dir, filename)
        await self._bot.download(item, destination=dest)
        try:
            if os.path.getsize(dest) > DOWNLOAD_LIMIT_BYTES:
                os.remove(dest)
                await self.notify(thread, "Файл больше 20 МБ - пропущен.")
                return
        except OSError:
            return
        await self._cb.on_outbound_file(thread, dest, filename)

    async def handle_callback(self, callback) -> None:
        if not self._is_owner(getattr(callback.from_user, "id", None)):
            await callback.answer("Недоступно")
            return
        data = callback.data or ""
        if data.startswith("set:"):
            view = await self._cb.on_toggle(data[4:])
            await callback.answer("Готово")
            try:
                await callback.message.edit_reply_markup(reply_markup=_settings_markup(view))
            except Exception:
                log.debug("меню настроек не обновлено", exc_info=True)
            return
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "fr":
            return
        accept = parts[1] == "a"
        try:
            rid = int(parts[2])
        except ValueError:
            return
        await self._cb.on_friend_decision(rid, accept)
        await callback.answer("Принято" if accept else "Отклонено")
        try:
            await callback.message.edit_reply_markup()
        except Exception:
            log.debug("кнопки не убраны", exc_info=True)

    def register(self, dp) -> None:
        from aiogram import F, Router as AioRouter
        from aiogram.filters import Command, CommandStart

        r = AioRouter()
        r.message.register(self.handle_start, CommandStart())
        r.message.register(
            self.handle_command,
            Command(commands=[
                "help", "settings", "toxid", "friends", "status", "add", "del",
                "setname", "setstatus", "online", "away", "busy", "mute", "unmute",
            ]),
        )
        r.message.register(self.handle_message)
        r.callback_query.register(
            self.handle_callback, F.data.startswith("fr:") | F.data.startswith("set:")
        )
        dp.include_router(r)
