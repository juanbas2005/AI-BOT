"""Persistencia en SQLite: historial de chat, preferencias y consumo de tokens."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY,
    username         TEXT,
    model            TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(user_id);
"""


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class UsageTotals:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class GlobalStats:
    users: int = 0
    messages: int = 0
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Database:
    """Capa de acceso a datos. Una única conexión compartida por el proceso."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        log.info("Base de datos lista en %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("La base de datos no está conectada (falta await connect()).")
        return self._conn

    # -------------------------------------------------------------- usuarios

    async def get_or_create_user(
        self,
        user_id: int,
        username: str | None,
        *,
        default_model: str,
        default_effort: str,
    ) -> UserSettings:
        await self._db.execute(
            """
            INSERT INTO users (user_id, username, model, reasoning_effort)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                updated_at = datetime('now')
            """,
            (user_id, username, default_model, default_effort),
        )
        await self._db.commit()
        return await self.get_user(user_id, default_model=default_model, default_effort=default_effort)

    async def get_user(
        self, user_id: int, *, default_model: str, default_effort: str
    ) -> UserSettings:
        async with self._db.execute(
            "SELECT user_id, model, reasoning_effort FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return UserSettings(user_id=user_id, model=default_model, reasoning_effort=default_effort)
        return UserSettings(
            user_id=row["user_id"],
            model=row["model"] or default_model,
            reasoning_effort=row["reasoning_effort"] or default_effort,
        )

    async def set_model(self, user_id: int, model: str) -> None:
        await self._db.execute(
            "UPDATE users SET model = ?, updated_at = datetime('now') WHERE user_id = ?",
            (model, user_id),
        )
        await self._db.commit()

    async def set_reasoning_effort(self, user_id: int, effort: str) -> None:
        await self._db.execute(
            "UPDATE users SET reasoning_effort = ?, updated_at = datetime('now') WHERE user_id = ?",
            (effort, user_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------- historial

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        await self._db.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await self._db.commit()

    async def get_history(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        """Devuelve los últimos `limit` mensajes en orden cronológico."""
        if limit <= 0:
            return []
        async with self._db.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def clear_history(self, user_id: int) -> int:
        cursor = await self._db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await self._db.commit()
        return cursor.rowcount or 0

    # --------------------------------------------------------------- consumo

    async def add_usage(
        self, user_id: int, model: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO usage (user_id, model, prompt_tokens, completion_tokens)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, model, prompt_tokens, completion_tokens),
        )
        await self._db.commit()

    async def get_user_usage(self, user_id: int) -> UsageTotals:
        async with self._db.execute(
            """
            SELECT COUNT(*)                  AS requests,
                   COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM usage WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return UsageTotals()
        return UsageTotals(
            requests=row["requests"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
        )

    async def get_global_stats(self) -> GlobalStats:
        async with self._db.execute(
            """
            SELECT (SELECT COUNT(*) FROM users)    AS users,
                   (SELECT COUNT(*) FROM messages) AS messages,
                   (SELECT COUNT(*) FROM usage)    AS requests,
                   (SELECT COALESCE(SUM(prompt_tokens), 0) FROM usage)     AS prompt_tokens,
                   (SELECT COALESCE(SUM(completion_tokens), 0) FROM usage) AS completion_tokens
            """
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return GlobalStats()
        return GlobalStats(
            users=row["users"],
            messages=row["messages"],
            requests=row["requests"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
        )
