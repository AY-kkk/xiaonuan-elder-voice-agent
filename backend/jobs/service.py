"""轻量后台任务服务。

用于会话结束后的记忆蒸馏、风险信号生成等 fire-and-forget 工作：
  - 任务状态落 SQLite，健康检查可看到最近失败；
  - 失败可按固定次数重试；
  - 不持久化对话内容，只记录任务类型、老人 ID、错误摘要。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS background_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    started_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_background_jobs_status
ON background_jobs(status, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_background_jobs_elder
ON background_jobs(elder_id, started_at DESC);
"""


class BackgroundJobService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    def submit(
        self,
        elder_id: str,
        job_type: str,
        factory: Callable[[], Awaitable[object]],
        *,
        max_attempts: int = 2,
    ) -> asyncio.Task:
        return asyncio.create_task(
            self.run(elder_id, job_type, factory, max_attempts=max_attempts)
        )

    async def run(
        self,
        elder_id: str,
        job_type: str,
        factory: Callable[[], Awaitable[object]],
        *,
        max_attempts: int = 2,
    ) -> None:
        job_id = await self._create_job(elder_id, job_type)
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            await self._mark_attempt(job_id, attempt)
            try:
                await factory()
                await self._finish(job_id, "succeeded")
                return
            except Exception as exc:  # 后台任务不能冲垮主会话
                last_error = str(exc)[:500]
                logger.exception(
                    "后台任务失败 job_id=%s elder_id=%s job_type=%s attempt=%s/%s",
                    job_id,
                    elder_id,
                    job_type,
                    attempt,
                    max_attempts,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(min(2**attempt, 8))
        await self._finish(job_id, "failed", error=last_error)

    async def recent_failures(self, *, since_seconds: int = 86400) -> int:
        cutoff = time.time() - since_seconds
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM background_jobs "
                "WHERE status='failed' AND COALESCE(finished_at, started_at)>=?",
                (cutoff,),
            )
            row = await cur.fetchone()
            return int(row[0] if row else 0)

    async def last_failure(self) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT elder_id, job_type, attempts, error, finished_at "
                "FROM background_jobs WHERE status='failed' "
                "ORDER BY finished_at DESC LIMIT 1"
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def _create_job(self, elder_id: str, job_type: str) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO background_jobs(elder_id, job_type, status, started_at) "
                "VALUES(?,?,?,?)",
                (elder_id, job_type, "running", time.time()),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def _mark_attempt(self, job_id: int, attempt: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE background_jobs SET attempts=? WHERE id=?",
                (attempt, job_id),
            )
            await db.commit()

    async def _finish(self, job_id: int, status: str, *, error: str = "") -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE background_jobs SET status=?, error=?, finished_at=? WHERE id=?",
                (status, error, time.time(), job_id),
            )
            await db.commit()
