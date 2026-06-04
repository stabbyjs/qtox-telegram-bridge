"""Маршрутизация между Tox и Telegram поверх Store/TopicSink/ToxSender.

Создаёт и переиспользует топики, отсекает дубли, переводит входящие события
в сообщения Telegram, учитывает настройки и разбирает команды владельца.
"""

from __future__ import annotations

import logging
import os

from .interfaces import ToxSender, TopicSink
from .models import (
    InboundConnection,
    InboundFileComplete,
    InboundFriendRequest,
    InboundName,
    InboundReadReceipt,
    InboundStatus,
    InboundText,
    InboundTyping,
)
from .settings import Settings
from .store import Store

log = logging.getLogger(__name__)

_STATUS_RU = {"online": "в сети", "away": "отошёл", "busy": "не беспокоить"}

_HELP = (
    "Команды:\n"
    "/settings - настройки бота (кнопки)\n"
    "/toxid - мой Tox ID\n"
    "/friends - список контактов\n"
    "/status - сводка\n"
    "/add <ToxID> [текст] - отправить заявку\n"
    "/del <pubkey> - удалить контакт\n"
    "/setname <имя> - сменить мой ник\n"
    "/setstatus <текст> - сменить статусное сообщение\n"
    "/online /away /busy - сменить мой статус\n"
    "/mute /unmute - заглушить/вернуть уведомления в этом диалоге"
)


class Router:
    def __init__(self, store: Store, sink: TopicSink, tox: ToxSender, settings: Settings):
        self._store = store
        self._sink = sink
        self._tox = tox
        self._cfg = settings

    async def _ensure_topic(self, pubkey: str, name: str) -> int:
        topic_id = await self._store.topic_for_pubkey(pubkey)
        if topic_id is not None:
            if name and await self._store.name_for_pubkey(pubkey) != name:
                await self._store.update_name(pubkey, name)
            return topic_id
        topic_id = await self._sink.create_topic(name or pubkey[:8])
        await self._store.link_friend(pubkey, name, topic_id)
        return topic_id

    @property
    def _silent(self) -> bool:
        return self._cfg.get("silent")

    # Tox -> Telegram

    async def handle_inbound_text(self, msg: InboundText) -> None:
        key = f"in:{msg.pubkey}:{msg.message_id}"
        if await self._store.is_duplicate(key):
            return
        topic_id = await self._ensure_topic(msg.pubkey, msg.name)
        text = f"* {msg.text}" if (msg.action and self._cfg.get("show_actions")) else msg.text
        await self._sink.send_text(topic_id, text, silent=self._silent)
        await self._store.mark_seen(key)

    async def handle_inbound_file(self, f: InboundFileComplete) -> None:
        if not self._cfg.get("forward_files"):
            _remove(f.path)
            return
        key = f"infile:{f.pubkey}:{f.filename}:{f.size}"
        if await self._store.is_duplicate(key):
            return
        topic_id = await self._ensure_topic(f.pubkey, f.name)
        await self._sink.send_file(topic_id, f.path, as_photo=f.is_image, silent=self._silent)
        await self._store.mark_seen(key)
        _remove(f.path)

    async def handle_inbound_friend_request(self, req: InboundFriendRequest) -> None:
        rid = await self._store.add_request(req.pubkey, req.message)
        if rid is None:
            return
        await self._sink.send_friend_request(rid, req.pubkey, req.message)

    async def handle_inbound_connection(self, ev: InboundConnection) -> None:
        if not self._cfg.get("notify_status") or self._cfg.is_muted(ev.pubkey):
            return
        topic_id = await self._store.topic_for_pubkey(ev.pubkey)
        if topic_id is not None:
            await self._sink.notify(topic_id, "в сети" if ev.online else "не в сети", silent=self._silent)

    async def handle_inbound_status(self, ev: InboundStatus) -> None:
        if not self._cfg.get("notify_status") or self._cfg.is_muted(ev.pubkey):
            return
        topic_id = await self._store.topic_for_pubkey(ev.pubkey)
        if topic_id is not None:
            await self._sink.notify(topic_id, _STATUS_RU.get(ev.status, ev.status), silent=self._silent)

    async def handle_inbound_typing(self, ev: InboundTyping) -> None:
        if not ev.typing or not self._cfg.get("notify_typing") or self._cfg.is_muted(ev.pubkey):
            return
        topic_id = await self._store.topic_for_pubkey(ev.pubkey)
        if topic_id is not None:
            await self._sink.send_typing(topic_id)

    async def handle_inbound_name(self, ev: InboundName) -> None:
        topic_id = await self._store.topic_for_pubkey(ev.pubkey)
        if topic_id is None:
            return
        await self._store.update_name(ev.pubkey, ev.name)
        await self._sink.edit_topic_name(topic_id, ev.name)

    async def handle_inbound_read_receipt(self, ev: InboundReadReceipt) -> None:
        if not self._cfg.get("read_receipts"):
            return
        sent = await self._store.pop_sent(ev.pubkey, ev.message_id)
        if sent is not None:
            await self._sink.mark_read(sent["thread_id"], sent["tg_msg_id"])

    # Telegram -> Tox

    async def handle_outbound_text(self, thread_id: int, text: str, tg_msg_id: int = 0) -> None:
        pubkey = await self._store.pubkey_for_topic(thread_id)
        if pubkey is None:
            log.warning("текст в неизвестный топик %s - игнор", thread_id)
            return
        tox_msg_id = await self._tox.send_message(pubkey, text)
        if tox_msg_id is not None and tg_msg_id and self._cfg.get("read_receipts"):
            await self._store.remember_sent(pubkey, tox_msg_id, tg_msg_id, thread_id)

    async def handle_outbound_file(self, thread_id: int, path: str, filename: str) -> None:
        pubkey = await self._store.pubkey_for_topic(thread_id)
        if pubkey is None:
            log.warning("файл в неизвестный топик %s - игнор", thread_id)
            return
        await self._tox.send_file(pubkey, path, filename)

    async def handle_friend_decision(self, rid: int, accept: bool) -> None:
        pubkey = await self._store.pop_request(rid)
        if pubkey is None:
            return
        if accept:
            self._tox.accept_friend(pubkey)
        else:
            self._tox.reject_friend(pubkey)

    # настройки и команды

    def settings_view(self) -> list[tuple[str, str, bool]]:
        return self._cfg.view()

    async def toggle_setting(self, key: str) -> list[tuple[str, str, bool]]:
        await self._cfg.toggle(key)
        return self._cfg.view()

    async def handle_command(self, name: str, args: str, thread_id: int = 0) -> str:
        if name == "toxid":
            return f"Tox ID:\n{self._tox.tox_id}"
        if name == "help":
            return _HELP
        if name in ("online", "away", "busy"):
            self._tox.set_self_status(name)
            return f"Статус: {_STATUS_RU.get(name, name)}"
        if name == "setname":
            if not args:
                return "Использование: /setname <имя>"
            self._tox.set_self_name(args)
            return f'Ник изменён на "{args}"'
        if name == "setstatus":
            self._tox.set_self_status_message(args)
            return "Статусное сообщение обновлено"
        if name == "add":
            parts = args.split(maxsplit=1)
            if not parts:
                return "Использование: /add <ToxID> [текст]"
            tox_id = parts[0].strip()
            if not _is_tox_id(tox_id):
                return "Неверный Tox ID - нужно 76 hex-символов"
            ok = self._tox.add_friend(tox_id, parts[1] if len(parts) > 1 else "")
            return "Заявка отправлена" if ok else "Не удалось отправить заявку"
        if name == "del":
            pubkey = args.strip()
            if not pubkey:
                return "Использование: /del <pubkey>"
            ok = self._tox.delete_friend(pubkey)
            if ok:
                try:
                    await self._store.unlink_friend(pubkey)
                except Exception:
                    log.exception("не удалось убрать %s из БД", pubkey)
            return "Контакт удалён" if ok else "Контакт не найден"
        if name in ("mute", "unmute"):
            return await self._handle_mute(name, thread_id)
        if name in ("friends", "status"):
            return self._format_friends(name)
        return "Неизвестная команда. /help - список."

    async def _handle_mute(self, name: str, thread_id: int) -> str:
        pubkey = await self._store.pubkey_for_topic(thread_id) if thread_id else None
        if pubkey is None:
            return "Команду нужно отправить внутри топика диалога"
        if name == "mute":
            await self._cfg.mute(pubkey)
            return "Уведомления в этом диалоге заглушены"
        await self._cfg.unmute(pubkey)
        return "Уведомления в этом диалоге включены"

    def _format_friends(self, name: str) -> str:
        friends = self._tox.friends_summary()
        online = sum(1 for f in friends if f["online"])
        if name == "status":
            return f"Контактов: {len(friends)}, в сети: {online}"
        if not friends:
            return "Список контактов пуст"
        lines = [
            f"{'🟢' if f['online'] else '⚪'} {f['name'] or '(без имени)'}\n  {f['pubkey']}"
            for f in friends
        ]
        return "\n".join(lines)


def _is_tox_id(value: str) -> bool:
    return len(value) == 76 and all(c in "0123456789abcdefABCDEF" for c in value)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
