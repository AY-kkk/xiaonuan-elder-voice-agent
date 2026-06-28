"""Deprecated 角色 API。

角色生成的唯一写入口已经迁移到 /api/parent/{elder_id}/characters...。
本模块只保留只读列表用于短期兼容，所有写操作返回 410，避免绕过父端授权确认。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..character import CharacterService

router = APIRouter(prefix="/api/character", tags=["character"])

_svc: Optional[CharacterService] = None

def bind(svc: CharacterService) -> None:
    global _svc
    _svc = svc


class CharacterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    relation: str = Field("", max_length=40)
    elder_alias: str = Field("", max_length=40)


class PersonaIn(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=8000, description="角色的说话片段/聊天记录")


@router.get("/{elder_id}")
async def list_characters(elder_id: str) -> dict:
    return {"items": await _svc.list(elder_id)}


def _deprecated_write() -> None:
    raise HTTPException(
        status_code=410,
        detail="角色生成已迁移到 /api/parent/{elder_id}/characters",
    )


@router.post("/{elder_id}")
async def create_character(elder_id: str, body: CharacterIn) -> dict:
    _deprecated_write()


@router.post("/{elder_id}/{cid}/voice")
async def train_voice(elder_id: str, cid: int) -> dict:
    _deprecated_write()


@router.get("/{elder_id}/{cid}/voice")
async def voice_status(elder_id: str, cid: int) -> dict:
    _deprecated_write()


@router.post("/{elder_id}/{cid}/persona")
async def distill_persona(elder_id: str, cid: int, body: PersonaIn) -> dict:
    _deprecated_write()


@router.post("/{elder_id}/{cid}/activate")
async def activate(elder_id: str, cid: int) -> dict:
    _deprecated_write()


@router.post("/{elder_id}/{cid}/sync")
async def sync_to_elder(elder_id: str, cid: int) -> dict:
    _deprecated_write()


@router.post("/{elder_id}/deactivate")
async def deactivate(elder_id: str) -> dict:
    _deprecated_write()


@router.delete("/{elder_id}/{cid}")
async def delete_character(elder_id: str, cid: int) -> dict:
    _deprecated_write()
