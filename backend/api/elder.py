"""老人端 HTTP API：生活点滴回顾。

只读 life_memories（小陪从聊天里记住的生活记忆），给老人自己回看。
隐私边界：这是「老人看自己的数据」，life_memories 含家常细节，
**绝不进子女端路由**（子女端只看 signals 结论），二者严格分离。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..care import ElderCareService
from ..character import CharacterService
from ..memory import MemoryStore

router = APIRouter(prefix="/api/elder", tags=["elder"])

_store: Optional[MemoryStore] = None
_character: Optional[CharacterService] = None
_care: Optional[ElderCareService] = None


class ElderActionIn(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=40)
    target_type: str = Field("", max_length=40)
    target_id: Optional[int] = None
    detail: str = Field("", max_length=240)


def bind(
    store: MemoryStore,
    character: Optional[CharacterService] = None,
    care: Optional[ElderCareService] = None,
) -> None:
    global _store, _character, _care
    _store = store
    _character = character
    _care = care


@router.get("/{elder_id}/memories")
async def get_memories(elder_id: str, limit: int = 30) -> dict:
    """生活点滴回顾：返回小陪记住的生活记忆，按时间近优。"""
    items = await _store.recent_life_memories(elder_id, limit=limit)
    return {"items": items}


@router.get("/{elder_id}/today")
async def today_care(elder_id: str) -> dict:
    """老人端首页今日关怀。

    只返回老人能直接理解和使用的信息：一句家人问候、用药/重点提醒、
    紧急联系入口，以及低打扰陪伴文案。不暴露技术状态和原始分析过程。
    """
    if _care is None:
        raise HTTPException(status_code=404, detail="关怀服务未启用")
    return await _care.today(elder_id)


def _require_care() -> ElderCareService:
    if _care is None:
        raise HTTPException(status_code=404, detail="关怀服务未启用")
    return _care


@router.post("/{elder_id}/actions")
async def log_elder_action(elder_id: str, body: ElderActionIn) -> dict:
    action = await _require_care().log_elder_action(
        elder_id,
        body.action_type,
        target_type=body.target_type,
        target_id=body.target_id,
        detail=body.detail,
    )
    return {"ok": True, "action": action}


@router.post("/{elder_id}/medications/{medication_id}/taken")
async def elder_medication_taken(elder_id: str, medication_id: int) -> dict:
    try:
        action = await _require_care().medication_taken(elder_id, medication_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "action": action}


@router.get("/{elder_id}/companions")
async def companions(elder_id: str) -> dict:
    """老人端角色选择：只返回已由子女同步的可选陪伴对象。"""
    if _character is None:
        return {
            "active_character_id": 0,
            "items": [
                {
                    "id": 0,
                    "name": "小暖",
                    "relation": "默认通话对象",
                    "ready": True,
                    "is_active": True,
                    "elder_copy": "我一直在，想聊就点我",
                }
            ],
            "notice": None,
        }
    return await _character.companions_for_elder(elder_id)


@router.post("/{elder_id}/companions/{character_id}/activate")
async def activate_companion(elder_id: str, character_id: int) -> dict:
    """老人端选择通话对象。character_id=0 表示回到默认小暖。"""
    if _character is None:
        raise HTTPException(status_code=404, detail="角色服务未启用")
    if character_id == 0:
        await _character.deactivate(elder_id)
        return {"ok": True}
    ok = await _character.activate(elder_id, character_id)
    if not ok:
        raise HTTPException(status_code=404, detail="通话对象不存在")
    await _character.mark_elder_notice_seen(elder_id, character_id)
    return {"ok": True}


@router.post("/{elder_id}/companions/{character_id}/notice_seen")
async def mark_companion_notice_seen(elder_id: str, character_id: int) -> dict:
    if _character is None:
        raise HTTPException(status_code=404, detail="角色服务未启用")
    if character_id:
        await _character.mark_elder_notice_seen(elder_id, character_id)
    return {"ok": True}
