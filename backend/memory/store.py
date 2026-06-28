"""分层记忆持久化（SQLite / aiosqlite）。

对应 PRD 7.4 两层记忆：
  - 层级 A 重点事项（key_facts）：结构化，子女可预设，每次会话高优先级注入。
  - 层级 B 生活记忆（life_memories）：会话后 LLM 蒸馏产出，按时间近优注入。

仅后端持有；原始对话不落子女端（隐私硬边界，见 PRD 7.5）。
"""
from __future__ import annotations

import time
from typing import List, Optional

import aiosqlite

# 层级 A 允许的事项分类（与蒸馏/子女端配置共用同一份枚举）
KEY_FACT_CATEGORIES = ("用药", "慢病", "忌口", "重要日期", "紧急联系人", "其他")
MEMORY_STATUS_ACTIVE = "active"
MEMORY_STATUS_PENDING = "pending"
MEMORY_STATUS_ARCHIVED = "archived"
MEMORY_STATUSES = (MEMORY_STATUS_ACTIVE, MEMORY_STATUS_PENDING, MEMORY_STATUS_ARCHIVED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS key_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'dialog',   -- parent=子女预设 / dialog=对话识别
    status      TEXT NOT NULL DEFAULT 'active',   -- active=注入 / pending=待子女确认 / archived=不再注入
    confidence  REAL NOT NULL DEFAULT 1.0,
    expires_at  REAL,
    confirmed_at REAL,
    archived_at REAL,
    updated_at  REAL NOT NULL,
    UNIQUE(elder_id, category, content)
);
CREATE TABLE IF NOT EXISTS life_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'dialog',
    status      TEXT NOT NULL DEFAULT 'active',
    confidence  REAL NOT NULL DEFAULT 0.7,
    expires_at  REAL,
    archived_at REAL,
    created_at  REAL NOT NULL,
    UNIQUE(elder_id, content)
);
CREATE INDEX IF NOT EXISTS idx_life_elder_time ON life_memories(elder_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_key_fact_elder_status ON key_facts(elder_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_life_elder_status ON life_memories(elder_id, status, created_at DESC);
"""


class MemoryStore:
    """记忆读写。所有方法独立开连接，避免长连接跨协程竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await self._migrate_key_facts(db)
            await self._migrate_life_memories(db)
            await db.commit()

    async def _migrate_key_facts(self, db: aiosqlite.Connection) -> None:
        cols = await _column_names(db, "key_facts")
        additions = {
            "status": "ALTER TABLE key_facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "confidence": "ALTER TABLE key_facts ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "expires_at": "ALTER TABLE key_facts ADD COLUMN expires_at REAL",
            "confirmed_at": "ALTER TABLE key_facts ADD COLUMN confirmed_at REAL",
            "archived_at": "ALTER TABLE key_facts ADD COLUMN archived_at REAL",
        }
        for name, sql in additions.items():
            if name not in cols:
                await db.execute(sql)

    async def _migrate_life_memories(self, db: aiosqlite.Connection) -> None:
        cols = await _column_names(db, "life_memories")
        additions = {
            "source": "ALTER TABLE life_memories ADD COLUMN source TEXT NOT NULL DEFAULT 'dialog'",
            "status": "ALTER TABLE life_memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "confidence": "ALTER TABLE life_memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.7",
            "expires_at": "ALTER TABLE life_memories ADD COLUMN expires_at REAL",
            "archived_at": "ALTER TABLE life_memories ADD COLUMN archived_at REAL",
        }
        for name, sql in additions.items():
            if name not in cols:
                await db.execute(sql)

    # ---- 层级 A：重点事项 ----
    async def add_key_fact(
        self,
        elder_id: str,
        category: str,
        content: str,
        source: str = "dialog",
        confidence: float = 1.0,
        expires_days: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        if category not in KEY_FACT_CATEGORIES:
            category = "其他"
        now = time.time()
        if status not in MEMORY_STATUSES:
            status = MEMORY_STATUS_ACTIVE if source == "parent" else MEMORY_STATUS_PENDING
        confidence = _clamp_confidence(confidence)
        expires_at = now + expires_days * 86400 if expires_days else None
        confirmed_at = now if source == "parent" or status == MEMORY_STATUS_ACTIVE else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO key_facts(elder_id, category, content, source, status, confidence, expires_at, confirmed_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(elder_id, category, content) DO UPDATE SET "
                "source=excluded.source, status=excluded.status, confidence=excluded.confidence, "
                "expires_at=excluded.expires_at, confirmed_at=COALESCE(key_facts.confirmed_at, excluded.confirmed_at), "
                "archived_at=NULL, updated_at=excluded.updated_at",
                (
                    elder_id,
                    category,
                    content,
                    source,
                    status,
                    confidence,
                    expires_at,
                    confirmed_at,
                    now,
                ),
            )
            await db.commit()

    async def list_key_facts(self, elder_id: str, include_archived: bool = False) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            where = "elder_id=?"
            params: list = [elder_id]
            if not include_archived:
                where += " AND status!='archived'"
            cursor = await db.execute(
                "SELECT id, category, content, source, status, confidence, expires_at, confirmed_at, archived_at, updated_at "
                f"FROM key_facts WHERE {where} ORDER BY updated_at DESC",
                params,
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def active_key_facts(self, elder_id: str) -> List[dict]:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, category, content, source, status, confidence, expires_at, updated_at FROM key_facts "
                "WHERE elder_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?) "
                "ORDER BY updated_at DESC",
                (elder_id, now),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_key_fact_status(
        self, elder_id: str, fact_id: int, status: str, expires_days: Optional[int] = None
    ) -> None:
        if status not in MEMORY_STATUSES:
            return
        now = time.time()
        expires_at = now + expires_days * 86400 if expires_days else None
        confirmed_at = now if status == MEMORY_STATUS_ACTIVE else None
        archived_at = now if status == MEMORY_STATUS_ARCHIVED else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE key_facts SET status=?, expires_at=COALESCE(?, expires_at), "
                "confirmed_at=COALESCE(?, confirmed_at), archived_at=?, updated_at=? "
                "WHERE elder_id=? AND id=?",
                (status, expires_at, confirmed_at, archived_at, now, elder_id, fact_id),
            )
            await db.commit()

    async def delete_key_fact(self, elder_id: str, fact_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM key_facts WHERE elder_id=? AND id=?", (elder_id, fact_id)
            )
            await db.commit()

    # ---- 层级 B：生活记忆 ----
    async def add_life_memory(
        self,
        elder_id: str,
        content: str,
        source: str = "dialog",
        confidence: float = 0.7,
        expires_days: int = 90,
        status: str = MEMORY_STATUS_ACTIVE,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        if status not in MEMORY_STATUSES:
            status = MEMORY_STATUS_ACTIVE
        now = time.time()
        expires_at = now + expires_days * 86400 if expires_days else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO life_memories(elder_id, content, source, status, confidence, expires_at, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (elder_id, content, source, status, _clamp_confidence(confidence), expires_at, now),
            )
            await db.commit()

    async def recent_life_memories(self, elder_id: str, limit: int = 20) -> List[str]:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT content FROM life_memories WHERE elder_id=? AND status='active' "
                "AND (expires_at IS NULL OR expires_at>?) "
                "ORDER BY created_at DESC LIMIT ?",
                (elder_id, now, limit),
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def list_life_memories(
        self, elder_id: str, limit: int = 50, include_archived: bool = False
    ) -> List[dict]:
        where = "elder_id=?"
        params: list = [elder_id]
        if not include_archived:
            where += " AND status!='archived'"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, content, source, status, confidence, expires_at, archived_at, created_at "
                f"FROM life_memories WHERE {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_life_memory_status(self, elder_id: str, memory_id: int, status: str) -> None:
        if status not in MEMORY_STATUSES:
            return
        archived_at = time.time() if status == MEMORY_STATUS_ARCHIVED else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE life_memories SET status=?, archived_at=? WHERE elder_id=? AND id=?",
                (status, archived_at, elder_id, memory_id),
            )
            await db.commit()


async def _column_names(db: aiosqlite.Connection, table: str) -> set:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


def _clamp_confidence(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.7
