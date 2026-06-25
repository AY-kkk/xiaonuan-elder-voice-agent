"""子女端 HTTP API（L4）：重点事项配置 + 信号摘要查看。

隐私硬边界（PRD 6.2）：本路由绝不暴露任何原始对话接口，只提供：
  - 层级 A 重点事项的增删查（子女预设）
  - 信号摘要列表（结论性信息）
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..memory import KEY_FACT_CATEGORIES, MemoryStore
from ..signals import SignalService

router = APIRouter(prefix="/api/parent", tags=["parent"])

# 由 server.py 启动时注入（避免循环依赖与重复实例化）
_store: Optional[MemoryStore] = None
_signals: Optional[SignalService] = None


def bind(store: MemoryStore, signals: SignalService) -> None:
    global _store, _signals
    _store, _signals = store, signals


class KeyFactIn(BaseModel):
    category: str = Field(..., description="用药/慢病/忌口/重要日期/紧急联系人/其他")
    content: str = Field(..., min_length=1, max_length=200)


@router.get("/categories")
async def categories() -> dict:
    return {"categories": list(KEY_FACT_CATEGORIES)}


@router.get("/{elder_id}/key_facts")
async def get_key_facts(elder_id: str) -> dict:
    return {"items": await _store.list_key_facts(elder_id)}


@router.post("/{elder_id}/key_facts")
async def add_key_fact(elder_id: str, body: KeyFactIn) -> dict:
    if body.category not in KEY_FACT_CATEGORIES:
        raise HTTPException(status_code=400, detail="非法的事项分类")
    await _store.add_key_fact(elder_id, body.category, body.content, source="parent")
    return {"ok": True, "items": await _store.list_key_facts(elder_id)}


@router.delete("/{elder_id}/key_facts/{fact_id}")
async def delete_key_fact(elder_id: str, fact_id: int) -> dict:
    await _store.delete_key_fact(elder_id, fact_id)
    return {"ok": True}


@router.get("/{elder_id}/signals")
async def get_signals(elder_id: str) -> dict:
    return {"items": await _signals.list_signals(elder_id)}
