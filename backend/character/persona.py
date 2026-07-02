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

import logging
from typing import List, Optional

from ..ark.text_client import ArkTextClient
from ..config import ArkConfig
from .persona_skill import PERSONA_DISTILL_SKILL, ROLE_DIALOGUE_LOG_DISTILL_SKILL

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
_DIALOGUE_HEADER = "【真实互动日志蒸馏：请按老人更能接受的方式回应】"

_LOG_DISTILL_SYSTEM = (
    "你是老人语音陪伴场景的角色互动蒸馏专家。请阅读某个角色与老人的对话日志，"
    "只提炼能让下一次陪伴更自然、更像熟人的互动规律。只输出 JSON，不要解释。格式：\n"
    '{"relationship_dynamics": ["角色与老人之间稳定的互动节奏，每条一句话"], '
    '"elder_response_preferences": ["老人更容易接受的说法、节奏或话题进入方式"], '
    '"effective_moves": ["角色已经有效的安抚、转场、提醒、逗趣或收束方式"], '
    '"repair_moves": ["当老人不耐烦、沉默、误解或拒绝时应怎样修复"], '
    '"new_boundaries": ["从日志中归纳出的新边界或少碰的话题"], '
    '"phrase_patterns": ["可复用的句式模式，不能输出原文"]}\n'
    "要求：不要输出原始对话句子；不要保存具体疾病、地址、钱款、家庭矛盾等隐私事实；"
    "不要把单次情绪当成长期人格；只保留可指导角色下一轮更真实陪伴的策略。\n"
    f"{ROLE_DIALOGUE_LOG_DISTILL_SKILL}"
)


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

        safe_traits = _redact_source_echoes(traits, corpus) if traits else None
        return _render_prompt(name, relation, safe_traits) if safe_traits else _template_prompt(
            name, relation, traits=None
        )

    async def refine_from_dialogue(
        self,
        elder_id: str,
        name: str,
        relation: str,
        current_prompt: str,
        dialogue: str,
    ) -> str:
        """用角色与老人的对话日志反哺人格 prompt。

        只把日志蒸馏成互动策略，不保存原始日志；方舟不可用或结果无效时保持原 prompt。
        """
        dialogue = (dialogue or "").strip()
        base_prompt = (current_prompt or "").strip() or _template_prompt(
            name, relation, traits=None
        )
        if not dialogue:
            return base_prompt
        if not self._ark_cfg.enabled:
            logger.info("方舟未配置，跳过角色真实互动日志蒸馏")
            return base_prompt

        try:
            user_content = (
                "现有角色画像（用于合并更新，不要复述）：\n"
                f"{_truncate(base_prompt, limit=4000)}\n\n"
                "新对话日志（只抽象互动策略，不保存原文）：\n"
                f"{_truncate(dialogue, limit=6000)}"
            )
            traits = await self._client_for(elder_id).chat_json(
                [
                    {"role": "system", "content": _LOG_DISTILL_SYSTEM},
                    {"role": "user", "content": user_content},
                ]
            )
        except Exception:
            logger.exception("角色真实互动日志蒸馏失败，保持现有人格")
            return base_prompt

        safe_traits = _redact_source_echoes(traits, dialogue) if traits else None
        if not _has_dialogue_traits(safe_traits):
            return base_prompt
        return _render_dialogue_refinement(base_prompt, name, relation, safe_traits or {})


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


def _render_dialogue_refinement(
    current_prompt: str, name: str, relation: str, traits: dict
) -> str:
    """把真实互动日志蒸馏结果追加到人格提示词末尾；重复蒸馏时替换旧日志块。"""
    base = _strip_dialogue_refinement(current_prompt)
    who = f"{name}" + (f"（{relation}）" if relation else "")
    lines = [_DIALOGUE_HEADER, f"- 角色：{who}"]
    list_fields = [
        ("relationship_dynamics", "关系节奏"),
        ("elder_response_preferences", "老人更接受"),
        ("effective_moves", "有效陪伴动作"),
        ("repair_moves", "卡住时修复"),
        ("new_boundaries", "新增边界"),
        ("phrase_patterns", "真实句式模式"),
    ]
    for key, label in list_fields:
        values = _as_str_list(traits.get(key))
        if values:
            lines.append(f"- {label}：" + "；".join(values[:5]))
    lines.append(
        "使用规则：这些是互动策略，不是事实记忆；只在自然相关时使用，"
        "不要让老人感觉被记录或被分析。"
    )
    return f"{base}\n\n" + "\n".join(lines)


def _strip_dialogue_refinement(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if _DIALOGUE_HEADER not in prompt:
        return prompt
    return prompt.split(_DIALOGUE_HEADER, 1)[0].rstrip()


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


def format_dialogue_log(transcript: List[dict], *, assistant_name: str = "角色") -> str:
    role_cn = {"user": "老人", "assistant": assistant_name}
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


def _has_dialogue_traits(value) -> bool:
    if not isinstance(value, dict):
        return False
    return any(
        _as_str_list(value.get(key))
        for key in (
            "relationship_dynamics",
            "elder_response_preferences",
            "effective_moves",
            "repair_moves",
            "new_boundaries",
            "phrase_patterns",
        )
    )


def _redact_source_echoes(traits: dict, source: str) -> dict:
    """删除疑似复述上传原文的字段，给 prompt 约束之外再加一道确定性护栏。"""
    if not isinstance(traits, dict):
        return {}
    source_text = (source or "").strip()
    source_lines = [
        line.strip()
        for line in source_text.splitlines()
        if len(line.strip()) >= 8
    ]
    cleaned: dict = {}
    for key, value in traits.items():
        if isinstance(value, list):
            items = [
                str(item).strip()
                for item in value
                if str(item).strip() and not _looks_like_source_echo(str(item), source_text, source_lines)
            ]
            if items:
                cleaned[key] = items
        else:
            text = str(value).strip()
            if text and not _looks_like_source_echo(text, source_text, source_lines):
                cleaned[key] = text
    return cleaned


def _looks_like_source_echo(value: str, source_text: str, source_lines: List[str]) -> bool:
    text = " ".join((value or "").split())
    if len(text) < 8:
        return False
    compact_source = " ".join(source_text.split())
    if text in compact_source:
        return True
    return any(line in text or text in line for line in source_lines)
