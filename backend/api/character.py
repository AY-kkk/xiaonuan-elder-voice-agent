"""角色再生 HTTP API（声音克隆 + 人格蒸馏）。

交互范式刻意与实时语音流不同 —— 这是「表单/向导式」配置端口：
  1. POST   /api/character/{elder_id}                 创建角色（名字+关系）
  2. POST   /api/character/{elder_id}/{cid}/voice     上传音频训练音色（multipart）
  3. GET    /api/character/{elder_id}/{cid}/voice     轮询音色训练状态
  4. POST   /api/character/{elder_id}/{cid}/persona   上传语料蒸馏人格（JSON）
  5. POST   /api/character/{elder_id}/{cid}/activate  启用该角色（激活互斥）
  6. POST   /api/character/{elder_id}/deactivate      取消启用（回落默认）
  7. GET    /api/character/{elder_id}                 角色列表
  8. DELETE /api/character/{elder_id}/{cid}           删除角色

隐私：上传的音频与语料仅用于训练/蒸馏，不落库；DB 只存 speaker_id 与人格提示词。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..character import CharacterService

router = APIRouter(prefix="/api/character", tags=["character"])

_svc: Optional[CharacterService] = None

# 单文件上限 10MB（与火山声音复刻限制一致）
_MAX_AUDIO_BYTES = 10 * 1024 * 1024
_ALLOWED_FORMATS = {"wav", "mp3", "ogg", "m4a", "aac", "pcm"}


def bind(svc: CharacterService) -> None:
    global _svc
    _svc = svc


class CharacterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    relation: str = Field("", max_length=40)


class PersonaIn(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=8000, description="角色的说话片段/聊天记录")


class VoiceForm(BaseModel):
    speaker_id: str


@router.get("/{elder_id}")
async def list_characters(elder_id: str) -> dict:
    return {"items": await _svc.list(elder_id)}


@router.post("/{elder_id}")
async def create_character(elder_id: str, body: CharacterIn) -> dict:
    try:
        char = await _svc.create(elder_id, body.name, body.relation)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "character": char}


@router.post("/{elder_id}/{cid}/voice")
async def train_voice(
    elder_id: str,
    cid: int,
    speaker_id: str = Form(..., description="火山控制台购买资源包获取的音色代号 S_xxx"),
    text: str = Form("", description="可选：朗读文本，用于校验音频与文本一致性"),
    audio: UploadFile = File(..., description="音频样本（wav/mp3/m4a/aac/ogg；pcm须24k单声道）"),
) -> dict:
    audio_format = _infer_format(audio)
    if audio_format not in _ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的音频格式：{audio_format}")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="音频超过 10MB 上限")
    try:
        char = await _svc.train_voice(
            elder_id, cid, speaker_id, data, audio_format, text=text
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "character": char}


@router.get("/{elder_id}/{cid}/voice")
async def voice_status(elder_id: str, cid: int) -> dict:
    try:
        char = await _svc.refresh_voice_status(elder_id, cid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"voice_status": char["voice_status"], "speaker_id": char["speaker_id"], "character": char}


@router.post("/{elder_id}/{cid}/persona")
async def distill_persona(elder_id: str, cid: int, body: PersonaIn) -> dict:
    try:
        char = await _svc.distill_persona(elder_id, cid, body.corpus)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "character": char}


@router.post("/{elder_id}/{cid}/activate")
async def activate(elder_id: str, cid: int) -> dict:
    ok = await _svc.activate(elder_id, cid)
    if not ok:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"ok": True}


@router.post("/{elder_id}/deactivate")
async def deactivate(elder_id: str) -> dict:
    await _svc.deactivate(elder_id)
    return {"ok": True}


@router.delete("/{elder_id}/{cid}")
async def delete_character(elder_id: str, cid: int) -> dict:
    await _svc.delete(elder_id, cid)
    return {"ok": True}


def _infer_format(audio: UploadFile) -> str:
    """从文件名后缀推断音频格式（小写，去点）。"""
    name = (audio.filename or "").lower()
    if "." in name:
        return name.rsplit(".", 1)[1]
    return ""
