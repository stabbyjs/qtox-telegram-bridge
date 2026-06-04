"""События, которыми обмениваются Tox- и Telegram-стороны.

Публичные ключи на уровне домена - hex-строки в нижнем регистре; перевод
в bytes и обратно делает tox-адаптер.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundText:
    """Текст от собеседника (Tox → Telegram)."""

    pubkey: str
    name: str
    text: str
    message_id: int
    action: bool = False  # сообщение вида "/me ..." (TOX_MESSAGE_TYPE_ACTION)


@dataclass(frozen=True)
class InboundFileComplete:
    """Принятый целиком файл (Tox → Telegram)."""

    pubkey: str
    name: str
    filename: str
    path: str
    size: int
    is_image: bool


@dataclass(frozen=True)
class InboundFriendRequest:
    """Заявка в друзья (Tox → Telegram)."""

    pubkey: str
    message: str


@dataclass(frozen=True)
class InboundConnection:
    """Друг ушёл в онлайн/офлайн."""

    pubkey: str
    name: str
    online: bool


@dataclass(frozen=True)
class InboundStatus:
    """Друг сменил статус (none/away/busy)."""

    pubkey: str
    name: str
    status: str


@dataclass(frozen=True)
class InboundTyping:
    """Друг печатает / перестал печатать."""

    pubkey: str
    typing: bool


@dataclass(frozen=True)
class InboundName:
    """Друг сменил ник - нужно переименовать топик."""

    pubkey: str
    name: str


@dataclass(frozen=True)
class InboundReadReceipt:
    """Подтверждение, что собеседник прочитал наше сообщение."""

    pubkey: str
    message_id: int
