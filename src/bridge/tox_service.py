"""Адаптер сети Tox поверх py-toxcore-c (`pytox`).

Грузит зашифрованный профиль, крутит ``iterate()`` и превращает синхронные
коллбэки toxcore в события на ``asyncio.Queue``. Реализует ``ToxSender``.

Импорт ``pytox`` ленивый и подменяемый, поэтому чистая логация трансляции
проверяется тестами без системного libtoxcore.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .config import BridgeConfig
from .file_transfer import (
    IncomingTransfer,
    OutgoingTransfer,
    TransferTooLarge,
    is_image,
    safe_filename,
    unique_path,
)
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

log = logging.getLogger(__name__)

# Зазор для message_id между перезапусками: персистентный дедуп роутера не
# должен схлопывать сообщения новой сессии, чьи id начинаются заново.
_MID_GAP = 1_000_000

_KIND_DATA = 0
_KIND_AVATAR = 1

_STATUS_NAMES = {
    "TOX_USER_STATUS_NONE": "online",
    "TOX_USER_STATUS_AWAY": "away",
    "TOX_USER_STATUS_BUSY": "busy",
}


class ProfileDecryptError(Exception):
    """Профиль не расшифровался - почти всегда из-за неверного пароля."""


class ToxService:
    def __init__(
        self,
        config: BridgeConfig,
        inbound_queue: "asyncio.Queue",
        store,
        *,
        core=None,
        enc=None,
        nodes=None,
        tmp_dir: Optional[str] = None,
    ):
        self._config = config
        self._q = inbound_queue
        self._store = store
        self._core = core
        self._enc = enc
        self._nodes = nodes
        self._tmp_dir = tmp_dir or (config.tmp_dir if config else "./data/tmp")
        self._tmp_abs = os.path.abspath(self._tmp_dir)
        self._tox = None
        self._mid = 0
        self._max_file_bytes = (config.max_file_mb if config else 50) * 1024 * 1024
        self._incoming: dict[tuple[int, int], tuple[IncomingTransfer, str]] = {}
        self._outgoing: dict[tuple[int, int], OutgoingTransfer] = {}

    # профиль

    @staticmethod
    def load_profile(raw: bytes, password: str, enc) -> bytes:
        if enc.is_data_encrypted(raw):
            return enc.pass_decrypt(raw, password.encode())
        return raw

    def _lazy_import(self) -> None:
        if self._core is None:
            import pytox.toxcore.tox as core

            self._core = core
        if self._enc is None:
            import pytox.toxencryptsave.toxencryptsave as enc

            self._enc = enc

    async def build(self) -> "ToxService":
        self._lazy_import()
        core = self._core

        base = 0
        if self._store is not None:
            persisted = int(await self._store.get_meta("inbound_seq") or "0")
            base = persisted + _MID_GAP
            await self._store.set_meta("inbound_seq", str(base))
        self._mid = base

        data = None
        path = self._config.tox_profile_path
        if os.path.exists(path):
            with open(path, "rb") as f:
                raw = f.read()
            try:
                data = self.load_profile(raw, self._config.tox_profile_password, self._enc)
            except Exception as exc:
                raise ProfileDecryptError(
                    "не удалось расшифровать профиль Tox - проверь TOX_PROFILE_PASSWORD"
                ) from exc

        svc = self

        class _Node(core.Tox_Ptr):
            def handle_friend_message(self, fn, mtype, message):
                svc._on_friend_message(fn, mtype, message)

            def handle_friend_request(self, pk, message):
                svc._on_friend_request(pk, message)

            def handle_friend_connection_status(self, fn, status):
                svc._on_connection(fn, status)

            def handle_friend_status(self, fn, status):
                svc._on_status(fn, status)

            def handle_friend_typing(self, fn, typing):
                svc._on_typing(fn, typing)

            def handle_friend_name(self, fn, name):
                svc._on_name(fn, name)

            def handle_friend_read_receipt(self, fn, message_id):
                svc._on_read_receipt(fn, message_id)

            def handle_file_recv(self, fn, file_number, kind, file_size, filename):
                svc._on_file_recv(fn, file_number, kind, file_size, filename)

            def handle_file_recv_chunk(self, fn, file_number, position, data):
                svc._on_file_recv_chunk(fn, file_number, position, data)

            def handle_file_chunk_request(self, fn, file_number, position, length):
                svc._on_file_chunk_request(fn, file_number, position, length)

        with core.Tox_Options_Ptr() as opt:
            opt.udp_enabled = True
            if data:
                opt.savedata = data
                opt.savedata_type = core.Tox_Savedata_Type.TOX_SAVEDATA_TYPE_TOX_SAVE
            self._tox = _Node(opt)

        if not self._tox.name:
            self._tox.name = b"qTox Bridge"
        self._bootstrap()
        return self

    def _bootstrap(self) -> None:
        nodes = self._nodes
        if nodes is None:
            from . import nodes as nodes_mod

            nodes = nodes_mod.BOOTSTRAP_NODES
        ok = 0
        for host, port, pk in nodes:
            try:
                self._tox.bootstrap(host, port, bytes.fromhex(pk))
                self._tox.add_tcp_relay(host, port, bytes.fromhex(pk))
                ok += 1
            except Exception:
                log.debug("нода %s:%s недоступна", host, port, exc_info=True)
        if ok == 0:
            log.warning("ни одна bootstrap-нода не добавлена - связи с сетью Tox может не быть")

    # жизненный цикл

    def iterate(self) -> None:
        self._tox.iterate()

    @property
    def iteration_interval(self) -> int:
        return self._tox.iteration_interval

    @property
    def tox_id(self) -> str:
        return self._tox.address.hex().upper()

    def save(self) -> None:
        if self._tox is None:
            return
        # Шифруем, пишем во временный файл, затем атомарно подменяем - сбой записи
        # не должен оставить полупустой профиль.
        blob = self._enc.pass_encrypt(
            self._tox.savedata, self._config.tox_profile_password.encode()
        )
        path = self._config.tox_profile_path
        with open(path + ".tmp", "wb") as f:
            f.write(blob)
        os.replace(path + ".tmp", path)

    # перевод данных друга

    def _pubkey_hex(self, fn: int) -> str:
        return self._tox.friend_get_public_key(fn).hex()

    def _friend_name(self, fn: int) -> str:
        try:
            return self._tox.friend_get_name(fn).decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _enqueue(self, event) -> None:
        try:
            self._q.put_nowait(event)
        except asyncio.QueueFull:
            log.error("входящая очередь переполнена, событие отброшено: %r", event)

    def _cancel_file(self, fn: int, file_number: int) -> None:
        try:
            self._tox.file_control(
                fn, file_number, self._core.Tox_File_Control.TOX_FILE_CONTROL_CANCEL
            )
        except Exception:
            log.debug("CANCEL не прошёл", exc_info=True)

    # синхронные коллбэки toxcore (внутри iterate) - только _enqueue, без await

    def _on_friend_message(self, fn, mtype, message: bytes) -> None:
        self._mid += 1
        action = getattr(mtype, "name", "") == "TOX_MESSAGE_TYPE_ACTION"
        self._enqueue(
            InboundText(
                pubkey=self._pubkey_hex(fn),
                name=self._friend_name(fn),
                text=message.decode("utf-8", errors="replace"),
                message_id=self._mid,
                action=action,
            )
        )

    def _on_friend_request(self, pk: bytes, message: bytes) -> None:
        self._enqueue(
            InboundFriendRequest(pk.hex(), message.decode("utf-8", errors="replace"))
        )

    def _on_connection(self, fn, status) -> None:
        online = getattr(status, "name", "") != "TOX_CONNECTION_NONE"
        self._enqueue(InboundConnection(self._pubkey_hex(fn), self._friend_name(fn), online))

    def _on_status(self, fn, status) -> None:
        label = _STATUS_NAMES.get(getattr(status, "name", ""), "online")
        self._enqueue(InboundStatus(self._pubkey_hex(fn), self._friend_name(fn), label))

    def _on_typing(self, fn, typing: bool) -> None:
        self._enqueue(InboundTyping(self._pubkey_hex(fn), bool(typing)))

    def _on_name(self, fn, name: bytes) -> None:
        self._enqueue(InboundName(self._pubkey_hex(fn), name.decode("utf-8", errors="replace")))

    def _on_read_receipt(self, fn, message_id) -> None:
        self._enqueue(InboundReadReceipt(self._pubkey_hex(fn), int(message_id)))

    def _on_file_recv(self, fn, file_number, kind, file_size, filename: bytes) -> None:
        avatar = kind == _KIND_AVATAR
        if kind != _KIND_DATA and not avatar:
            self._cancel_file(fn, file_number)
            return
        if avatar and not file_size:
            # пустой аватар = его сняли; отвечать CANCEL не нужно, просто игнорируем
            return
        if file_size and file_size > self._max_file_bytes:
            self._cancel_file(fn, file_number)
            return
        raw = filename.decode("utf-8", errors="replace")
        name = "avatar.png" if avatar else safe_filename(raw)
        tmp = unique_path(self._tmp_dir, name)
        try:
            transfer = IncomingTransfer(file_size, tmp, self._max_file_bytes)
        except OSError:
            self._cancel_file(fn, file_number)
            return
        self._incoming[(fn, file_number)] = (transfer, name)
        try:
            self._tox.file_control(
                fn, file_number, self._core.Tox_File_Control.TOX_FILE_CONTROL_RESUME
            )
        except Exception:
            transfer.cleanup()
            self._incoming.pop((fn, file_number), None)
            self._cancel_file(fn, file_number)

    def _on_file_recv_chunk(self, fn, file_number, position, data: bytes) -> None:
        entry = self._incoming.get((fn, file_number))
        if entry is None:
            return
        transfer, name = entry
        try:
            transfer.feed(position, data)
        except TransferTooLarge:
            self._cancel_file(fn, file_number)
            transfer.cleanup()
            self._incoming.pop((fn, file_number), None)
            return
        if transfer.is_complete:
            transfer.finish()
            self._enqueue(
                InboundFileComplete(
                    pubkey=self._pubkey_hex(fn),
                    name=self._friend_name(fn),
                    filename=name,
                    path=transfer.tmp_path,
                    size=transfer.received,
                    is_image=name == "avatar.png" or is_image(name),
                )
            )
            self._incoming.pop((fn, file_number), None)

    def _discard_outgoing(self, fn: int, file_number: int) -> None:
        transfer = self._outgoing.pop((fn, file_number), None)
        if transfer is None:
            return
        transfer.close()
        if os.path.abspath(transfer.path).startswith(self._tmp_abs):
            try:
                os.remove(transfer.path)
            except OSError:
                pass

    def _on_file_chunk_request(self, fn, file_number, position, length) -> None:
        transfer = self._outgoing.get((fn, file_number))
        if transfer is None:
            return
        if length == 0:
            self._discard_outgoing(fn, file_number)
            return
        try:
            self._tox.file_send_chunk(fn, file_number, position, transfer.read_chunk(position, length))
        except Exception:
            log.debug("file_send_chunk упал, отменяю трансфер", exc_info=True)
            self._cancel_file(fn, file_number)
            self._discard_outgoing(fn, file_number)

    # ToxSender - исходящие

    def _chunk_text(self, text: str) -> list[bytes]:
        limit = getattr(self._core, "MAX_MESSAGE_LENGTH", 1372)
        chunks: list[bytes] = []
        cur = b""
        for ch in text:
            b = ch.encode("utf-8")
            if len(cur) + len(b) > limit:
                chunks.append(cur)
                cur = b""
            cur += b
        if cur or not chunks:
            chunks.append(cur)
        return chunks

    def _resolve(self, pubkey: str) -> Optional[int]:
        try:
            return self._tox.friend_by_public_key(bytes.fromhex(pubkey))
        except Exception:
            log.warning("друг %s не найден", pubkey)
            return None

    async def send_message(self, pubkey: str, text: str) -> Optional[int]:
        if not text:
            return None
        fn = self._resolve(pubkey)
        if fn is None:
            return None
        normal = self._core.Tox_Message_Type.TOX_MESSAGE_TYPE_NORMAL
        last_id = None
        for chunk in self._chunk_text(text):
            try:
                last_id = self._tox.friend_send_message(fn, normal, chunk)
            except Exception:
                log.warning("кусок сообщения не ушёл", exc_info=True)
        return last_id

    async def send_file(self, pubkey: str, path: str, filename: str) -> None:
        fn = self._resolve(pubkey)
        if fn is None:
            return
        try:
            transfer = OutgoingTransfer(path)
        except OSError:
            log.warning("не открыть файл %s", path)
            return
        try:
            file_number = self._tox.file_send(
                fn, _KIND_DATA, transfer.size, b"", filename.encode("utf-8")
            )
        except Exception:
            transfer.close()  # иначе дескриптор повиснет
            log.warning("file_send не прошёл для %s", path, exc_info=True)
            return
        self._outgoing[(fn, file_number)] = transfer

    def accept_friend(self, pubkey: str) -> None:
        try:
            self._tox.friend_add_norequest(bytes.fromhex(pubkey))
            self.save()
        except Exception:
            log.warning("не удалось принять заявку %s", pubkey, exc_info=True)

    def reject_friend(self, pubkey: str) -> None:
        log.info("заявка %s отклонена", pubkey)

    def add_friend(self, tox_id: str, message: str) -> bool:
        try:
            self._tox.friend_add(bytes.fromhex(tox_id), (message or "Привет").encode("utf-8"))
            self.save()
            return True
        except Exception:
            log.warning("не удалось добавить %s", tox_id, exc_info=True)
            return False

    def delete_friend(self, pubkey: str) -> bool:
        fn = self._resolve(pubkey)
        if fn is None:
            return False
        try:
            self._tox.friend_delete(fn)
            self.save()
            return True
        except Exception:
            log.warning("не удалось удалить %s", pubkey, exc_info=True)
            return False

    def set_self_name(self, name: str) -> None:
        self._tox.name = name.encode("utf-8")
        self.save()

    def set_self_status_message(self, message: str) -> None:
        self._tox.status_message = message.encode("utf-8")
        self.save()

    def set_self_status(self, status: str) -> None:
        mapping = {
            "online": "TOX_USER_STATUS_NONE",
            "away": "TOX_USER_STATUS_AWAY",
            "busy": "TOX_USER_STATUS_BUSY",
        }
        enum_name = mapping.get(status, "TOX_USER_STATUS_NONE")
        self._tox.status = getattr(self._core.Tox_User_Status, enum_name)

    def friends_summary(self) -> list[dict]:
        out = []
        for fn in self._tox.friend_list:
            try:
                online = getattr(
                    self._tox.friend_get_connection_status(fn), "name", ""
                ) != "TOX_CONNECTION_NONE"
            except Exception:
                online = False
            out.append(
                {"pubkey": self._pubkey_hex(fn), "name": self._friend_name(fn), "online": online}
            )
        return out
