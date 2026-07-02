"""Persistent care content configured by family members."""
from __future__ import annotations

import time
from typing import Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_greetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id     TEXT NOT NULL,
    sender_name  TEXT NOT NULL DEFAULT '家人',
    text         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_greetings_elder
ON daily_greetings(elder_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS emergency_contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    relation     TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL,
    priority     INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emergency_contacts_elder
ON emergency_contacts(elder_id, status, priority ASC, updated_at DESC);

CREATE TABLE IF NOT EXISTS medication_reminders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id       TEXT NOT NULL,
    medicine_name  TEXT NOT NULL,
    dosage         TEXT NOT NULL DEFAULT '',
    schedule_text  TEXT NOT NULL,
    note           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_medication_reminders_elder
ON medication_reminders(elder_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS elder_action_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id     TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    target_type  TEXT NOT NULL DEFAULT '',
    target_id    INTEGER,
    detail       TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_elder_action_logs_elder
ON elder_action_logs(elder_id, created_at DESC);
"""


class CareStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def set_daily_greeting(self, elder_id: str, sender_name: str, text: str) -> dict:
        sender = (sender_name or "家人").strip()[:30] or "家人"
        content = (text or "").strip()
        if not content:
            raise ValueError("问候内容不能为空")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE daily_greetings SET status='archived', updated_at=? "
                "WHERE elder_id=? AND status='active'",
                (now, elder_id),
            )
            cur = await db.execute(
                "INSERT INTO daily_greetings(elder_id, sender_name, text, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (elder_id, sender, content[:160], "active", now, now),
            )
            await db.commit()
            greeting_id = int(cur.lastrowid)
        greeting = await self.active_daily_greeting(elder_id)
        if greeting is None or greeting["id"] != greeting_id:
            raise RuntimeError("每日问候保存失败")
        return greeting

    async def active_daily_greeting(self, elder_id: str) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, sender_name, text, status, created_at, updated_at "
                "FROM daily_greetings WHERE elder_id=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 1",
                (elder_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_emergency_contacts(self, elder_id: str) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, name, relation, phone, priority, status, created_at, updated_at "
                "FROM emergency_contacts WHERE elder_id=? AND status='active' "
                "ORDER BY priority ASC, updated_at DESC",
                (elder_id,),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_emergency_contact(
        self,
        elder_id: str,
        name: str,
        phone: str,
        *,
        relation: str = "",
        priority: int = 1,
    ) -> dict:
        contact_name = (name or "").strip()[:40]
        clean_phone = (phone or "").strip()[:30]
        if not contact_name:
            raise ValueError("联系人姓名不能为空")
        if not clean_phone:
            raise ValueError("联系人电话不能为空")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO emergency_contacts"
                "(elder_id, name, relation, phone, priority, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    elder_id,
                    contact_name,
                    (relation or "").strip()[:40],
                    clean_phone,
                    max(1, int(priority or 1)),
                    "active",
                    now,
                    now,
                ),
            )
            await db.commit()
            contact_id = int(cur.lastrowid)
        return await self.get_emergency_contact(elder_id, contact_id)

    async def get_emergency_contact(self, elder_id: str, contact_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, name, relation, phone, priority, status, created_at, updated_at "
                "FROM emergency_contacts WHERE elder_id=? AND id=? AND status='active'",
                (elder_id, contact_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_emergency_contact(self, elder_id: str, contact_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE emergency_contacts SET status='archived', updated_at=? WHERE elder_id=? AND id=?",
                (time.time(), elder_id, contact_id),
            )
            await db.commit()

    async def list_medication_reminders(self, elder_id: str) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, medicine_name, dosage, schedule_text, note, status, created_at, updated_at "
                "FROM medication_reminders WHERE elder_id=? AND status='active' "
                "ORDER BY updated_at DESC",
                (elder_id,),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def add_medication_reminder(
        self,
        elder_id: str,
        medicine_name: str,
        schedule_text: str,
        *,
        dosage: str = "",
        note: str = "",
    ) -> dict:
        name = (medicine_name or "").strip()[:60]
        schedule = (schedule_text or "").strip()[:80]
        if not name:
            raise ValueError("药品名称不能为空")
        if not schedule:
            raise ValueError("提醒时间不能为空")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO medication_reminders"
                "(elder_id, medicine_name, dosage, schedule_text, note, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    elder_id,
                    name,
                    (dosage or "").strip()[:60],
                    schedule,
                    (note or "").strip()[:120],
                    "active",
                    now,
                    now,
                ),
            )
            await db.commit()
            reminder_id = int(cur.lastrowid)
        return await self.get_medication_reminder(elder_id, reminder_id)

    async def get_medication_reminder(self, elder_id: str, reminder_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, medicine_name, dosage, schedule_text, note, status, created_at, updated_at "
                "FROM medication_reminders WHERE elder_id=? AND id=? AND status='active'",
                (elder_id, reminder_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_medication_reminder(self, elder_id: str, reminder_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE medication_reminders SET status='archived', updated_at=? WHERE elder_id=? AND id=?",
                (time.time(), elder_id, reminder_id),
            )
            await db.commit()

    async def log_action(
        self,
        elder_id: str,
        action_type: str,
        *,
        target_type: str = "",
        target_id: int | None = None,
        detail: str = "",
    ) -> dict:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO elder_action_logs(elder_id, action_type, target_type, target_id, detail, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    elder_id,
                    (action_type or "").strip()[:40],
                    (target_type or "").strip()[:40],
                    target_id,
                    (detail or "").strip()[:240],
                    now,
                ),
            )
            await db.commit()
            action_id = int(cur.lastrowid)
        return {
            "id": action_id,
            "elder_id": elder_id,
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "detail": detail,
            "created_at": now,
        }

    async def recent_actions(self, elder_id: str, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, elder_id, action_type, target_type, target_id, detail, created_at "
                "FROM elder_action_logs WHERE elder_id=? ORDER BY created_at DESC LIMIT ?",
                (elder_id, limit),
            )
            return [dict(row) for row in await cur.fetchall()]
