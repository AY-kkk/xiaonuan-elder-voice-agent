"""子女端信号引擎与存储（L4）。

职责：会话结束后基于规则引擎（rules.py）生成「信号」，可选用方舟 LLM 润色
一句温和摘要，落库供子女端查看。

隐私硬边界（PRD 护栏指标=0）：signals 表只存等级/心情/话题标签/次数/摘要，
绝不写入任何原始对话句子。摘要文本同样只描述结论，不复述原话。
"""
from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

import aiosqlite

from ..ark.text_client import ArkTextClient
from ..config import ArkConfig
from . import rules

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id    TEXT NOT NULL,
    level       TEXT NOT NULL,
    mood        TEXT NOT NULL,
    mood_score  INTEGER NOT NULL DEFAULT 0,   -- 情绪数值化：积极+1/平稳0/低落-1，供趋势预测
    mentions    TEXT NOT NULL DEFAULT '[]',   -- JSON: [{"topic","count"}]
    summary     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_elder_time ON signals(elder_id, created_at DESC);
"""

# 情绪 -> 数值（与 rules._judge_mood 的三档取值一一对应）。
# 用于把单次会话情绪落成时间序列，后续由情绪趋势预测模块消费。
_MOOD_SCORE = {"积极": 1, "平稳": 0, "低落": -1}

_SUMMARY_SYSTEM = (
    "你在帮远方的子女了解独居老人今天的状态。根据给定的结论性标签，写一句温暖、"
    "简短、让子女安心的中文摘要（30 字内）。只描述结论，绝对不要编造或复述任何对话原文。"
)


class SignalService:
    def __init__(self, db_path: str, ark_cfg: ArkConfig) -> None:
        self._db_path = db_path
        self._ark_cfg = ark_cfg
        self._ark = ArkTextClient(ark_cfg)

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await self._migrate_mood_score(db)
            await db.commit()

    async def _migrate_mood_score(self, db: aiosqlite.Connection) -> None:
        """老库（无 mood_score 列）平滑加列；新库由 _SCHEMA 已含，跳过。"""
        cursor = await db.execute("PRAGMA table_info(signals)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "mood_score" not in cols:
            await db.execute(
                "ALTER TABLE signals ADD COLUMN mood_score INTEGER NOT NULL DEFAULT 0"
            )

    async def generate(self, elder_id: str, transcript: List[dict]) -> Optional[dict]:
        """会话结束后生成并落库信号。失败不抛（不影响主流程）。"""
        if not transcript:
            return None
        try:
            result = rules.analyze_text(transcript)
            summary = await self._summarize(result)
            await self._persist(elder_id, result, summary)
            return {**result, "summary": summary}
        except Exception:
            logger.exception("信号生成失败（已忽略，不影响通话与蒸馏）")
            return None

    async def list_signals(self, elder_id: str, limit: int = 30) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT level, mood, mentions, summary, created_at FROM signals "
                "WHERE elder_id=? ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            rows = await cursor.fetchall()
            out = []
            for r in rows:
                item = dict(r)
                item["mentions"] = json.loads(item["mentions"] or "[]")
                out.append(item)
            return out

    async def _summarize(self, result: dict) -> str:
        rule_summary = rules.build_summary(result)
        if not self._ark_cfg.enabled:
            return rule_summary
        try:
            tags = {
                "level": result["level"],
                "mood": result["mood"],
                "mentions": result["mentions"],
            }
            text = await self._ark.chat(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user", "content": json.dumps(tags, ensure_ascii=False)},
                ],
                temperature=0.4,
                timeout=15.0,
            )
            return text or rule_summary
        except Exception:
            logger.warning("信号摘要润色失败，降级为规则摘要")
            return rule_summary

    async def _persist(self, elder_id: str, result: dict, summary: str) -> None:
        mood_score = _MOOD_SCORE.get(result["mood"], 0)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO signals(elder_id, level, mood, mood_score, mentions, summary, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    elder_id,
                    result["level"],
                    result["mood"],
                    mood_score,
                    json.dumps(result["mentions"], ensure_ascii=False),
                    summary,
                    time.time(),
                ),
            )
            await db.commit()
