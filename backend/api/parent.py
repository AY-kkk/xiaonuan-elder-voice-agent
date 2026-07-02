"""子女端 HTTP API（L4）：重点事项配置 + 信号摘要查看。

隐私硬边界（PRD 6.2）：本路由绝不暴露任何原始对话接口，只提供：
  - 层级 A 重点事项的增删查（子女预设）
  - 信号摘要列表（结论性信息）
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..care import ElderCareService
from ..character import CharacterService
from ..character.chat_parser import (
    SUPPORTED_CHAT_EXTENSIONS,
    ChatParseError,
    parse_chat_record,
)
from ..memory import KEY_FACT_CATEGORIES, MemoryStore
from ..signals import SignalService
from ..usage import UsageStore
from ..voice import VoiceService

router = APIRouter(prefix="/api/parent", tags=["parent"])

# 由 server.py 启动时注入（避免循环依赖与重复实例化）
_store: Optional[MemoryStore] = None
_signals: Optional[SignalService] = None
_usage: Optional[UsageStore] = None
_character: Optional[CharacterService] = None
_voice: Optional[VoiceService] = None
_care: Optional[ElderCareService] = None
_price_per_mtoken: float = 0.0

_MAX_AUDIO_BYTES = 50 * 1024 * 1024
_MAX_CHAT_RECORD_BYTES = 20 * 1024 * 1024
_ALLOWED_FORMATS = {"wav", "mp3", "ogg", "m4a", "aac", "pcm", "webm", "flac", "mp4"}


def bind(
    store: MemoryStore,
    signals: SignalService,
    usage: UsageStore,
    price_per_mtoken: float,
    character: Optional[CharacterService] = None,
    voice: Optional[VoiceService] = None,
    care: Optional[ElderCareService] = None,
) -> None:
    global _store, _signals, _usage, _price_per_mtoken, _character, _voice, _care
    _store, _signals, _usage, _character, _voice, _care = (
        store,
        signals,
        usage,
        character,
        voice,
        care,
    )
    _price_per_mtoken = price_per_mtoken


class KeyFactIn(BaseModel):
    category: str = Field(..., description="用药/慢病/忌口/重要日期/紧急联系人/其他")
    content: str = Field(..., min_length=1, max_length=200)


class MemoryStatusIn(BaseModel):
    status: str = Field(..., description="active/pending/archived")
    expires_days: Optional[int] = Field(None, ge=1, le=3650)


class ParentCharacterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    relation: str = Field("", max_length=40)
    elder_alias: str = Field("", max_length=40)


class ParentPersonaIn(BaseModel):
    corpus: str = Field(..., min_length=1, max_length=8000)


class ParentVoiceCloneIn(BaseModel):
    sample_id: Optional[int] = Field(None, description="不传则使用最新授权录音")
    text: str = Field("", max_length=200, description="可选朗读文本")


class ParentVoicePreviewIn(BaseModel):
    text: str = Field("小暖，陪你唠唠。声音已经准备好了。", max_length=120)


class DailyGreetingIn(BaseModel):
    sender_name: str = Field("家人", min_length=1, max_length=30)
    text: str = Field(..., min_length=1, max_length=160)


class EmergencyContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    phone: str = Field(..., min_length=1, max_length=30)
    relation: str = Field("", max_length=40)
    priority: int = Field(1, ge=1, le=20)


class MedicationReminderIn(BaseModel):
    medicine_name: str = Field(..., min_length=1, max_length=60)
    schedule_text: str = Field(..., min_length=1, max_length=80)
    dosage: str = Field("", max_length=60)
    note: str = Field("", max_length=120)


class RechargeIn(BaseModel):
    amount_cents: int = Field(..., ge=100, le=1000000)
    title: str = Field("家庭陪伴充值", max_length=40)


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
    await _store.add_key_fact(
        elder_id,
        body.category,
        body.content,
        source="parent",
        confidence=1.0,
        status="active",
    )
    return {"ok": True, "items": await _store.list_key_facts(elder_id)}


@router.patch("/{elder_id}/key_facts/{fact_id}")
async def update_key_fact(elder_id: str, fact_id: int, body: MemoryStatusIn) -> dict:
    if body.status not in ("active", "pending", "archived"):
        raise HTTPException(status_code=400, detail="非法的记忆状态")
    await _store.update_key_fact_status(elder_id, fact_id, body.status, body.expires_days)
    return {"ok": True, "items": await _store.list_key_facts(elder_id)}


@router.delete("/{elder_id}/key_facts/{fact_id}")
async def delete_key_fact(elder_id: str, fact_id: int) -> dict:
    await _store.delete_key_fact(elder_id, fact_id)
    return {"ok": True}


@router.get("/{elder_id}/memories")
async def get_memories(elder_id: str) -> dict:
    """子女端记忆审核。

    隐私边界：父端只能审核可转化为照护动作的「待确认重点事项」。
    life_memories 属于老人自己的生活点滴，不从父端路由返回。
    """
    key_facts = await _store.list_key_facts(elder_id)
    return {"reviewable_key_facts": [f for f in key_facts if f.get("status") == "pending"]}


@router.patch("/{elder_id}/memories/{memory_id}")
async def update_life_memory(elder_id: str, memory_id: int, body: MemoryStatusIn) -> dict:
    raise HTTPException(status_code=410, detail="生活记忆仅老人本人可见，父端不能修改")


@router.get("/{elder_id}/signals")
async def get_signals(elder_id: str) -> dict:
    return {"items": await _signals.list_signals(elder_id)}


@router.get("/{elder_id}/usage")
async def get_usage(elder_id: str) -> dict:
    """本月成本看板：把 token 用量折算成大白话的钱与关怀次数。

    隐私：只读 usage_log（纯 token 计数），绝不涉及任何对话内容。
    """
    now = time.localtime()
    month_start = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    s = await _usage.summary(elder_id, since_ts=month_start)
    cost_yuan = round(s["total_tokens"] / 1_000_000 * _price_per_mtoken, 2)
    return {
        "month": time.strftime("%Y-%m", now),
        "calls": s["calls"],            # 本月关怀分析次数（蒸馏+信号）
        "total_tokens": s["total_tokens"],
        "cost_yuan": cost_yuan,         # 折算金额（展示用，以方舟账单为准）
        "price_per_mtoken": _price_per_mtoken,
    }


@router.get("/{elder_id}/wallet")
async def get_wallet(elder_id: str) -> dict:
    wallet = await _usage.wallet(elder_id)
    s = await _usage.summary(elder_id)
    tx = await _usage.wallet_transactions(elder_id, limit=10)
    rules = await _usage.billing_rules()
    voice_rule = next((r for r in rules if r["item_code"] == "voice_minute"), None)
    est_minutes = 0
    if voice_rule and voice_rule["price_cents"] > 0:
        est_minutes = wallet["balance_cents"] // voice_rule["price_cents"]
    return {
        "balance_cents": wallet["balance_cents"],
        "estimated_voice_minutes": est_minutes,
        "usage": {"care_analysis_calls": s["calls"], "total_tokens": s["total_tokens"]},
        "transactions": tx,
        "billing_rules": rules,
    }


@router.post("/{elder_id}/recharge")
async def recharge(elder_id: str, body: RechargeIn) -> dict:
    try:
        wallet = await _usage.recharge(
            elder_id,
            body.amount_cents,
            title=body.title,
            detail="MVP 模拟充值，真实支付接入后替换为支付回调入账",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "wallet": wallet}


def _require_character() -> CharacterService:
    if _character is None:
        raise HTTPException(status_code=404, detail="角色服务未启用")
    return _character


def _require_voice() -> VoiceService:
    if _voice is None:
        raise HTTPException(status_code=404, detail="声音服务未启用")
    return _voice


def _require_care() -> ElderCareService:
    if _care is None:
        raise HTTPException(status_code=404, detail="关怀服务未启用")
    return _care


@router.get("/{elder_id}/daily_greeting")
async def parent_daily_greeting(elder_id: str) -> dict:
    return await _require_care().parent_greeting(elder_id)


@router.post("/{elder_id}/daily_greeting")
async def parent_set_daily_greeting(elder_id: str, body: DailyGreetingIn) -> dict:
    try:
        result = await _require_care().set_parent_greeting(
            elder_id, body.sender_name, body.text
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.get("/{elder_id}/emergency_contacts")
async def parent_emergency_contacts(elder_id: str) -> dict:
    return await _require_care().parent_contacts(elder_id)


@router.post("/{elder_id}/emergency_contacts")
async def parent_add_emergency_contact(elder_id: str, body: EmergencyContactIn) -> dict:
    try:
        result = await _require_care().add_parent_contact(
            elder_id,
            body.name,
            body.phone,
            relation=body.relation,
            priority=body.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.delete("/{elder_id}/emergency_contacts/{contact_id}")
async def parent_delete_emergency_contact(elder_id: str, contact_id: int) -> dict:
    await _require_care().delete_parent_contact(elder_id, contact_id)
    return {"ok": True}


@router.get("/{elder_id}/medications")
async def parent_medications(elder_id: str) -> dict:
    return await _require_care().parent_medications(elder_id)


@router.post("/{elder_id}/medications")
async def parent_add_medication(elder_id: str, body: MedicationReminderIn) -> dict:
    try:
        result = await _require_care().add_parent_medication(
            elder_id,
            body.medicine_name,
            body.schedule_text,
            dosage=body.dosage,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.delete("/{elder_id}/medications/{medication_id}")
async def parent_delete_medication(elder_id: str, medication_id: int) -> dict:
    await _require_care().delete_parent_medication(elder_id, medication_id)
    return {"ok": True}


@router.get("/{elder_id}/care_actions")
async def parent_care_actions(elder_id: str) -> dict:
    return await _require_care().recent_elder_actions(elder_id, limit=20)


@router.get("/{elder_id}/characters")
async def parent_characters(elder_id: str) -> dict:
    return {"items": await _require_character().list(elder_id)}


@router.post("/{elder_id}/characters")
async def parent_create_character(elder_id: str, body: ParentCharacterIn) -> dict:
    try:
        char = await _require_character().create(
            elder_id, body.name, body.relation, body.elder_alias
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "character": char}


@router.post("/{elder_id}/characters/{cid}/voice/sample")
async def parent_upload_voice_sample(
    elder_id: str,
    cid: int,
    consent: bool = Form(..., description="是否已获得合法授权"),
    consent_text: str = Form("", description="授权说明"),
    audio: UploadFile = File(...),
) -> dict:
    audio_format = _infer_format(audio)
    if audio_format not in _ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的音频格式：{audio_format}")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="音频超过 50MB 上限")
    try:
        result = await _require_voice().save_sample(
            elder_id=elder_id,
            character_id=cid,
            filename=audio.filename or f"sample.{audio_format}",
            audio_format=audio_format,
            audio_bytes=data,
            consent=consent,
            consent_text=consent_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.post("/{elder_id}/characters/{cid}/voice/clone")
async def parent_clone_voice(elder_id: str, cid: int, body: ParentVoiceCloneIn) -> dict:
    try:
        result = await _require_voice().clone_latest(
            elder_id=elder_id,
            character_id=cid,
            sample_id=body.sample_id,
            text=body.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, **result}


@router.get("/{elder_id}/characters/{cid}/voice/status")
async def parent_voice_profile_status(elder_id: str, cid: int) -> dict:
    try:
        result = await _require_voice().refresh_status(elder_id, cid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, **result}


@router.post("/{elder_id}/characters/{cid}/voice/preview")
async def parent_preview_voice(elder_id: str, cid: int, body: ParentVoicePreviewIn) -> Response:
    try:
        preview = await _require_voice().preview(elder_id, cid, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=preview.audio_bytes,
        media_type=preview.content_type,
        headers={"Content-Disposition": f'inline; filename="{preview.filename}"'},
    )


@router.delete("/{elder_id}/characters/{cid}/voice_data")
async def parent_delete_voice_data(elder_id: str, cid: int) -> dict:
    try:
        result = await _require_voice().delete_voice_data(elder_id, cid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, **result}


@router.post("/{elder_id}/voice_samples/cleanup")
async def parent_cleanup_voice_samples(elder_id: str) -> dict:
    return {"ok": True, **await _require_voice().cleanup_expired_samples()}


@router.post("/{elder_id}/characters/{cid}/voice")
async def parent_train_voice(
    elder_id: str,
    cid: int,
    speaker_id: str = Form(..., description="火山音色代号 S_xxx"),
    text: str = Form("", description="可选朗读文本"),
    audio: UploadFile = File(...),
) -> dict:
    audio_format = _infer_format(audio)
    if audio_format not in _ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的音频格式：{audio_format}")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="音频超过 50MB 上限")
    try:
        char = await _require_character().train_voice(
            elder_id, cid, speaker_id, data, audio_format, text=text
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "character": char}


@router.get("/{elder_id}/characters/{cid}/voice")
async def parent_voice_status(elder_id: str, cid: int) -> dict:
    try:
        char = await _require_character().refresh_voice_status(elder_id, cid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"voice_status": char["voice_status"], "character": char}


@router.post("/{elder_id}/characters/{cid}/persona")
async def parent_distill_persona(elder_id: str, cid: int, body: ParentPersonaIn) -> dict:
    try:
        char = await _require_character().distill_persona(elder_id, cid, body.corpus)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "character": char}


@router.post("/{elder_id}/characters/{cid}/persona_upload")
async def parent_distill_persona_upload(
    elder_id: str,
    cid: int,
    elder_call_name: str = Form("", max_length=80),
    tone: str = Form("", max_length=120),
    common_words: str = Form("", max_length=4000),
    avoid_topics: str = Form("", max_length=2000),
    chat_record: Optional[UploadFile] = File(None),
) -> dict:
    parts = [
        f"TA 平时这样称呼老人：{elder_call_name.strip()}" if elder_call_name.strip() else "",
        f"TA 的语气：{tone.strip()}" if tone.strip() else "",
        f"TA 常说的话：{common_words.strip()}" if common_words.strip() else "",
        f"TA 不应该主动提起的话：{avoid_topics.strip()}" if avoid_topics.strip() else "",
    ]
    if chat_record is not None and chat_record.filename:
        data = await chat_record.read()
        if len(data) > _MAX_CHAT_RECORD_BYTES:
            raise HTTPException(status_code=400, detail="聊天记录超过 20MB 上限")
        try:
            parsed = parse_chat_record(chat_record.filename, data)
        except ChatParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        parts.append(f"聊天记录节选：\n{parsed}")
    corpus = "\n".join(p for p in parts if p).strip()
    if not corpus:
        raise HTTPException(
            status_code=400,
            detail="请填写说话方式，或上传聊天记录文件。支持："
            + "、".join(sorted(SUPPORTED_CHAT_EXTENSIONS)),
        )
    try:
        char = await _require_character().distill_persona(elder_id, cid, corpus)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "character": char}


@router.post("/{elder_id}/characters/{cid}/persona_logs")
async def parent_refine_persona_logs(
    elder_id: str,
    cid: int,
    dialogue_log: str = Form("", max_length=12000),
    chat_record: Optional[UploadFile] = File(None),
) -> dict:
    parts = [dialogue_log.strip() if dialogue_log.strip() else ""]
    if chat_record is not None and chat_record.filename:
        data = await chat_record.read()
        if len(data) > _MAX_CHAT_RECORD_BYTES:
            raise HTTPException(status_code=400, detail="对话日志超过 20MB 上限")
        try:
            parsed = parse_chat_record(chat_record.filename, data)
        except ChatParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        parts.append(parsed)
    corpus = "\n".join(p for p in parts if p).strip()
    if not corpus:
        raise HTTPException(
            status_code=400,
            detail="请粘贴角色与老人的对话日志，或上传日志文件。支持："
            + "、".join(sorted(SUPPORTED_CHAT_EXTENSIONS)),
        )
    try:
        char = await _require_character().refine_persona_from_log(elder_id, cid, corpus)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "ok": True,
        "refined": bool(char.get("persona_refine_applied")),
        "character": char,
    }


@router.post("/{elder_id}/characters/{cid}/sync")
async def parent_sync_character(elder_id: str, cid: int) -> dict:
    ok = await _require_character().sync_to_elder(elder_id, cid)
    if not ok:
        raise HTTPException(status_code=400, detail="声音和说话方式都准备好后才能同步")
    return {"ok": True, "character": await _require_character().get(elder_id, cid)}


@router.post("/{elder_id}/characters/{cid}/activate")
async def parent_activate_character(elder_id: str, cid: int) -> dict:
    ok = await _require_character().activate(elder_id, cid)
    if not ok:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"ok": True}


@router.delete("/{elder_id}/characters/{cid}")
async def parent_delete_character(elder_id: str, cid: int) -> dict:
    await _require_character().delete(elder_id, cid)
    return {"ok": True}


def _infer_format(audio: UploadFile) -> str:
    name = (audio.filename or "").lower()
    return name.rsplit(".", 1)[1] if "." in name else ""
