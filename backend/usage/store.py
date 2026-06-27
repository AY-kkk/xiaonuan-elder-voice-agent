"""方舟 LLM 用量记账（成本可观测）。

只记 token 数与场景，不记任何对话内容——与隐私边界一致。
单价折算在接口层做（单价可配置），本层只存原始 token 事实。

scene 取值：
  - distill：L3 记忆蒸馏
  - signal：L4 信号摘要润色
"""
from __future__ import annotations

import time
from typing import List

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id          TEXT NOT NULL,
    scene             TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_elder_time ON usage_log(elder_id, created_at DESC);
"""


class UsageStore:
    """token 用量读写。每方法独立开连接，避免跨协程长连接竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def record(
        self,
        elder_id: str,
        scene: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO usage_log(elder_id, scene, model, prompt_tokens, "
                "completion_tokens, total_tokens, created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    elder_id,
                    scene,
                    model,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    int(total_tokens or 0),
                    time.time(),
                ),
            )
            await db.commit()

    async def summary(self, elder_id: str, since_ts: float = 0.0) -> dict:
        """汇总某 elder 自 since_ts 起的总 token 与调用次数。"""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(total_tokens),0), COUNT(*) FROM usage_log "
                "WHERE elder_id=? AND created_at>=?",
                (elder_id, since_ts),
            )
            total_tokens, calls = await cursor.fetchone()
            return {"total_tokens": int(total_tokens), "calls": int(calls)}

    async def recent(self, elder_id: str, limit: int = 50) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT scene, model, total_tokens, created_at FROM usage_log "
                "WHERE elder_id=? ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            return [dict(r) for r in await cursor.fetchall()]
