"""人格蒸馏（第二蒸馏链路）：把一段角色语料提炼成可注入的 system prompt 片段。

与 L3 记忆蒸馏（memory/distiller.py）的区别 —— 两者维度正交、互不干扰：
  - 记忆蒸馏：从「老人说了什么事」提炼事实记忆（用药/慢病/近况），是“记住内容”。
  - 人格蒸馏：从「角色怎么说话」提炼说话风格/性格/口头禅/知识背景，是“再生角色”。

业界共识（big-AGI Persona / colleague.skill 五层人格模型 / 火山角色扮演 SP 指南）：
  纯 prompt 注入即可让角色再生，无需任何重训 —— 这正是本功能的技术路线。

落地形态：用户（子女）一次性粘贴该角色的说话片段/聊天记录 → 方舟 LLM 蒸馏成
五层人格 JSON → 渲染成 system prompt 片段落库 → 通话时拼进 system_role 注入。

隐私：只落「蒸馏后的人格特征/提示词」，绝不存用户上传的原始语料（用完即弃）。
降级：未配置方舟（ARK_API_KEY）时用模板兜底，不阻塞端口可用性。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..ark.text_client import ArkTextClient
from ..config import ArkConfig
from .persona_skill import PERSONA_DISTILL_SKILL

logger = logging.getLogger(__name__)

_DISTILL_SYSTEM = (
    "你是家庭照护场景的人物关系画像分析师。请阅读一段某个人物的说话片段，"
    "提炼出适合老人语音陪伴的角色设定。只输出 JSON，不要解释。格式：\n"
    '{"identity": "这个人是谁，一句话", '
    '"address_style": "TA 通常如何称呼老人、如何自称", '
    '"emotional_style": "情绪基调，如温柔/爽朗/慢条斯理/爱开玩笑", '
    '"speech_style": "用词、句式、口头禅、停顿特点", '
    '"caring_behaviors": ["TA 会怎样关心老人"], '
    '"boundaries": ["必须遵守的照护边界"], '
    '"avoid_topics": ["不应主动提起的话题，可空"], '
    '"phrase_patterns": ["可模仿的句式模式或短句类型，不能输出原文"]}\n'
    "要求：只基于语料归纳，不编造具体事实；不要输出原始聊天句子；"
    "重点服务老人陪伴，温和、克制、不制造监控感；涉及医疗只建议联系医生或家人。\n"
    f"{PERSONA_DISTILL_SKILL}"
)

# 注入发声链路的人格段落框架（拼在基础陪伴人设之后）。
_PERSONA_HEADER = "【陪伴角色画像：请用这个人的说话方式陪老人聊天】"


class PersonaService:
    """人格蒸馏。任何失败都收敛为模板降级，绝不抛断上层端口。"""

    def __init__(self, ark_cfg: ArkConfig, usage_store=None) -> None:
        self._ark_cfg = ark_cfg
        self._usage_store = usage_store

    def _client_for(self, elder_id: str) -> ArkTextClient:
        """带用量上报的客户端（sink 闭包捕获 elder_id，避免并发串号；scene=persona）。"""
        if not self._usage_store:
            return ArkTextClient(self._ark_cfg)

        async def _sink(usage: dict) -> None:
            await self._usage_store.record(
                elder_id, "persona", self._ark_cfg.text_model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
            )

        return ArkTextClient(self._ark_cfg, usage_sink=_sink)

    async def distill(
        self, elder_id: str, name: str, relation: str, corpus: str
    ) -> str:
        """把角色语料蒸馏成 system prompt 片段。失败回落模板（仍可用）。

        返回值即可直接注入的人格提示词片段（已含框架头）。
        """
        corpus = (corpus or "").strip()
        if not corpus:
            return _template_prompt(name, relation, traits=None)

        if not self._ark_cfg.enabled:
            logger.info("方舟未配置，人格蒸馏降级为模板（仍可注入基础角色设定）")
            return _template_prompt(name, relation, traits=None)

        try:
            traits = await self._client_for(elder_id).chat_json(
                [
                    {"role": "system", "content": _DISTILL_SYSTEM},
                    {"role": "user", "content": _truncate(corpus)},
                ]
            )
        except Exception:
            logger.exception("人格蒸馏调用失败，降级为模板")
            traits = None

        return _render_prompt(name, relation, traits) if traits else _template_prompt(
            name, relation, traits=None
        )


def _render_prompt(name: str, relation: str, traits: dict) -> str:
    """把家庭陪伴画像 JSON 渲染成可注入的提示词片段。"""
    lines = [_PERSONA_HEADER, f"- 角色：{name}" + (f"（{relation}）" if relation else "")]
    field_label = [
        ("identity", "身份"),
        ("address_style", "称呼方式"),
        ("emotional_style", "情绪基调"),
        ("speech_style", "说话习惯"),
    ]
    for key, label in field_label:
        val = str(traits.get(key, "")).strip()
        if val:
            lines.append(f"- {label}：{val}")
    list_fields = [
        ("caring_behaviors", "关心方式"),
        ("boundaries", "边界"),
        ("avoid_topics", "不要主动提"),
        ("phrase_patterns", "句式模式"),
    ]
    for key, label in list_fields:
        items = traits.get(key)
        if isinstance(items, list):
            values = [str(t).strip() for t in items if str(t).strip()]
            if values:
                lines.append(f"- {label}：" + "；".join(values))
    lines.append(
        "使用规则：像这个人一样自然说话，但不要声称自己真的就是本人；不要复述上传语料；"
        "不做医疗诊断，遇健康问题温柔建议联系医生或家人。"
    )
    return "\n".join(lines)


def _template_prompt(name: str, relation: str, traits: Optional[dict]) -> str:
    """无语料/无方舟时的兜底：仅凭名字与关系给出基础角色设定。"""
    who = f"{name}" + (f"（{relation}）" if relation else "")
    return (
        f"{_PERSONA_HEADER}\n"
        f"- 角色：{who}\n"
        f"- 称呼方式：使用家人之间自然、亲近但不过分夸张的称呼。\n"
        f"- 情绪基调：亲切、耐心、克制，像 {who} 在身边慢慢陪老人说话。\n"
        "- 关心方式：多问候身体、吃饭、睡眠和心情；提醒时用商量语气，不命令。\n"
        "- 边界：不做医疗诊断，不制造监控感，不反复追问老人不想说的事。\n"
        "使用规则：像这个人一样自然说话，但不要声称自己真的就是本人；遇健康问题温柔建议联系医生或家人。"
    )


def _truncate(text: str, limit: int = 4000) -> str:
    """语料过长时截断，控制单次蒸馏成本（人格特征不需要全量语料）。"""
    return text if len(text) <= limit else text[:limit]
