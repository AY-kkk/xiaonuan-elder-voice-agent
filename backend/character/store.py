"""角色（声音 + 人格）持久化（SQLite / aiosqlite）。

一个角色 = 老人喜爱对象的「声音」+「灵魂」的可复用绑定：
  - speaker_id     火山声音复刻音色代号（S_xxx），决定「用谁的嗓子说话」
  - voice_status   音色训练状态：none/training/ready/failed
  - persona_prompt 人格蒸馏出的 system prompt 片段，决定「用谁的口吻/性格说话」
  - persona_status 人格状态：none/ready
  - is_active      老人当前启用的角色（每个 elder_id 至多一个，激活互斥）

多角色按 elder_id 物理隔离（所有读写 WHERE elder_id=?），符合会话隔离约束。
隐私：只存蒸馏后的人格特征与音色代号，绝不存用户上传的原始语料/音频。
"""
from __future__ import annotations

import time
from typing import List, Optional

import aiosqlite

VOICE_STATUSES = ("none", "training", "ready", "failed")
PERSONA_STATUSES = ("none", "ready")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    relation        TEXT NOT NULL DEFAULT '',
    speaker_id      TEXT NOT NULL DEFAULT '',
    voice_status    TEXT NOT NULL DEFAULT 'none',
    persona_prompt  TEXT NOT NULL DEFAULT '',
    persona_status  TEXT NOT NULL DEFAULT 'none',
    is_active       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(elder_id, name)
);
CREATE INDEX IF NOT EXISTS idx_char_elder ON characters(elder_id, updated_at DESC);
"""

_COLUMNS = (
    "id, elder_id, name, relation, speaker_id, voice_status, "
    "persona_prompt, persona_status, is_active, created_at, updated_at"
)


class CharacterStore:
    """角色读写。每个方法独立开连接，避免长连接跨协程竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create(self, elder_id: str, name: str, relation: str = "") -> dict:
        """新建角色（声音/人格均未就绪）。重名返回已有角色（幂等友好）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("角色名不能为空")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO characters"
                "(elder_id, name, relation, created_at, updated_at) VALUES(?,?,?,?,?)",
                (elder_id, name, (relation or "").strip(), now, now),
            )
            await db.commit()
        existing = await self.get_by_name(elder_id, name)
        if existing is None:  # 理论不可达，防御
            raise RuntimeError("角色创建失败")
        return existing

    async def list(self, elder_id: str) -> List[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT {_COLUMNS} FROM characters WHERE elder_id=? "
                "ORDER BY is_active DESC, updated_at DESC",
                (elder_id,),
            )
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def get(self, elder_id: str, char_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT {_COLUMNS} FROM characters WHERE elder_id=? AND id=?",
                (elder_id, char_id),
            )
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None

    async def get_by_name(self, elder_id: str, name: str) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT {_COLUMNS} FROM characters WHERE elder_id=? AND name=?",
                (elder_id, name),
            )
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None

    async def get_active(self, elder_id: str) -> Optional[dict]:
        """老人当前启用的角色；未启用任何角色返回 None（回落默认音色/人设）。"""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT {_COLUMNS} FROM characters WHERE elder_id=? AND is_active=1 LIMIT 1",
                (elder_id,),
            )
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None

    async def update_voice(self, elder_id: str, char_id: int, *, speaker_id: str, status: str) -> None:
        if status not in VOICE_STATUSES:
            raise ValueError(f"非法音色状态：{status}")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET speaker_id=?, voice_status=?, updated_at=? "
                "WHERE elder_id=? AND id=?",
                (speaker_id, status, time.time(), elder_id, char_id),
            )
            await db.commit()

    async def update_persona(self, elder_id: str, char_id: int, *, prompt: str, status: str) -> None:
        if status not in PERSONA_STATUSES:
            raise ValueError(f"非法人格状态：{status}")
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET persona_prompt=?, persona_status=?, updated_at=? "
                "WHERE elder_id=? AND id=?",
                (prompt, status, time.time(), elder_id, char_id),
            )
            await db.commit()

    async def set_active(self, elder_id: str, char_id: int) -> bool:
        """激活某角色（同 elder 下互斥：先全部置 0 再置目标为 1）。

        返回是否成功（目标角色不存在则 False）。在单事务内完成，避免出现
        「多个激活」或「无激活」的中间态。
        """
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM characters WHERE elder_id=? AND id=?", (elder_id, char_id)
            )
            if await cur.fetchone() is None:
                return False
            await db.execute(
                "UPDATE characters SET is_active=0, updated_at=? WHERE elder_id=?",
                (time.time(), elder_id),
            )
            await db.execute(
                "UPDATE characters SET is_active=1, updated_at=? WHERE elder_id=? AND id=?",
                (time.time(), elder_id, char_id),
            )
            await db.commit()
            return True

    async def deactivate_all(self, elder_id: str) -> None:
        """取消启用所有角色（回落默认音色/人设）。"""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET is_active=0, updated_at=? WHERE elder_id=?",
                (time.time(), elder_id),
            )
            await db.commit()

    async def delete(self, elder_id: str, char_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM characters WHERE elder_id=? AND id=?", (elder_id, char_id)
            )
            await db.commit()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["is_active"] = bool(d.get("is_active"))
    return d
