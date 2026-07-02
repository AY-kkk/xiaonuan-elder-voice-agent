"""Conversation session event persistence for call-chain observability."""
from __future__ import annotations

import json
import time

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id      TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    audio_frames  INTEGER NOT NULL DEFAULT 0,
    audio_bytes   INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_events_elder
ON session_events(elder_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_events_session
ON session_events(session_id, created_at ASC);
"""


class SessionEventStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def log(
        self,
        elder_id: str,
        session_id: str,
        event_type: str,
        *,
        detail: str | dict = "",
        audio_frames: int = 0,
        audio_bytes: int = 0,
    ) -> None:
        if isinstance(detail, dict):
            detail_text = json.dumps(detail, ensure_ascii=False)
        else:
            detail_text = str(detail or "")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO session_events(elder_id, session_id, event_type, detail, audio_frames, audio_bytes, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    elder_id,
                    session_id,
                    event_type,
                    detail_text[:500],
                    int(audio_frames or 0),
                    int(audio_bytes or 0),
                    time.time(),
                ),
            )
            await db.commit()

    async def recent(self, elder_id: str, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT elder_id, session_id, event_type, detail, audio_frames, audio_bytes, created_at "
                "FROM session_events WHERE elder_id=? ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            return [dict(row) for row in await cur.fetchall()]
