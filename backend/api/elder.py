"""老人端 HTTP API：生活点滴回顾。

只读 life_memories（小陪从聊天里记住的生活记忆），给老人自己回看。
隐私边界：这是「老人看自己的数据」，life_memories 含家常细节，
**绝不进子女端路由**（子女端只看 signals 结论），二者严格分离。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..memory import MemoryStore

router = APIRouter(prefix="/api/elder", tags=["elder"])

_store: Optional[MemoryStore] = None


def bind(store: MemoryStore) -> None:
    global _store
    _store = store


@router.get("/{elder_id}/memories")
async def get_memories(elder_id: str, limit: int = 30) -> dict:
    """生活点滴回顾：返回小陪记住的生活记忆，按时间近优。"""
    items = await _store.recent_life_memories(elder_id, limit=limit)
    return {"items": items}
