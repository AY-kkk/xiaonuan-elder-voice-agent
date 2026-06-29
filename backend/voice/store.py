"""Persistent voice sample/profile records.

Raw audio is stored on disk under backend/voice_samples by default. The DB keeps
only metadata and provider identifiers so future providers can be swapped safely.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import aiosqlite

VOICE_PROFILE_STATUSES = ("none", "training", "ready", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id        TEXT NOT NULL,
    character_id    INTEGER NOT NULL,
    filename        TEXT NOT NULL DEFAULT '',
    audio_format    TEXT NOT NULL DEFAULT '',
    bytes_size      INTEGER NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL,
    storage_path    TEXT NOT NULL,
    consent         INTEGER NOT NULL DEFAULT 0,
    consent_text    TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_samples_character
ON voice_samples(elder_id, character_id, created_at DESC);

CREATE TABLE IF NOT EXISTS voice_profiles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id            TEXT NOT NULL,
    character_id        INTEGER NOT NULL,
    sample_id           INTEGER NOT NULL,
    provider            TEXT NOT NULL,
    provider_voice_id   TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'training',
    preview_text        TEXT NOT NULL DEFAULT '',
    detail              TEXT NOT NULL DEFAULT '',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    UNIQUE(elder_id, character_id, provider_voice_id)
);
CREATE INDEX IF NOT EXISTS idx_voice_profiles_character
ON voice_profiles(elder_id, character_id, updated_at DESC);
"""


class VoiceStore:
    def __init__(self, db_path: str, sample_dir: str | None = None) -> None:
        self._db_path = db_path
        db_parent = Path(db_path).resolve().parent
        self._sample_dir = Path(sample_dir).resolve() if sample_dir else db_parent / "voice_samples"

    async def ensure_schema(self) -> None:
        self._sample_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create_sample(
        self,
        *,
        elder_id: str,
        character_id: int,
        filename: str,
        audio_format: str,
        audio_bytes: bytes,
        consent: bool,
        consent_text: str,
    ) -> dict:
        now = time.time()
        digest = hashlib.sha256(audio_bytes).hexdigest()
        ext = audio_format or "bin"
        path = self._sample_dir / f"{elder_id}_{character_id}_{int(now)}_{digest[:12]}.{ext}"
        path.write_bytes(audio_bytes)
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO voice_samples"
                "(elder_id, character_id, filename, audio_format, bytes_size, sha256, storage_path, consent, consent_text, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    elder_id,
                    character_id,
                    filename,
                    audio_format,
                    len(audio_bytes),
                    digest,
                    str(path),
                    1 if consent else 0,
                    consent_text,
                    now,
                ),
            )
            await db.commit()
            sample_id = cur.lastrowid
        sample = await self.get_sample(elder_id, character_id, int(sample_id))
        if sample is None:
            raise RuntimeError("声音样本保存失败")
        return sample

    async def latest_sample(self, elder_id: str, character_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM voice_samples WHERE elder_id=? AND character_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (elder_id, character_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_sample(self, elder_id: str, character_id: int, sample_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM voice_samples WHERE elder_id=? AND character_id=? AND id=?",
                (elder_id, character_id, sample_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_profile(
        self,
        *,
        elder_id: str,
        character_id: int,
        sample_id: int,
        provider: str,
        provider_voice_id: str,
        status: str,
        detail: str = "",
        preview_text: str = "",
    ) -> dict:
        if status not in VOICE_PROFILE_STATUSES:
            raise ValueError(f"非法音色状态：{status}")
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO voice_profiles"
                "(elder_id, character_id, sample_id, provider, provider_voice_id, status, detail, preview_text, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(elder_id, character_id, provider_voice_id) DO UPDATE SET "
                "sample_id=excluded.sample_id, status=excluded.status, detail=excluded.detail, "
                "preview_text=excluded.preview_text, updated_at=excluded.updated_at",
                (
                    elder_id,
                    character_id,
                    sample_id,
                    provider,
                    provider_voice_id,
                    status,
                    detail,
                    preview_text,
                    now,
                    now,
                ),
            )
            await db.commit()
        profile = await self.latest_profile(elder_id, character_id)
        if profile is None:
            raise RuntimeError("声音档案保存失败")
        return profile

    async def latest_profile(self, elder_id: str, character_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM voice_profiles WHERE elder_id=? AND character_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (elder_id, character_id),
            )
            row = await cur.fetchone()
            return dict(row) if row else None
