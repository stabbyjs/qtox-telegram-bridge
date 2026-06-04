from types import SimpleNamespace

import pytest

from bridge.config import BridgeConfig
from bridge.telegram_service import TelegramCallbacks, TelegramService

OWNER = 42


class FakeBot:
    def __init__(self):
        self.calls = []
        self._tid = 1000
        self._mid = 2000

    async def create_forum_topic(self, chat_id, name, **kw):
        self._tid += 1
        self.calls.append(("create", chat_id, name))
        return SimpleNamespace(message_thread_id=self._tid)

    async def edit_forum_topic(self, chat_id, message_thread_id, name, **kw):
        self.calls.append(("edit_topic", message_thread_id, name))

    async def send_message(self, chat_id, text, message_thread_id=None, reply_markup=None, **kw):
        self._mid += 1
        self.calls.append(("msg", chat_id, message_thread_id, text, reply_markup))
        return SimpleNamespace(message_id=self._mid)

    async def send_photo(self, chat_id, photo, message_thread_id=None, **kw):
        self.calls.append(("photo", chat_id, message_thread_id, photo))

    async def send_document(self, chat_id, document, message_thread_id=None, **kw):
        self.calls.append(("doc", chat_id, message_thread_id, document))

    async def send_chat_action(self, chat_id, action, message_thread_id=None, **kw):
        self.calls.append(("action", message_thread_id, action))

    async def set_message_reaction(self, chat_id, message_id, reaction, **kw):
        self.calls.append(("reaction", message_id))

    async def download(self, file, destination):
        with open(destination, "wb") as f:
            f.write(b"data")
        self.calls.append(("download", destination))


def _config(**ov):
    env = {
        "TOX_PROFILE_PATH": "/p.tox",
        "TOX_PROFILE_PASSWORD": "x",
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_OWNER_ID": str(OWNER),
    }
    env.update(ov)
    return BridgeConfig.from_env(env)


class Recorder:
    def __init__(self):
        self.texts = []
        self.files = []
        self.decisions = []
        self.owner_set = []
        self.commands = []
        self.toggled = []
        self._view = [("notify_status", "Уведомления о статусах", True), ("silent", "Беззвучные", False)]

    async def on_outbound_text(self, thread_id, text, tg_msg_id):
        self.texts.append((thread_id, text, tg_msg_id))

    async def on_outbound_file(self, thread_id, path, filename):
        self.files.append((thread_id, path, filename))

    async def on_friend_decision(self, rid, accept):
        self.decisions.append((rid, accept))

    async def on_owner_start(self, owner_id):
        self.owner_set.append(owner_id)

    async def on_command(self, name, args, thread_id):
        self.commands.append((name, args, thread_id))
        return f"ответ:{name}"

    async def on_settings(self):
        return self._view

    async def on_toggle(self, key):
        self.toggled.append(key)
        self._view = [(k, lbl, (not v if k == key else v)) for k, lbl, v in self._view]
        return self._view


def _make(tmp_path, config=None):
    bot, rec = FakeBot(), Recorder()
    cb = TelegramCallbacks(
        on_outbound_text=rec.on_outbound_text,
        on_outbound_file=rec.on_outbound_file,
        on_friend_decision=rec.on_friend_decision,
        on_owner_start=rec.on_owner_start,
        on_command=rec.on_command,
        on_settings=rec.on_settings,
        on_toggle=rec.on_toggle,
    )
    svc = TelegramService(bot, config or _config(), cb, tmp_dir=str(tmp_path))
    return svc, bot, rec


@pytest.fixture
def svc(tmp_path):
    return _make(tmp_path)


def _owner_msg(**kw):
    base = dict(
        chat=SimpleNamespace(id=OWNER),
        from_user=SimpleNamespace(id=OWNER),
        message_thread_id=1001,
        is_topic_message=True,
        text=None,
        document=None,
        photo=None,
        message_id=5555,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_create_topic_uses_owner_chat(svc):
    service, bot, rec = svc
    tid = await service.create_topic("Alice")
    assert tid == 1001
    assert ("create", OWNER, "Alice") in bot.calls


async def test_send_text_returns_message_id(svc):
    service, bot, rec = svc
    mid = await service.send_text(1001, "hello")
    assert mid == 2001
    assert ("msg", OWNER, 1001, "hello", None) in bot.calls


async def test_edit_topic_name(svc):
    service, bot, rec = svc
    await service.edit_topic_name(1001, "New")
    assert ("edit_topic", 1001, "New") in bot.calls


async def test_send_file_photo_and_document(svc):
    service, bot, rec = svc
    await service.send_file(1001, "/tmp/a.png", as_photo=True)
    await service.send_file(1001, "/tmp/a.pdf", as_photo=False)
    assert any(c[0] == "photo" and c[3].path == "/tmp/a.png" for c in bot.calls)
    assert any(c[0] == "doc" and c[3].path == "/tmp/a.pdf" for c in bot.calls)


async def test_send_typing(svc):
    service, bot, rec = svc
    await service.send_typing(1001)
    assert ("action", 1001, "typing") in bot.calls


async def test_mark_read(svc):
    service, bot, rec = svc
    await service.mark_read(1001, 777)
    assert ("reaction", 777) in bot.calls


async def test_friend_request_keyboard_within_limit(svc):
    service, bot, rec = svc
    await service.send_friend_request(7, "ab" * 32, "add me")
    markup = [c for c in bot.calls if c[0] == "msg"][-1][4]
    assert markup is not None
    for row in markup.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode()) <= 64


async def test_topic_text_routes_with_message_id(svc):
    service, bot, rec = svc
    await service.handle_message(_owner_msg(text="reply text"))
    assert rec.texts == [(1001, "reply text", 5555)]


async def test_message_from_stranger_ignored(svc):
    service, bot, rec = svc
    await service.handle_message(
        _owner_msg(chat=SimpleNamespace(id=999), from_user=SimpleNamespace(id=999), text="hack")
    )
    assert rec.texts == []


async def test_command_invokes_callback_and_replies(svc):
    service, bot, rec = svc
    msg = _owner_msg(text="/toxid", message_thread_id=None, is_topic_message=False)
    await service.handle_command(msg)
    assert rec.commands == [("toxid", "", 0)]
    assert any(c[0] == "msg" and c[3] == "ответ:toxid" for c in bot.calls)


async def test_command_with_args(svc):
    service, bot, rec = svc
    msg = _owner_msg(text="/add ABCD hi there", message_thread_id=None, is_topic_message=False)
    await service.handle_command(msg)
    assert rec.commands == [("add", "ABCD hi there", 0)]


async def test_command_from_stranger_ignored(svc):
    service, bot, rec = svc
    msg = _owner_msg(
        chat=SimpleNamespace(id=999), from_user=SimpleNamespace(id=999), text="/toxid"
    )
    await service.handle_command(msg)
    assert rec.commands == []


async def test_incoming_document_downloaded(svc):
    service, bot, rec = svc
    doc = SimpleNamespace(file_size=1234, file_name="report.pdf")
    await service.handle_message(_owner_msg(text=None, document=doc))
    assert rec.files and rec.files[0][0] == 1001
    assert rec.files[0][2] == "report.pdf"


async def test_incoming_oversize_document_rejected(svc):
    service, bot, rec = svc
    doc = SimpleNamespace(file_size=21 * 1024 * 1024, file_name="big.bin")
    await service.handle_message(_owner_msg(text=None, document=doc))
    assert rec.files == []
    assert any(c[0] == "msg" for c in bot.calls)


async def test_incoming_photo_downloaded(svc):
    service, bot, rec = svc
    photo = SimpleNamespace(file_size=5000, file_unique_id="abc")
    await service.handle_message(_owner_msg(text=None, photo=[photo]))
    assert rec.files and rec.files[0][2] == "photo.jpg"


async def test_callback_owner_accept(svc):
    service, bot, rec = svc
    cb = SimpleNamespace(
        data="fr:a:7",
        from_user=SimpleNamespace(id=OWNER),
        answer=_anoop,
        message=SimpleNamespace(edit_reply_markup=_anoop),
    )
    await service.handle_callback(cb)
    assert rec.decisions == [(7, True)]


async def test_callback_stranger_denied(svc):
    service, bot, rec = svc
    cb = SimpleNamespace(
        data="fr:a:7",
        from_user=SimpleNamespace(id=999),
        answer=_anoop,
        message=SimpleNamespace(edit_reply_markup=_anoop),
    )
    await service.handle_callback(cb)
    assert rec.decisions == []


async def test_start_sets_owner_when_unconfigured(tmp_path):
    env = {"TOX_PROFILE_PATH": "/p", "TOX_PROFILE_PASSWORD": "x", "TELEGRAM_BOT_TOKEN": "t"}
    service, bot, rec = _make(tmp_path, BridgeConfig.from_env(env))
    msg = SimpleNamespace(chat=SimpleNamespace(id=77), from_user=SimpleNamespace(id=77))
    await service.handle_start(msg)
    assert rec.owner_set == [77]


async def test_start_from_stranger_ignored_when_configured(svc):
    service, bot, rec = svc
    msg = SimpleNamespace(chat=SimpleNamespace(id=999), from_user=SimpleNamespace(id=999))
    await service.handle_start(msg)
    assert rec.owner_set == []


async def _anoop(*a, **k):
    return None


async def test_settings_command_shows_menu(svc):
    service, bot, rec = svc
    msg = _owner_msg(text="/settings", message_thread_id=None, is_topic_message=False)
    await service.handle_command(msg)
    markup = [c for c in bot.calls if c[0] == "msg" and c[4] is not None][-1][4]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert all(d.startswith("set:") for d in datas)
    assert "set:notify_status" in datas


async def test_settings_toggle_callback(svc):
    service, bot, rec = svc
    edited = []
    cb = SimpleNamespace(
        data="set:silent",
        from_user=SimpleNamespace(id=OWNER),
        answer=_anoop,
        message=SimpleNamespace(edit_reply_markup=lambda **kw: _record(edited, kw)),
    )
    await service.handle_callback(cb)
    assert rec.toggled == ["silent"]
    assert edited and edited[0]["reply_markup"] is not None


async def test_settings_toggle_from_stranger_denied(svc):
    service, bot, rec = svc
    cb = SimpleNamespace(
        data="set:silent",
        from_user=SimpleNamespace(id=999),
        answer=_anoop,
        message=SimpleNamespace(edit_reply_markup=_anoop),
    )
    await service.handle_callback(cb)
    assert rec.toggled == []


async def _record(store, kw):
    store.append(kw)
