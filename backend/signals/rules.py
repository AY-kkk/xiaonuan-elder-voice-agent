"""子女端信号规则库（L4）：关键词→话题分类→等级。

隐私硬边界（PRD 6.2 / 7.5）：规则只把原始对话映射为「话题标签 + 次数 + 等级」
等结论性信息，下游绝不持久化或外传任何原始句子。

规则可独立维护，与引擎解耦，便于后续由产品/运营调整阈值（Open Question #2）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# 等级
LEVEL_NORMAL = "normal"
LEVEL_ATTENTION = "attention"
LEVEL_URGENT = "urgent"

# 话题 -> (展示名, 等级, 关键词列表)
# 紧急话题优先级最高；命中即整体升级为 urgent。
_TOPIC_RULES: List[Tuple[str, str, str, List[str]]] = [
    ("emergency", "紧急情况", LEVEL_URGENT,
     ["摔倒", "跌倒", "摔了", "救命", "喘不上气", "喘不过气", "胸口疼", "胸闷", "晕倒", "出血", "急救"]),
    ("pain", "身体不适", LEVEL_ATTENTION,
     ["疼", "痛", "难受", "不舒服", "头晕", "发烧", "发热", "咳嗽", "拉肚子", "恶心", "没力气", "浑身"]),
    ("mood_low", "情绪低落", LEVEL_ATTENTION,
     ["难过", "孤独", "想哭", "没意思", "心烦", "烦躁", "害怕", "担心", "睡不着", "失眠", "没胃口"]),
    ("medicine", "用药提醒", LEVEL_ATTENTION,
     ["忘了吃药", "没吃药", "不想吃药", "忘记吃药", "药吃完了", "停药"]),
]

# 积极情绪词：用于判断当日整体心情基调
_POSITIVE_WORDS = ["开心", "高兴", "舒服", "挺好", "不错", "好多了", "精神", "热闹"]


def analyze_text(transcript: List[dict]) -> Dict:
    """对整轮对话做规则检测，仅返回结论性结构（不含任何原始句子）。

    返回：{
        "level": normal|attention|urgent,
        "mood": 积极|平稳|低落,
        "mentions": [{"topic": 展示名, "count": n}],   # 话题标签+次数
        "alerts": [展示名, ...],                        # attention/urgent 话题
    }
    """
    # 只看老人说的话，机器人回复不参与异常判定
    elder_text = "。".join(
        t.get("text", "") for t in transcript if t.get("role") == "user" and t.get("text")
    )

    mentions: List[dict] = []
    alerts: List[str] = []
    level = LEVEL_NORMAL
    for _topic, label, topic_level, words in _TOPIC_RULES:
        count = sum(elder_text.count(w) for w in words)
        if count <= 0:
            continue
        mentions.append({"topic": label, "count": count})
        if topic_level in (LEVEL_ATTENTION, LEVEL_URGENT):
            alerts.append(label)
        level = _escalate(level, topic_level)

    mood = _judge_mood(elder_text, level)
    return {"level": level, "mood": mood, "mentions": mentions, "alerts": alerts}


def build_summary(result: Dict) -> str:
    """把结论结构渲染成一句子女端可读摘要（无原始对话）。"""
    parts = [f"今日情绪{result['mood']}"]
    if result["mentions"]:
        parts.append("、".join(f"提到{m['topic']}{m['count']}次" for m in result["mentions"]))
    else:
        parts.append("未发现异常")
    if result["level"] == LEVEL_URGENT:
        parts.append("⚠️ 检测到紧急信号，建议尽快联系老人")
    return "；".join(parts)


def _escalate(current: str, candidate: str) -> str:
    order = {LEVEL_NORMAL: 0, LEVEL_ATTENTION: 1, LEVEL_URGENT: 2}
    return candidate if order[candidate] > order[current] else current


def _judge_mood(text: str, level: str) -> str:
    if level == LEVEL_URGENT:
        return "低落"
    positive = sum(text.count(w) for w in _POSITIVE_WORDS)
    negative = sum(
        text.count(w) for _t, _l, _lv, words in _TOPIC_RULES if _lv == LEVEL_ATTENTION for w in words
    )
    if negative > positive and negative > 0:
        return "低落"
    if positive > 0:
        return "积极"
    return "平稳"
