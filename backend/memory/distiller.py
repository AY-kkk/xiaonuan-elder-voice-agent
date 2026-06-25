"""记忆蒸馏与上下文拼装（L3 核心，与语音链路解耦）。

两个对外能力，分别对接 session/manager 的两个回调：
  - build_context(elder_id) -> (system_prompt, dialog_context)
        会话开始前调用，把层级 A 重点事项（高优先级）+ 层级 B 近期生活记忆
        拼成系统人设文本注入 StartSession。
  - distill(elder_id, transcript)
        会话结束后异步调用方舟 LLM，从本轮对话提炼层级 B 生活记忆与新增
        层级 A 重点事项并落库。

降级原则（PRD 7.4 / 8.2）：方舟不可用或返回非法 JSON 时跳过本轮蒸馏，
下次仍用已有记忆，绝不阻塞或中断语音通话。
"""
from __future__ import annotations

import json
import logging
from typing import List, Tuple

from ..ark.text_client import ArkTextClient
from ..config import ArkConfig
from .store import KEY_FACT_CATEGORIES, MemoryStore

logger = logging.getLogger(__name__)

_BASE_PERSONA = (
    "你是老人的语音陪伴伙伴，名字叫小陪。说话温和、亲切、有耐心，像家里晚辈陪长辈聊天，"
    "语速放慢、用短句。多关心老人的身体、情绪和日常，不做医疗诊断，遇到健康问题温柔建议就医。"
)

_DISTILL_SYSTEM = (
    "你是记忆整理助手。请阅读一段老人与陪伴 AI 的对话，提炼值得长期记住的信息。"
    "只输出 JSON，不要解释。格式："
    '{"life_memories": ["近况/偏好/情绪/家常，每条一句话"], '
    '"key_facts": [{"category": "用药|慢病|忌口|重要日期|紧急联系人|其他", "content": "一句话"}]}。'
    "没有可提炼的就给空数组。不要编造对话里没有的信息。"
)


class MemoryService:
    def __init__(self, store: MemoryStore, ark_cfg: ArkConfig) -> None:
        self._store = store
        self._ark_cfg = ark_cfg
        self._ark = ArkTextClient(ark_cfg)

    async def build_context(self, elder_id: str) -> Tuple[str, list]:
        """组装注入 StartSession 的 system_prompt 与 dialog_context。"""
        try:
            key_facts = await self._store.list_key_facts(elder_id)
            life = await self._store.recent_life_memories(elder_id, limit=15)
        except Exception:
            logger.exception("读取记忆失败，使用空记忆（不影响通话）")
            return _BASE_PERSONA, []

        parts = [_BASE_PERSONA]
        if key_facts:
            lines = [f"- [{f['category']}] {f['content']}" for f in key_facts]
            parts.append("【需要特别记住并主动关照的重点事项】\n" + "\n".join(lines))
        if life:
            lines = [f"- {m}" for m in life]
            parts.append("【最近聊到的事，可自然提起以示在乎】\n" + "\n".join(lines))
        return "\n\n".join(parts), []

    async def distill(self, elder_id: str, transcript: List[dict]) -> None:
        """会话结束异步蒸馏。任何异常都吞掉，绝不影响下次通话。"""
        if not transcript:
            return
        if not self._ark_cfg.enabled:
            logger.info("方舟未配置，跳过蒸馏（降级为仅用已有记忆）")
            return
        try:
            dialogue = _format_dialogue(transcript)
            result = await self._ark.chat_json(
                [
                    {"role": "system", "content": _DISTILL_SYSTEM},
                    {"role": "user", "content": dialogue},
                ]
            )
            if not result:
                logger.warning("蒸馏返回空/非法 JSON，跳过本轮")
                return
            await self._persist(elder_id, result)
        except Exception:
            logger.exception("蒸馏失败（已忽略，下次仍用已有记忆）")

    async def _persist(self, elder_id: str, result: dict) -> None:
        for mem in _as_str_list(result.get("life_memories")):
            await self._store.add_life_memory(elder_id, mem)
        for fact in result.get("key_facts") or []:
            if not isinstance(fact, dict):
                continue
            category = str(fact.get("category", "其他")).strip()
            if category not in KEY_FACT_CATEGORIES:
                category = "其他"
            await self._store.add_key_fact(
                elder_id, category, str(fact.get("content", "")), source="dialog"
            )


def _format_dialogue(transcript: List[dict]) -> str:
    role_cn = {"user": "老人", "assistant": "小陪"}
    lines = [
        f"{role_cn.get(t.get('role'), t.get('role', ''))}：{t.get('text', '')}"
        for t in transcript
        if t.get("text")
    ]
    return "\n".join(lines)


def _as_str_list(value) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
