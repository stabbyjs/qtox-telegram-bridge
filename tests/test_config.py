import pytest

from bridge.config import BridgeConfig, ConfigError, TopicMode


def _base_env(**overrides):
    env = {
        "TOX_PROFILE_PATH": "/data/profile.tox",
        "TOX_PROFILE_PASSWORD": "secret",
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_OWNER_ID": "42",
    }
    env.update(overrides)
    return env


def test_from_env_parses_required_fields():
    cfg = BridgeConfig.from_env(_base_env())
    assert cfg.tox_profile_path == "/data/profile.tox"
    assert cfg.tox_profile_password == "secret"
    assert cfg.telegram_bot_token == "123:abc"
    assert cfg.telegram_owner_id == 42
    assert cfg.topic_mode is TopicMode.PRIVATE
    assert cfg.max_file_mb == 50  # default
    assert cfg.db_path == "./data/bridge.db"  # default


def test_missing_bot_token_raises():
    env = _base_env()
    del env["TELEGRAM_BOT_TOKEN"]
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        BridgeConfig.from_env(env)


def test_missing_profile_password_raises():
    env = _base_env()
    del env["TOX_PROFILE_PASSWORD"]
    with pytest.raises(ConfigError, match="TOX_PROFILE_PASSWORD"):
        BridgeConfig.from_env(env)


def test_owner_id_optional_defaults_to_none():
    env = _base_env()
    del env["TELEGRAM_OWNER_ID"]
    cfg = BridgeConfig.from_env(env)
    assert cfg.telegram_owner_id is None


def test_owner_id_non_numeric_raises():
    with pytest.raises(ConfigError, match="TELEGRAM_OWNER_ID"):
        BridgeConfig.from_env(_base_env(TELEGRAM_OWNER_ID="not-a-number"))


def test_max_file_mb_parsed():
    cfg = BridgeConfig.from_env(_base_env(MAX_FILE_MB="20"))
    assert cfg.max_file_mb == 20


def test_max_file_mb_invalid_raises():
    with pytest.raises(ConfigError, match="MAX_FILE_MB"):
        BridgeConfig.from_env(_base_env(MAX_FILE_MB="huge"))


def test_topic_mode_private_default():
    cfg = BridgeConfig.from_env(_base_env())
    assert cfg.topic_mode is TopicMode.PRIVATE


def test_topic_mode_supergroup_requires_chat_id():
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        BridgeConfig.from_env(_base_env(TELEGRAM_TOPIC_MODE="supergroup"))


def test_topic_mode_supergroup_with_chat_id_ok():
    cfg = BridgeConfig.from_env(
        _base_env(TELEGRAM_TOPIC_MODE="supergroup", TELEGRAM_CHAT_ID="-1001234")
    )
    assert cfg.topic_mode is TopicMode.SUPERGROUP
    assert cfg.telegram_chat_id == -1001234


def test_invalid_topic_mode_raises():
    with pytest.raises(ConfigError, match="TELEGRAM_TOPIC_MODE"):
        BridgeConfig.from_env(_base_env(TELEGRAM_TOPIC_MODE="bogus"))


def test_target_chat_id_private_is_owner():
    cfg = BridgeConfig.from_env(_base_env())
    assert cfg.target_chat_id() == 42


def test_target_chat_id_supergroup_is_group():
    cfg = BridgeConfig.from_env(
        _base_env(TELEGRAM_TOPIC_MODE="supergroup", TELEGRAM_CHAT_ID="-1001234")
    )
    assert cfg.target_chat_id() == -1001234


def test_target_chat_id_private_without_owner_raises():
    env = _base_env()
    del env["TELEGRAM_OWNER_ID"]
    cfg = BridgeConfig.from_env(env)
    with pytest.raises(ConfigError, match="owner"):
        cfg.target_chat_id()
