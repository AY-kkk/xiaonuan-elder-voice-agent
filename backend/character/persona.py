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

logger = logging.getLogger(__name__)

_DISTILL_SYSTEM = (
    "你是“人格画像”分析师。请阅读一段某个人物的真实说话片段/聊天记录，"
    "提炼出能让 AI 逼真扮演此人的特征。只输出 JSON，不要解释。格式：\n"
    '{"identity": "身份与角色一句话", '
    '"tone": "说话语气与情绪基调", '
    '"speech_style": "用词/句式/标点/口头禅等表达习惯", '
    '"knowledge": "知识背景与擅长话题", '
    '"values": "在意的事与价值取向", '
    '"taboos": ["绝不会说或做的事，可空"]}\n'
    "要求：基于语料真实归纳，不要编造语料里没有的信息；每项简短精炼。"
)

# 注入发声链路的人格段落框架（拼在基础陪伴人设之后）。
_PERSONA_HEADER = "【你要扮演的角色设定，请始终用 TA 的口吻、性格与说话习惯回应】"


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
    """把五层人格 JSON 渲染成可注入的提示词片段。"""
    lines = [_PERSONA_HEADER, f"- 角色：{name}" + (f"（{relation}）" if relation else "")]
    field_label = [
        ("identity", "身份"),
        ("tone", "语气"),
        ("speech_style", "说话习惯"),
        ("knowledge", "知识背景"),
        ("values", "在意的事"),
    ]
    for key, label in field_label:
        val = str(traits.get(key, "")).strip()
        if val:
            lines.append(f"- {label}：{val}")
    taboos = traits.get("taboos")
    if isinstance(taboos, list):
        items = [str(t).strip() for t in taboos if str(t).strip()]
        if items:
            lines.append("- 绝不会：" + "；".join(items))
    lines.append("注意：保持温和耐心，适配老年人陪伴场景；不做医疗诊断，遇健康问题温柔建议就医。")
    return "\n".join(lines)


def _template_prompt(name: str, relation: str, traits: Optional[dict]) -> str:
    """无语料/无方舟时的兜底：仅凭名字与关系给出基础角色设定。"""
    who = f"{name}" + (f"（{relation}）" if relation else "")
    return (
        f"{_PERSONA_HEADER}\n"
        f"- 角色：{who}\n"
        f"- 请用亲切自然、贴近日常的口吻，像 {who} 那样陪老人聊天。\n"
        "注意：保持温和耐心，适配老年人陪伴场景；不做医疗诊断，遇健康问题温柔建议就医。"
    )


def _truncate(text: str, limit: int = 4000) -> str:
    """语料过长时截断，控制单次蒸馏成本（人格特征不需要全量语料）。"""
    return text if len(text) <= limit else text[:limit]
