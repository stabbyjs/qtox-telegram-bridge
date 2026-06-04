"""Состояние моста в SQLite (aiosqlite): связь друг↔топик, дедуп, заявки,
карта отправленных сообщений для отметок о прочтении и служебные значения.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS friends (
    pubkey      TEXT PRIMARY KEY,
    name        TEXT,
    topic_id    INTEGER UNIQUE,
    created_at  INTEGER,
    updated_at  INTEGER
);
CREATE TABLE IF NOT EXISTS dedup (
    key         TEXT PRIMARY KEY,
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS friend_requests (
    rid         INTEGER PRIMARY KEY AUTOINCREMENT,
    pubkey      TEXT UNIQUE,
    message     TEXT,
    created_at  INTEGER
);
CREATE TABLE IF NOT EXISTS sent_messages (
    pubkey      TEXT NOT NULL,
    tox_msg_id  INTEGER NOT NULL,
    tg_msg_id   INTEGER NOT NULL,
    thread_id   INTEGER NOT NULL,
    created_at  INTEGER,
    PRIMARY KEY (pubkey, tox_msg_id)
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


class Store:
    def __init__(self, db_path: str, clock: Callable[[], int] = lambda: int(time.time())):
        self._db_path = db_path
        self._clock = clock
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        await self._purge_stale()

    async def _purge_stale(self) -> None:
        """Подчистить старьё, чтобы таблицы не росли вечно.

        Отметки об отправке без пришедшего read-receipt живут 3 дня, ключи
        дедупа - неделю (после рестарта message_id всё равно сдвигается).
        """
        now = self._clock()
        await self._conn.execute(
            "DELETE FROM sent_messages WHERE created_at < ?", (now - 3 * 86400,)
        )
        await self._conn.execute("DELETE FROM dedup WHERE created_at < ?", (now - 7 * 86400,))
        await self._conn.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store не инициализирован - вызови init()")
        return self._db

    # друзья и топики

    async def link_friend(self, pubkey: str, name: str, topic_id: int) -> None:
        now = self._clock()
        await self._conn.execute(
            """
            INSERT INTO friends (pubkey, name, topic_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pubkey) DO UPDATE SET
                name=excluded.name, topic_id=excluded.topic_id, updated_at=excluded.updated_at
            """,
            (pubkey, name, topic_id, now, now),
        )
        await self._conn.commit()

    async def update_name(self, pubkey: str, name: str) -> None:
        await self._conn.execute(
            "UPDATE friends SET name=?, updated_at=? WHERE pubkey=?",
            (name, self._clock(), pubkey),
        )
        await self._conn.commit()

    async def unlink_friend(self, pubkey: str) -> None:
        await self._conn.execute("DELETE FROM friends WHERE pubkey=?", (pubkey,))
        await self._conn.commit()

    async def topic_for_pubkey(self, pubkey: str) -> Optional[int]:
        async with self._conn.execute(
            "SELECT topic_id FROM friends WHERE pubkey=?", (pubkey,)
        ) as cur:
            row = await cur.fetchone()
        return int(row["topic_id"]) if row and row["topic_id"] is not None else None

    async def pubkey_for_topic(self, topic_id: int) -> Optional[str]:
        async with self._conn.execute(
            "SELECT pubkey FROM friends WHERE topic_id=?", (topic_id,)
        ) as cur:
            row = await cur.fetchone()
        return str(row["pubkey"]) if row else None

    async def name_for_pubkey(self, pubkey: str) -> Optional[str]:
        async with self._conn.execute(
            "SELECT name FROM friends WHERE pubkey=?", (pubkey,)
        ) as cur:
            row = await cur.fetchone()
        return str(row["name"]) if row and row["name"] is not None else None

    async def all_friends(self) -> list[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT pubkey, name, topic_id FROM friends"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # дедуп

    async def is_duplicate(self, key: str) -> bool:
        async with self._conn.execute("SELECT 1 FROM dedup WHERE key=?", (key,)) as cur:
            return await cur.fetchone() is not None

    async def mark_seen(self, key: str) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO dedup (key, created_at) VALUES (?, ?)",
            (key, self._clock()),
        )
        await self._conn.commit()

    # заявки в друзья (переживают рестарт)

    async def add_request(self, pubkey: str, message: str) -> Optional[int]:
        """Сохранить заявку. None, если такая от этого pubkey уже висит."""
        cur = await self._conn.execute(
            "INSERT OR IGNORE INTO friend_requests (pubkey, message, created_at) VALUES (?, ?, ?)",
            (pubkey, message, self._clock()),
        )
        await self._conn.commit()
        return int(cur.lastrowid) if cur.rowcount else None

    async def pop_request(self, rid: int) -> Optional[str]:
        async with self._conn.execute(
            "SELECT pubkey FROM friend_requests WHERE rid=?", (rid,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await self._conn.execute("DELETE FROM friend_requests WHERE rid=?", (rid,))
        await self._conn.commit()
        return str(row["pubkey"])

    # карта отправленных сообщений для отметок о прочтении

    async def remember_sent(
        self, pubkey: str, tox_msg_id: int, tg_msg_id: int, thread_id: int
    ) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO sent_messages
                (pubkey, tox_msg_id, tg_msg_id, thread_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pubkey, tox_msg_id, tg_msg_id, thread_id, self._clock()),
        )
        await self._conn.commit()

    async def pop_sent(self, pubkey: str, tox_msg_id: int) -> Optional[dict[str, Any]]:
        async with self._conn.execute(
            "SELECT tg_msg_id, thread_id FROM sent_messages WHERE pubkey=? AND tox_msg_id=?",
            (pubkey, tox_msg_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await self._conn.execute(
            "DELETE FROM sent_messages WHERE pubkey=? AND tox_msg_id=?", (pubkey, tox_msg_id)
        )
        await self._conn.commit()
        return dict(row)

    # служебные значения

    async def get_meta(self, key: str) -> Optional[str]:
        async with self._conn.execute("SELECT v FROM meta WHERE k=?", (key,)) as cur:
            row = await cur.fetchone()
        return str(row["v"]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        await self._conn.commit()
