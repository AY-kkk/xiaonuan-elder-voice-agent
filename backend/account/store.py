"""Minimal family account store.

This is not a full identity provider. It gives the app a production-shaped
boundary: users receive session tokens, sessions are tied to elder_id and role,
and APIs can enforce parent/elder permissions when AUTH_REQUIRED=1.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

import aiosqlite

ROLES = ("parent", "elder")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS family_memberships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    family_id   TEXT NOT NULL,
    elder_id    TEXT NOT NULL,
    role        TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_family_memberships_elder
ON family_memberships(elder_id, role);
CREATE TABLE IF NOT EXISTS account_sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    family_id   TEXT NOT NULL,
    elder_id    TEXT NOT NULL,
    role        TEXT NOT NULL,
    expires_at  REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_sessions_user
ON account_sessions(user_id, created_at DESC);
"""


class AccountStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def login_demo(
        self,
        *,
        display_name: str,
        role: str,
        elder_id: str,
        family_id: str = "family-default",
    ) -> dict:
        if role not in ROLES:
            raise ValueError("角色必须是 parent 或 elder")
        name = (display_name or ("家人" if role == "parent" else "长辈")).strip()[:40]
        if not name:
            raise ValueError("姓名不能为空")
        now = time.time()
        token = secrets.token_urlsafe(32)
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO account_users(display_name, role, created_at) VALUES(?,?,?)",
                (name, role, now),
            )
            user_id = int(cur.lastrowid)
            await db.execute(
                "INSERT INTO family_memberships(user_id, family_id, elder_id, role, created_at) "
                "VALUES(?,?,?,?,?)",
                (user_id, family_id, elder_id, role, now),
            )
            await db.execute(
                "INSERT INTO account_sessions(token, user_id, family_id, elder_id, role, expires_at, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (token, user_id, family_id, elder_id, role, now + 30 * 86400, now),
            )
            await db.commit()
        return {
            "token": token,
            "user": {"id": user_id, "display_name": name, "role": role},
            "family_id": family_id,
            "elder_id": elder_id,
        }

    async def session(self, token: str) -> Optional[dict]:
        if not token:
            return None
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT s.token, s.user_id, s.family_id, s.elder_id, s.role, s.expires_at, "
                "u.display_name FROM account_sessions s "
                "JOIN account_users u ON u.id=s.user_id WHERE s.token=?",
                (token,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            if data["expires_at"] and data["expires_at"] < now:
                return None
            return data

    async def allowed(self, token: str, *, elder_id: str, required_role: str) -> bool:
        sess = await self.session(token)
        if not sess or sess["elder_id"] != elder_id:
            return False
        role = sess["role"]
        if required_role == "parent":
            return role == "parent"
        if required_role == "elder":
            return role in ("elder", "parent")
        return False
