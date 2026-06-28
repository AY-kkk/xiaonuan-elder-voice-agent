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

CREATE TABLE IF NOT EXISTS wallet_accounts (
    elder_id       TEXT PRIMARY KEY,
    balance_cents  INTEGER NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wallet_transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id      TEXT NOT NULL,
    type          TEXT NOT NULL,          -- recharge / consume / refund / grant
    amount_cents  INTEGER NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_tx_elder_time
    ON wallet_transactions(elder_id, created_at DESC);
CREATE TABLE IF NOT EXISTS billing_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code     TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    unit          TEXT NOT NULL,
    price_cents   INTEGER NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1
);
"""


class UsageStore:
    """token 用量读写。每方法独立开连接，避免跨协程长连接竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await self._seed_billing_rules(db)
            await db.commit()

    async def _seed_billing_rules(self, db: aiosqlite.Connection) -> None:
        rules = [
            ("voice_minute", "语音陪伴", "分钟", 8),
            ("care_analysis", "关怀分析", "次", 5),
            ("voice_clone", "准备熟悉的声音", "次", 999),
        ]
        for item_code, display_name, unit, price_cents in rules:
            await db.execute(
                "INSERT OR IGNORE INTO billing_rules(item_code, display_name, unit, price_cents) "
                "VALUES(?,?,?,?)",
                (item_code, display_name, unit, price_cents),
            )

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

    async def wallet(self, elder_id: str) -> dict:
        await self._ensure_wallet(elder_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT elder_id, balance_cents, updated_at FROM wallet_accounts WHERE elder_id=?",
                (elder_id,),
            )
            row = await cur.fetchone()
            return dict(row)

    async def recharge(
        self, elder_id: str, amount_cents: int, title: str = "充值", detail: str = ""
    ) -> dict:
        amount_cents = int(amount_cents)
        if amount_cents <= 0:
            raise ValueError("充值金额必须大于 0")
        await self._ensure_wallet(elder_id)
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE wallet_accounts SET balance_cents=balance_cents+?, updated_at=? "
                "WHERE elder_id=?",
                (amount_cents, now, elder_id),
            )
            await db.execute(
                "INSERT INTO wallet_transactions(elder_id, type, amount_cents, title, detail, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (elder_id, "recharge", amount_cents, title, detail, now),
            )
            await db.commit()
        return await self.wallet(elder_id)

    async def wallet_transactions(self, elder_id: str, limit: int = 50) -> List[dict]:
        await self._ensure_wallet(elder_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT type, amount_cents, title, detail, created_at FROM wallet_transactions "
                "WHERE elder_id=? ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def billing_rules(self) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT item_code, display_name, unit, price_cents FROM billing_rules "
                "WHERE enabled=1 ORDER BY id ASC"
            )
            return [dict(r) for r in await cursor.fetchall()]

    async def _ensure_wallet(self, elder_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO wallet_accounts(elder_id, balance_cents, updated_at) "
                "VALUES(?,?,?)",
                (elder_id, 0, time.time()),
            )
            await db.commit()
