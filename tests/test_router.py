import pytest

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
from bridge.router import Router
from bridge.store import Store


class FakeSink:
    def __init__(self):
        self.created = []
        self.renamed = []
        self.texts = []
        self.files = []
        self.requests = []
        self.notes = []
        self.typing = []
        self.reads = []
        self._thread = 100
        self._msg = 500

    async def create_topic(self, name):
        self._thread += 1
        self.created.append((name, self._thread))
        return self._thread

    async def edit_topic_name(self, thread_id, name):
        self.renamed.append((thread_id, name))

    async def send_text(self, thread_id, text):
        self._msg += 1
        self.texts.append((thread_id, text))
        return self._msg

    async def send_file(self, thread_id, path, as_photo):
        self.files.append((thread_id, path, as_photo))

    async def send_friend_request(self, rid, pubkey, message):
        self.requests.append((rid, pubkey, message))

    async def notify(self, thread_id, text):
        self.notes.append((thread_id, text))

    async def send_typing(self, thread_id):
        self.typing.append(thread_id)

    async def mark_read(self, thread_id, message_id):
        self.reads.append((thread_id, message_id))


class FakeTox:
    tox_id = "TOXID123"

    def __init__(self):
        self.sent = []
        self.sent_files = []
        self.accepted = []
        self.rejected = []
        self.added = []
        self.deleted = []
        self.self_name = None
        self.self_status_message = None
        self.self_status = None
        self._next_id = 0

    async def send_message(self, pubkey, text):
        self._next_id += 1
        self.sent.append((pubkey, text))
        return self._next_id

    async def send_file(self, pubkey, path, filename):
        self.sent_files.append((pubkey, path, filename))

    def accept_friend(self, pubkey):
        self.accepted.append(pubkey)

    def reject_friend(self, pubkey):
        self.rejected.append(pubkey)

    def add_friend(self, tox_id, message):
        self.added.append((tox_id, message))
        return True

    def delete_friend(self, pubkey):
        self.deleted.append(pubkey)
        return True

    def set_self_name(self, name):
        self.self_name = name

    def set_self_status_message(self, message):
        self.self_status_message = message

    def set_self_status(self, status):
        self.self_status = status

    def friends_summary(self):
        return [
            {"pubkey": "aa", "name": "Alice", "online": True},
            {"pubkey": "bb", "name": "Bob", "online": False},
        ]


@pytest.fixture
async def ctx(tmp_path):
    store = Store(str(tmp_path / "r.db"), clock=lambda: 1)
    await store.init()
    sink, tox = FakeSink(), FakeTox()
    router = Router(store=store, sink=sink, tox=tox)
    yield router, store, sink, tox
    await store.close()


async def test_inbound_text_new_friend_creates_topic(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    assert sink.created == [("Alice", 101)]
    assert sink.texts == [(101, "hi")]


async def test_inbound_text_known_friend_reuses_topic(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_inbound_text(InboundText("aabb", "Alice", "yo", message_id=2))
    assert len(sink.created) == 1
    assert sink.texts == [(101, "hi"), (101, "yo")]


async def test_inbound_text_dedup(ctx):
    router, store, sink, tox = ctx
    msg = InboundText("aabb", "Alice", "hi", message_id=1)
    await router.handle_inbound_text(msg)
    await router.handle_inbound_text(msg)
    assert sink.texts == [(101, "hi")]


async def test_inbound_action_message_formatted(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "машет", 1, action=True))
    assert sink.texts == [(101, "* машет")]


async def test_inbound_image_as_photo_and_tmp_removed(ctx, tmp_path):
    router, store, sink, tox = ctx
    f = tmp_path / "pic.png"
    f.write_bytes(b"x")
    await router.handle_inbound_file(
        InboundFileComplete("aabb", "Alice", "pic.png", str(f), 1, is_image=True)
    )
    assert sink.files == [(101, str(f), True)]
    assert not f.exists()  # временная копия удалена после отправки


async def test_inbound_file_dedup(ctx, tmp_path):
    router, store, sink, tox = ctx
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    event = InboundFileComplete("aabb", "Alice", "a.pdf", str(f), 1, is_image=False)
    await router.handle_inbound_file(event)
    await router.handle_inbound_file(event)
    assert len(sink.files) == 1


async def test_friend_request_persisted_and_forwarded(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_friend_request(InboundFriendRequest("ccdd", "add me"))
    assert len(sink.requests) == 1
    rid, pubkey, message = sink.requests[0]
    assert pubkey == "ccdd"
    # повторная заявка от того же pubkey не дублируется
    await router.handle_inbound_friend_request(InboundFriendRequest("ccdd", "again"))
    assert len(sink.requests) == 1


async def test_friend_decision_accept(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_friend_request(InboundFriendRequest("ccdd", "hi"))
    rid = sink.requests[0][0]
    await router.handle_friend_decision(rid, accept=True)
    assert tox.accepted == ["ccdd"]


async def test_friend_decision_reject(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_friend_request(InboundFriendRequest("ccdd", "hi"))
    rid = sink.requests[0][0]
    await router.handle_friend_decision(rid, accept=False)
    assert tox.rejected == ["ccdd"]


async def test_friend_decision_stale_rid_noop(ctx):
    router, store, sink, tox = ctx
    await router.handle_friend_decision(999, accept=True)
    assert tox.accepted == []


async def test_inbound_connection_notifies_existing_topic(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_inbound_connection(InboundConnection("aabb", "Alice", online=True))
    assert sink.notes == [(101, "в сети")]


async def test_inbound_connection_without_topic_silent(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_connection(InboundConnection("zz", "X", online=False))
    assert sink.notes == []  # топик не создаётся ради статуса


async def test_inbound_status_notifies(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_inbound_status(InboundStatus("aabb", "Alice", "busy"))
    assert sink.notes == [(101, "не беспокоить")]


async def test_inbound_typing(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_inbound_typing(InboundTyping("aabb", typing=True))
    assert sink.typing == [101]
    await router.handle_inbound_typing(InboundTyping("aabb", typing=False))
    assert sink.typing == [101]  # окончание печати не шлём


async def test_inbound_name_renames_topic(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_inbound_name(InboundName("aabb", "Alice 2"))
    assert sink.renamed == [(101, "Alice 2")]
    assert await store.name_for_pubkey("aabb") == "Alice 2"


async def test_inbound_read_receipt_marks_read(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_outbound_text(101, "reply", tg_msg_id=777)
    tox_msg_id = tox.sent and 1  # FakeTox вернул 1
    await router.handle_inbound_read_receipt(InboundReadReceipt("aabb", tox_msg_id))
    assert sink.reads == [(101, 777)]


async def test_outbound_text_routes_and_remembers(ctx):
    router, store, sink, tox = ctx
    await router.handle_inbound_text(InboundText("aabb", "Alice", "hi", message_id=1))
    await router.handle_outbound_text(101, "reply", tg_msg_id=777)
    assert tox.sent == [("aabb", "reply")]


async def test_outbound_text_unknown_topic_noop(ctx):
    router, store, sink, tox = ctx
    await router.handle_outbound_text(555, "x", tg_msg_id=1)
    assert tox.sent == []


async def test_outbound_file_unknown_topic_noop(ctx):
    router, store, sink, tox = ctx
    await router.handle_outbound_file(555, "/tmp/x", "x")
    assert tox.sent_files == []


async def test_command_toxid(ctx):
    router, store, sink, tox = ctx
    assert "TOXID123" in await router.handle_command("toxid", "")


async def test_command_setname(ctx):
    router, store, sink, tox = ctx
    await router.handle_command("setname", "Neo")
    assert tox.self_name == "Neo"


async def test_command_status_change(ctx):
    router, store, sink, tox = ctx
    await router.handle_command("away", "")
    assert tox.self_status == "away"


async def test_command_add_friend(ctx):
    router, store, sink, tox = ctx
    tox_id = "ab" * 38  # валидный Tox ID = 76 hex-символов
    await router.handle_command("add", f"{tox_id} hello there")
    assert tox.added == [(tox_id, "hello there")]


async def test_command_add_invalid_tox_id(ctx):
    router, store, sink, tox = ctx
    out = await router.handle_command("add", "ABCD short")
    assert "Неверный" in out
    assert tox.added == []


async def test_command_del_friend(ctx):
    router, store, sink, tox = ctx
    await router.handle_command("del", "aabb")
    assert tox.deleted == ["aabb"]


async def test_command_friends_list(ctx):
    router, store, sink, tox = ctx
    out = await router.handle_command("friends", "")
    assert "Alice" in out and "Bob" in out


async def test_command_status_summary(ctx):
    router, store, sink, tox = ctx
    out = await router.handle_command("status", "")
    assert "2" in out and "1" in out  # 2 контакта, 1 в сети


async def test_command_unknown(ctx):
    router, store, sink, tox = ctx
    assert "Неизвестная" in await router.handle_command("wat", "")
