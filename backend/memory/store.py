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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS key_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'dialog',   -- parent=子女预设 / dialog=对话识别
    updated_at  REAL NOT NULL,
    UNIQUE(elder_id, category, content)
);
CREATE TABLE IF NOT EXISTS life_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(elder_id, content)
);
CREATE INDEX IF NOT EXISTS idx_life_elder_time ON life_memories(elder_id, created_at DESC);
"""


class MemoryStore:
    """记忆读写。所有方法独立开连接，避免长连接跨协程竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    # ---- 层级 A：重点事项 ----
    async def add_key_fact(
        self, elder_id: str, category: str, content: str, source: str = "dialog"
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        if category not in KEY_FACT_CATEGORIES:
            category = "其他"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO key_facts(elder_id, category, content, source, updated_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(elder_id, category, content) DO UPDATE SET "
                "source=excluded.source, updated_at=excluded.updated_at",
                (elder_id, category, content, source, time.time()),
            )
            await db.commit()

    async def list_key_facts(self, elder_id: str) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, category, content, source, updated_at FROM key_facts "
                "WHERE elder_id=? ORDER BY updated_at DESC",
                (elder_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_key_fact(self, elder_id: str, fact_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM key_facts WHERE elder_id=? AND id=?", (elder_id, fact_id)
            )
            await db.commit()

    # ---- 层级 B：生活记忆 ----
    async def add_life_memory(self, elder_id: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO life_memories(elder_id, content, created_at) "
                "VALUES(?,?,?)",
                (elder_id, content, time.time()),
            )
            await db.commit()

    async def recent_life_memories(self, elder_id: str, limit: int = 20) -> List[str]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT content FROM life_memories WHERE elder_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]
