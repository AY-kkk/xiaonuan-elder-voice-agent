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
SYNC_STATUSES = ("draft", "ready", "synced", "active", "failed")

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
    created_by      TEXT NOT NULL DEFAULT 'parent',
    sync_status     TEXT NOT NULL DEFAULT 'draft',
    synced_at       REAL,
    elder_notice_seen_at REAL,
    display_order   INTEGER NOT NULL DEFAULT 0,
    elder_alias     TEXT NOT NULL DEFAULT '',
    avoid_phrases   TEXT NOT NULL DEFAULT '[]',
    persona_revision INTEGER NOT NULL DEFAULT 0,
    persona_refined_at REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(elder_id, name)
);
CREATE INDEX IF NOT EXISTS idx_char_elder ON characters(elder_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_char_elder_sync ON characters(elder_id, sync_status, display_order);
"""

_COLUMNS = (
    "id, elder_id, name, relation, speaker_id, voice_status, "
    "persona_prompt, persona_status, is_active, created_by, sync_status, synced_at, "
    "elder_notice_seen_at, display_order, elder_alias, avoid_phrases, persona_revision, "
    "persona_refined_at, created_at, updated_at"
)


class CharacterStore:
    """角色读写。每个方法独立开连接，避免长连接跨协程竞争。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await self._migrate(db)
            await db.commit()

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        cols = await _column_names(db, "characters")
        additions = {
            "created_by": "ALTER TABLE characters ADD COLUMN created_by TEXT NOT NULL DEFAULT 'parent'",
            "sync_status": "ALTER TABLE characters ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'draft'",
            "synced_at": "ALTER TABLE characters ADD COLUMN synced_at REAL",
            "elder_notice_seen_at": "ALTER TABLE characters ADD COLUMN elder_notice_seen_at REAL",
            "display_order": "ALTER TABLE characters ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
            "elder_alias": "ALTER TABLE characters ADD COLUMN elder_alias TEXT NOT NULL DEFAULT ''",
            "avoid_phrases": "ALTER TABLE characters ADD COLUMN avoid_phrases TEXT NOT NULL DEFAULT '[]'",
            "persona_revision": "ALTER TABLE characters ADD COLUMN persona_revision INTEGER NOT NULL DEFAULT 0",
            "persona_refined_at": "ALTER TABLE characters ADD COLUMN persona_refined_at REAL",
        }
        for name, sql in additions.items():
            if name not in cols:
                await db.execute(sql)

    async def create(
        self,
        elder_id: str,
        name: str,
        relation: str = "",
        *,
        elder_alias: str = "",
        created_by: str = "parent",
    ) -> dict:
        """新建角色（声音/人格均未就绪）。重名返回已有角色（幂等友好）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("角色名不能为空")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO characters"
                "(elder_id, name, relation, elder_alias, created_by, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    elder_id,
                    name,
                    (relation or "").strip(),
                    (elder_alias or "").strip(),
                    (created_by or "parent").strip() or "parent",
                    now,
                    now,
                ),
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
                "ORDER BY is_active DESC, display_order ASC, updated_at DESC",
                (elder_id,),
            )
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def list_for_elder(self, elder_id: str) -> List[dict]:
        """老人端可见角色：只返回已同步/当前启用的角色。"""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT {_COLUMNS} FROM characters WHERE elder_id=? "
                "AND sync_status IN ('synced','active') "
                "ORDER BY is_active DESC, display_order ASC, synced_at DESC, updated_at DESC",
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
        await self._refresh_readiness(elder_id, char_id)

    async def update_persona(
        self,
        elder_id: str,
        char_id: int,
        *,
        prompt: str,
        status: str,
        refined: bool = False,
    ) -> None:
        if status not in PERSONA_STATUSES:
            raise ValueError(f"非法人格状态：{status}")
        refined_at = time.time() if refined else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET persona_prompt=?, persona_status=?, "
                "persona_revision=COALESCE(persona_revision, 0)+1, "
                "persona_refined_at=COALESCE(?, persona_refined_at), updated_at=? "
                "WHERE elder_id=? AND id=?",
                (prompt, status, refined_at, time.time(), elder_id, char_id),
            )
            await db.commit()
        await self._refresh_readiness(elder_id, char_id)

    async def _refresh_readiness(self, elder_id: str, char_id: int) -> None:
        """声音与人格都就绪时，把草稿推进到 ready；已同步/启用状态不回退。"""
        char = await self.get(elder_id, char_id)
        if not char:
            return
        if char["sync_status"] in ("synced", "active"):
            return
        target = "ready" if char["voice_status"] == "ready" and char["persona_status"] == "ready" else "draft"
        if target != char["sync_status"]:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE characters SET sync_status=?, updated_at=? WHERE elder_id=? AND id=?",
                    (target, time.time(), elder_id, char_id),
                )
                await db.commit()

    async def sync_to_elder(self, elder_id: str, char_id: int) -> bool:
        """同步给老人端。只有声音和人格都 ready 的角色可同步。"""
        char = await self.get(elder_id, char_id)
        if not char or char["voice_status"] != "ready" or char["persona_status"] != "ready":
            return False
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET sync_status='synced', synced_at=?, elder_notice_seen_at=NULL, updated_at=? "
                "WHERE elder_id=? AND id=?",
                (time.time(), time.time(), elder_id, char_id),
            )
            await db.commit()
        return True

    async def mark_notice_seen(self, elder_id: str, char_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET elder_notice_seen_at=COALESCE(elder_notice_seen_at, ?) "
                "WHERE elder_id=? AND id=?",
                (time.time(), elder_id, char_id),
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
                "UPDATE characters SET is_active=0, sync_status=CASE WHEN sync_status='active' THEN 'synced' ELSE sync_status END, updated_at=? WHERE elder_id=?",
                (time.time(), elder_id),
            )
            await db.execute(
                "UPDATE characters SET is_active=1, sync_status='active', updated_at=? WHERE elder_id=? AND id=?",
                (time.time(), elder_id, char_id),
            )
            await db.commit()
            return True

    async def deactivate_all(self, elder_id: str) -> None:
        """取消启用所有角色（回落默认音色/人设）。"""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE characters SET is_active=0, sync_status=CASE WHEN sync_status='active' THEN 'synced' ELSE sync_status END, updated_at=? WHERE elder_id=?",
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
    d["human_status"] = _human_status(d)
    return d


def _human_status(role: dict) -> str:
    if role.get("is_active"):
        return "正在作为通话对象"
    if role.get("sync_status") == "synced":
        return "已同步到老人端"
    if role.get("sync_status") == "ready":
        return "可同步到老人端"
    if role.get("voice_status") == "training":
        return "正在学习声音"
    if role.get("voice_status") == "failed" or role.get("sync_status") == "failed":
        return "需要重新处理"
    if role.get("persona_status") == "ready":
        return "还差声音"
    if role.get("voice_status") == "ready":
        return "还差说话方式"
    return "准备中"


async def _column_names(db: aiosqlite.Connection, table: str) -> set:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}
