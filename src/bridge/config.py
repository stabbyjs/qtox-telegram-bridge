"""Конфигурация моста: загрузка и валидация из окружения."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping, Optional


class ConfigError(Exception):
    """Некорректная или неполная конфигурация."""


class TopicMode(enum.Enum):
    PRIVATE = "private"
    SUPERGROUP = "supergroup"


def _require(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        raise ConfigError(f"{key} обязателен и не должен быть пустым")
    return value


def _parse_int(env: Mapping[str, str], key: str, default: Optional[int]) -> Optional[int]:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} должен быть целым числом, получено: {raw!r}") from exc


@dataclass(frozen=True)
class BridgeConfig:
    tox_profile_path: str
    tox_profile_password: str
    telegram_bot_token: str
    telegram_owner_id: Optional[int]
    topic_mode: TopicMode
    telegram_chat_id: Optional[int]
    db_path: str
    tmp_dir: str
    max_file_mb: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "BridgeConfig":
        tox_profile_path = _require(env, "TOX_PROFILE_PATH")
        tox_profile_password = _require(env, "TOX_PROFILE_PASSWORD")
        telegram_bot_token = _require(env, "TELEGRAM_BOT_TOKEN")

        owner_id = _parse_int(env, "TELEGRAM_OWNER_ID", None)

        mode_raw = (env.get("TELEGRAM_TOPIC_MODE") or "private").strip().lower()
        try:
            topic_mode = TopicMode(mode_raw)
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_TOPIC_MODE должен быть 'private' или 'supergroup', получено: {mode_raw!r}"
            ) from exc

        chat_id = _parse_int(env, "TELEGRAM_CHAT_ID", None)
        if topic_mode is TopicMode.SUPERGROUP and chat_id is None:
            raise ConfigError(
                "TELEGRAM_CHAT_ID обязателен при TELEGRAM_TOPIC_MODE=supergroup"
            )

        max_file_mb = _parse_int(env, "MAX_FILE_MB", 50)

        return cls(
            tox_profile_path=tox_profile_path,
            tox_profile_password=tox_profile_password,
            telegram_bot_token=telegram_bot_token,
            telegram_owner_id=owner_id,
            topic_mode=topic_mode,
            telegram_chat_id=chat_id,
            db_path=(env.get("DB_PATH") or "./data/bridge.db").strip(),
            tmp_dir=(env.get("TMP_DIR") or "./data/tmp").strip(),
            max_file_mb=max_file_mb,
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        )

    def target_chat_id(self) -> int:
        """Chat id, в котором живут топики: владелец (private) или группа (supergroup)."""
        if self.topic_mode is TopicMode.SUPERGROUP:
            assert self.telegram_chat_id is not None  # гарантировано валидацией
            return self.telegram_chat_id
        if self.telegram_owner_id is None:
            raise ConfigError(
                "owner id ещё не известен - отправь боту /start или задай TELEGRAM_OWNER_ID"
            )
        return self.telegram_owner_id
