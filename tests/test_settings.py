import pytest

from bridge.settings import Settings
from bridge.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / "s.db"), clock=lambda: 1)
    await s.init()
    yield s
    await s.close()


async def test_defaults(store):
    cfg = Settings(store)
    await cfg.load()
    assert cfg.get("notify_status") is True
    assert cfg.get("silent") is False
    assert cfg.get("unknown_key") is False


async def test_set_and_persist(store):
    cfg = Settings(store)
    await cfg.load()
    await cfg.set("silent", True)
    assert cfg.get("silent") is True
    # новый экземпляр читает из Store
    cfg2 = Settings(store)
    await cfg2.load()
    assert cfg2.get("silent") is True


async def test_toggle(store):
    cfg = Settings(store)
    await cfg.load()
    assert await cfg.toggle("notify_typing") is False
    assert await cfg.toggle("notify_typing") is True


async def test_set_ignores_unknown_key(store):
    cfg = Settings(store)
    await cfg.load()
    await cfg.set("nope", True)
    assert cfg.get("nope") is False


async def test_mute_unmute_persist(store):
    cfg = Settings(store)
    await cfg.load()
    assert cfg.is_muted("aa") is False
    await cfg.mute("aa")
    assert cfg.is_muted("aa") is True
    cfg2 = Settings(store)
    await cfg2.load()
    assert cfg2.is_muted("aa") is True
    await cfg2.unmute("aa")
    assert cfg2.is_muted("aa") is False


async def test_view_shape(store):
    cfg = Settings(store)
    await cfg.load()
    view = cfg.view()
    assert len(view) == len(Settings.ORDER)
    for key, label, enabled in view:
        assert key in Settings.DEFAULTS
        assert isinstance(label, str) and isinstance(enabled, bool)
