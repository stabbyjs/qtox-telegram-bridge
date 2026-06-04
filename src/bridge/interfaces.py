"""Protocol-границы между чистым ядром и конкретными SDK.

`Router` зависит только от этих абстракций и потому тестируется с фейками,
а адаптеры `telegram_service` и `tox_service` их реализуют.
"""

from __future__ import annotations

from typing import Optional, Protocol


class TopicSink(Protocol):
    """Сторона Telegram: топики и отправка контента."""

    async def create_topic(self, name: str) -> int: ...

    async def edit_topic_name(self, thread_id: int, name: str) -> None: ...

    async def send_text(self, thread_id: int, text: str, silent: bool = False) -> Optional[int]:
        """Отправить текст в топик. Возвращает id сообщения в Telegram."""
        ...

    async def send_file(
        self, thread_id: int, path: str, as_photo: bool, silent: bool = False
    ) -> None: ...

    async def send_friend_request(self, rid: int, pubkey: str, message: str) -> None: ...

    async def notify(self, thread_id: int, text: str, silent: bool = False) -> None: ...

    async def send_typing(self, thread_id: int) -> None: ...

    async def mark_read(self, thread_id: int, message_id: int) -> None:
        """Отметить наше сообщение как прочитанное собеседником."""
        ...


class ToxSender(Protocol):
    """Сторона Tox: исходящие действия в сеть."""

    @property
    def tox_id(self) -> str: ...

    async def send_message(self, pubkey: str, text: str) -> Optional[int]:
        """Отправить текст другу. Возвращает tox message id последнего куска."""
        ...

    async def send_file(self, pubkey: str, path: str, filename: str) -> None: ...

    def accept_friend(self, pubkey: str) -> None: ...

    def reject_friend(self, pubkey: str) -> None: ...

    def add_friend(self, tox_id: str, message: str) -> bool: ...

    def delete_friend(self, pubkey: str) -> bool: ...

    def set_self_name(self, name: str) -> None: ...

    def set_self_status_message(self, message: str) -> None: ...

    def set_self_status(self, status: str) -> None: ...

    def friends_summary(self) -> list[dict]:
        """Список друзей: pubkey, name, online - для /friends и /status."""
        ...
