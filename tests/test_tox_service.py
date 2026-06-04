import asyncio

import pytest

from bridge.config import BridgeConfig
from bridge.models import (
    InboundConnection,
    InboundFileComplete,
    InboundFriendRequest,
    InboundName,
    InboundReadReceipt,
    InboundStatus,
    InboundText,
    InboundTyping,
)
from bridge.tox_service import ToxService


class _E:
    def __init__(self, name):
        self.name = name


class FakeMsgType:
    TOX_MESSAGE_TYPE_NORMAL = _E("TOX_MESSAGE_TYPE_NORMAL")
    TOX_MESSAGE_TYPE_ACTION = _E("TOX_MESSAGE_TYPE_ACTION")


class FakeFileControl:
    TOX_FILE_CONTROL_RESUME = _E("RESUME")
    TOX_FILE_CONTROL_PAUSE = _E("PAUSE")
    TOX_FILE_CONTROL_CANCEL = _E("CANCEL")


class FakeUserStatus:
    TOX_USER_STATUS_NONE = _E("TOX_USER_STATUS_NONE")
    TOX_USER_STATUS_AWAY = _E("TOX_USER_STATUS_AWAY")
    TOX_USER_STATUS_BUSY = _E("TOX_USER_STATUS_BUSY")


class FakeCore:
    Tox_Message_Type = FakeMsgType
    Tox_File_Control = FakeFileControl
    Tox_User_Status = FakeUserStatus
    MAX_MESSAGE_LENGTH = 1372


class FakeEnc:
    def __init__(self, encrypted=True):
        self.encrypted = encrypted

    def is_data_encrypted(self, raw):
        return self.encrypted

    def pass_decrypt(self, raw, pw):
        assert isinstance(pw, bytes)
        return b"DECRYPTED"


class FakeTox:
    def __init__(self):
        self.sent = []
        self.controls = []
        self.chunks = []
        self.norequest = []
        self.added = []
        self.deleted = []
        self.name = b""
        self.status_message = b""
        self.status = None
        self._pk = {0: bytes.fromhex("aa" * 32)}
        self._names = {0: b"Alice"}
        self._msg_id = 0
        self.friend_list = [0]

    def friend_by_public_key(self, pk):
        for fn, k in self._pk.items():
            if k == pk:
                return fn
        raise KeyError(pk)

    def friend_get_public_key(self, fn):
        return self._pk[fn]

    def friend_get_name(self, fn):
        return self._names[fn]

    def friend_get_connection_status(self, fn):
        return _E("TOX_CONNECTION_UDP")

    def friend_send_message(self, fn, mtype, message):
        self._msg_id += 1
        self.sent.append((fn, message))
        return self._msg_id

    def friend_add_norequest(self, pk):
        self.norequest.append(pk)
        return 0

    def friend_add(self, addr, message):
        self.added.append((addr, message))
        return 1

    def friend_delete(self, fn):
        self.deleted.append(fn)

    def file_control(self, fn, file_number, control):
        self.controls.append((fn, file_number, control.name))

    def file_send(self, fn, kind, size, file_id, filename):
        return 0

    def file_send_chunk(self, fn, file_number, position, data):
        self.chunks.append((position, data))


def _config():
    return BridgeConfig.from_env(
        {
            "TOX_PROFILE_PATH": "/p.tox",
            "TOX_PROFILE_PASSWORD": "pw",
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_OWNER_ID": "42",
        }
    )


def _service(tmp_path):
    q: asyncio.Queue = asyncio.Queue()
    svc = ToxService(_config(), q, store=None, core=FakeCore(), enc=FakeEnc(), tmp_dir=str(tmp_path))
    svc._tox = FakeTox()
    svc._mid = 0
    svc.save = lambda: None  # без записи реального профиля в тестах
    return svc, q


# --- профиль ---

def test_load_profile_decrypts():
    assert ToxService.load_profile(b"raw", "pw", FakeEnc(True)) == b"DECRYPTED"


def test_load_profile_passthrough():
    assert ToxService.load_profile(b"plain", "pw", FakeEnc(False)) == b"plain"


# --- исходящий текст ---

async def test_send_message_returns_id(tmp_path):
    svc, q = _service(tmp_path)
    assert await svc.send_message("aa" * 32, "hi") == 1
    assert svc._tox.sent == [(0, b"hi")]


async def test_send_message_chunks_long(tmp_path):
    svc, q = _service(tmp_path)
    await svc.send_message("aa" * 32, "x" * 3000)
    assert len(svc._tox.sent) >= 2
    assert b"".join(m[1] for m in svc._tox.sent) == ("x" * 3000).encode()


async def test_send_message_does_not_split_multibyte(tmp_path):
    svc, q = _service(tmp_path)
    svc._core.MAX_MESSAGE_LENGTH = 5
    text = "ё" * 10  # каждый символ 2 байта
    await svc.send_message("aa" * 32, text)
    for _, chunk in svc._tox.sent:
        chunk.decode("utf-8")  # не должно падать
    assert b"".join(m[1] for m in svc._tox.sent).decode() == text


async def test_send_empty_noop(tmp_path):
    svc, q = _service(tmp_path)
    assert await svc.send_message("aa" * 32, "") is None
    assert svc._tox.sent == []


async def test_send_unknown_friend_noop(tmp_path):
    svc, q = _service(tmp_path)
    assert await svc.send_message("ff" * 32, "hi") is None
    assert svc._tox.sent == []


# --- входящие коллбэки ---

async def test_on_friend_message(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_friend_message(0, FakeMsgType.TOX_MESSAGE_TYPE_NORMAL, b"hello")
    ev = q.get_nowait()
    assert isinstance(ev, InboundText)
    assert ev.pubkey == "aa" * 32 and ev.name == "Alice" and ev.text == "hello"
    assert ev.message_id == 1 and ev.action is False


async def test_on_friend_message_action(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_friend_message(0, FakeMsgType.TOX_MESSAGE_TYPE_ACTION, b"machet")
    assert q.get_nowait().action is True


async def test_on_friend_message_bad_utf8(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_friend_message(0, FakeMsgType.TOX_MESSAGE_TYPE_NORMAL, b"\xff bad")
    assert isinstance(q.get_nowait(), InboundText)


async def test_on_friend_request(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_friend_request(bytes.fromhex("cc" * 32), b"add")
    ev = q.get_nowait()
    assert isinstance(ev, InboundFriendRequest) and ev.pubkey == "cc" * 32


async def test_on_connection(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_connection(0, _E("TOX_CONNECTION_UDP"))
    ev = q.get_nowait()
    assert isinstance(ev, InboundConnection) and ev.online is True
    svc._on_connection(0, _E("TOX_CONNECTION_NONE"))
    assert q.get_nowait().online is False


async def test_on_status(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_status(0, _E("TOX_USER_STATUS_BUSY"))
    ev = q.get_nowait()
    assert isinstance(ev, InboundStatus) and ev.status == "busy"


async def test_on_typing(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_typing(0, True)
    ev = q.get_nowait()
    assert isinstance(ev, InboundTyping) and ev.typing is True


async def test_on_name(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_name(0, b"NewName")
    ev = q.get_nowait()
    assert isinstance(ev, InboundName) and ev.name == "NewName"


async def test_on_read_receipt(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_read_receipt(0, 99)
    ev = q.get_nowait()
    assert isinstance(ev, InboundReadReceipt) and ev.message_id == 99


# --- входящие файлы ---

async def test_file_recv_wrong_kind_cancels(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_file_recv(0, 1, 7, 10, b"x.bin")  # kind=7 неизвестный
    assert svc._tox.controls == [(0, 1, "CANCEL")]


async def test_file_recv_oversize_cancels(tmp_path):
    svc, q = _service(tmp_path)
    svc._max_file_bytes = 5
    svc._on_file_recv(0, 1, 0, 10, b"big.bin")
    assert svc._tox.controls == [(0, 1, "CANCEL")]


async def test_file_recv_resumes_and_registers(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_file_recv(0, 1, 0, 4, b"a.bin")
    assert (0, 1) in svc._incoming
    assert svc._tox.controls == [(0, 1, "RESUME")]


async def test_file_recv_chunk_completes(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_file_recv(0, 1, 0, 4, b"a.bin")
    svc._on_file_recv_chunk(0, 1, 0, b"abcd")
    ev = q.get_nowait()
    assert isinstance(ev, InboundFileComplete) and ev.size == 4
    assert (0, 1) not in svc._incoming


async def test_file_recv_chunk_too_large_cancels(tmp_path):
    svc, q = _service(tmp_path)
    svc._max_file_bytes = 3
    svc._on_file_recv(0, 1, 0, 0, b"a.bin")
    svc._on_file_recv_chunk(0, 1, 0, b"abcdef")
    assert (0, 1) not in svc._incoming
    assert (0, 1, "CANCEL") in svc._tox.controls
    assert q.empty()


async def test_avatar_accepted(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_file_recv(0, 2, 1, 3, b"")  # kind=1 avatar
    svc._on_file_recv_chunk(0, 2, 0, b"png")
    ev = q.get_nowait()
    assert isinstance(ev, InboundFileComplete) and ev.is_image is True


async def test_empty_avatar_ignored(tmp_path):
    svc, q = _service(tmp_path)
    svc._on_file_recv(0, 2, 1, 0, b"")  # снятый аватар - просто игнор
    assert svc._tox.controls == []
    assert (0, 2) not in svc._incoming


# --- исходящие файлы ---

async def test_send_file_registers_outgoing(tmp_path):
    svc, q = _service(tmp_path)
    src = tmp_path / "out.bin"
    src.write_bytes(b"hello")
    await svc.send_file("aa" * 32, str(src), "out.bin")
    assert (0, 0) in svc._outgoing


async def test_send_file_unknown_friend_noop(tmp_path):
    svc, q = _service(tmp_path)
    src = tmp_path / "out.bin"
    src.write_bytes(b"hello")
    await svc.send_file("ff" * 32, str(src), "out.bin")
    assert svc._outgoing == {}


async def test_chunk_request_sends_and_discards(tmp_path):
    svc, q = _service(tmp_path)
    src = tmp_path / "out.bin"
    src.write_bytes(b"0123456789")
    await svc.send_file("aa" * 32, str(src), "out.bin")
    svc._on_file_chunk_request(0, 0, 0, 4)
    assert svc._tox.chunks == [(0, b"0123")]
    svc._on_file_chunk_request(0, 0, 0, 0)  # length 0 = конец
    assert (0, 0) not in svc._outgoing
    assert not src.exists()  # временный исходник удалён


# --- управление ---

def test_accept_friend(tmp_path):
    svc, q = _service(tmp_path)
    svc.accept_friend("bb" * 32)
    assert svc._tox.norequest == [bytes.fromhex("bb" * 32)]


def test_add_friend(tmp_path):
    svc, q = _service(tmp_path)
    assert svc.add_friend("cc" * 38, "hi") is True
    assert svc._tox.added


def test_delete_friend(tmp_path):
    svc, q = _service(tmp_path)
    assert svc.delete_friend("aa" * 32) is True
    assert svc._tox.deleted == [0]


def test_delete_unknown_friend(tmp_path):
    svc, q = _service(tmp_path)
    assert svc.delete_friend("ff" * 32) is False


def test_set_self_name(tmp_path):
    svc, q = _service(tmp_path)
    svc.set_self_name("Neo")
    assert svc._tox.name == b"Neo"


def test_set_self_status(tmp_path):
    svc, q = _service(tmp_path)
    svc.set_self_status("busy")
    assert svc._tox.status is FakeUserStatus.TOX_USER_STATUS_BUSY


def test_friends_summary(tmp_path):
    svc, q = _service(tmp_path)
    summary = svc.friends_summary()
    assert summary == [{"pubkey": "aa" * 32, "name": "Alice", "online": True}]


async def test_send_message_sdk_error_does_not_raise(tmp_path):
    svc, q = _service(tmp_path)

    def boom(*a):
        raise RuntimeError("net")

    svc._tox.friend_send_message = boom
    assert await svc.send_message("aa" * 32, "hi") is None  # не падает


async def test_chunk_request_send_error_cancels(tmp_path):
    svc, q = _service(tmp_path)
    src = tmp_path / "out.bin"
    src.write_bytes(b"0123456789")
    await svc.send_file("aa" * 32, str(src), "out.bin")

    def boom(*a):
        raise RuntimeError("net")

    svc._tox.file_send_chunk = boom
    svc._on_file_chunk_request(0, 0, 0, 4)
    assert (0, 0) not in svc._outgoing
    assert (0, 0, "CANCEL") in svc._tox.controls


def test_discard_outgoing_keeps_file_outside_tmp(tmp_path, monkeypatch):
    svc, q = _service(tmp_path)
    outside = tmp_path.parent / "keep.bin"
    outside.write_bytes(b"x")
    from bridge.file_transfer import OutgoingTransfer

    svc._outgoing[(0, 0)] = OutgoingTransfer(str(outside))
    svc._discard_outgoing(0, 0)
    assert outside.exists()  # файл вне tmp_dir не трогаем
    outside.unlink()


def test_set_self_status_message(tmp_path):
    svc, q = _service(tmp_path)
    svc.set_self_status_message("на созвоне")
    assert svc._tox.status_message == "на созвоне".encode()


async def test_enqueue_drops_when_full(tmp_path):
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    svc = ToxService(_config(), q, store=None, core=FakeCore(), enc=FakeEnc(), tmp_dir=str(tmp_path))
    svc._tox = FakeTox()
    svc._mid = 0
    svc._on_friend_message(0, FakeMsgType.TOX_MESSAGE_TYPE_NORMAL, b"one")
    svc._on_friend_message(0, FakeMsgType.TOX_MESSAGE_TYPE_NORMAL, b"two")
    assert q.qsize() == 1
