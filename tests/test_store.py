import pytest

from bridge.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / "test.db"), clock=lambda: 1000)
    await s.init()
    yield s
    await s.close()


async def test_conn_raises_before_init(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    with pytest.raises(RuntimeError):
        await s.topic_for_pubkey("aa")


async def test_link_and_lookup_friend(store):
    await store.link_friend("aabb", "Alice", topic_id=7)
    assert await store.topic_for_pubkey("aabb") == 7
    assert await store.pubkey_for_topic(7) == "aabb"
    assert await store.name_for_pubkey("aabb") == "Alice"


async def test_unknown_lookups_return_none(store):
    assert await store.topic_for_pubkey("zz") is None
    assert await store.pubkey_for_topic(999) is None
    assert await store.name_for_pubkey("zz") is None


async def test_link_updates_on_conflict(store):
    await store.link_friend("aabb", "Alice", topic_id=7)
    await store.link_friend("aabb", "Renamed", topic_id=7)
    assert await store.name_for_pubkey("aabb") == "Renamed"
    assert await store.topic_for_pubkey("aabb") == 7


async def test_update_name(store):
    await store.link_friend("aabb", "Alice", topic_id=7)
    await store.update_name("aabb", "Bob")
    assert await store.name_for_pubkey("aabb") == "Bob"


async def test_unlink_friend(store):
    await store.link_friend("aabb", "Alice", topic_id=7)
    await store.unlink_friend("aabb")
    assert await store.topic_for_pubkey("aabb") is None


async def test_all_friends(store):
    await store.link_friend("aa", "A", topic_id=1)
    await store.link_friend("bb", "B", topic_id=2)
    keys = {f["pubkey"] for f in await store.all_friends()}
    assert keys == {"aa", "bb"}


async def test_dedup_roundtrip(store):
    assert await store.is_duplicate("k1") is False
    await store.mark_seen("k1")
    assert await store.is_duplicate("k1") is True
    assert await store.is_duplicate("k2") is False


async def test_meta_roundtrip(store):
    assert await store.get_meta("owner_id") is None
    await store.set_meta("owner_id", "42")
    assert await store.get_meta("owner_id") == "42"
    await store.set_meta("owner_id", "43")
    assert await store.get_meta("owner_id") == "43"


async def test_friend_request_add_and_pop(store):
    rid = await store.add_request("ccdd", "add me")
    assert rid is not None
    assert await store.pop_request(rid) == "ccdd"
    assert await store.pop_request(rid) is None  # уже снята


async def test_friend_request_dedup_same_pubkey(store):
    rid1 = await store.add_request("ccdd", "first")
    rid2 = await store.add_request("ccdd", "second")
    assert rid1 is not None
    assert rid2 is None  # повторная заявка от того же pubkey игнорируется


async def test_sent_message_roundtrip(store):
    await store.remember_sent("aabb", tox_msg_id=5, tg_msg_id=101, thread_id=7)
    rec = await store.pop_sent("aabb", 5)
    assert rec == {"tg_msg_id": 101, "thread_id": 7}
    assert await store.pop_sent("aabb", 5) is None
