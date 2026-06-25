"""隐私硬边界统一断言工具。

核心约束（PRD 护栏指标 = 0）：任何落库或推送给子女端的产物，绝不能包含
老人对话的原始片段。本工具被各隐私回归测试复用，避免断言逻辑散落、口径不一。

判定方法：滑动窗口子串匹配。原始敏感句中任意连续 window 字（默认 6）出现在
对外产物字符串中，即判为泄漏——窗口取 6 是经验值：太短易误报（如常见双字词），
太长会漏掉短敏感片段，可按实际语料调整。
"""
from __future__ import annotations


def find_raw_leak(
    raw_sentences: list[str],
    outward_blob: str,
    window: int = 6,
) -> str | None:
    """返回第一个泄漏的原文片段；无泄漏返回 None。

    :param raw_sentences: 老人说过的原始敏感句子列表。
    :param outward_blob: 任何对外产物（落库记录/推送文本）的字符串形态。
    :param window: 连续多少字算泄漏，默认 6。
    """
    if not outward_blob:
        return None
    for sentence in raw_sentences:
        s = (sentence or "").strip()
        if not s:
            continue
        # 句子本身比窗口短：整句作为一个片段比对，避免漏检短敏感词。
        if len(s) <= window:
            if s in outward_blob:
                return s
            continue
        for i in range(len(s) - window + 1):
            frag = s[i : i + window]
            if frag in outward_blob:
                return frag
    return None


def assert_no_raw_leak(
    raw_sentences: list[str],
    outward_blob: str,
    window: int = 6,
) -> None:
    """断言对外产物不含任何原文片段，泄漏则抛 AssertionError。"""
    leak = find_raw_leak(raw_sentences, outward_blob, window)
    if leak is not None:
        raise AssertionError(f"隐私泄漏：原文片段「{leak}」出现在对外产物中")
