"""L4 子女端信号单测：规则检测 + 隐私边界 + 落库查询。

运行：.venv/bin/python -m backend.scripts.test_signals
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..config import ArkConfig
from ..signals import rules
from ..signals.engine import SignalService

ELDER = "elder-test"


def _test_rules() -> None:
    # 正常对话
    normal = [
        {"role": "user", "text": "今天天气不错，我去公园转了转，挺开心的。"},
        {"role": "assistant", "text": "那真好，多出去走走对身体好。"},
    ]
    r = rules.analyze_text(normal)
    assert r["level"] == rules.LEVEL_NORMAL, r
    assert r["mood"] == "积极", r

    # 身体不适 -> attention
    pain = [{"role": "user", "text": "我膝盖疼，头也有点晕，浑身没力气。"}]
    r = rules.analyze_text(pain)
    assert r["level"] == rules.LEVEL_ATTENTION, r
    assert "身体不适" in r["alerts"], r

    # 紧急 -> urgent，且整体心情判低落
    urgent = [{"role": "user", "text": "我刚才在厨房摔倒了，胸口有点疼。"}]
    r = rules.analyze_text(urgent)
    assert r["level"] == rules.LEVEL_URGENT, r
    assert r["mood"] == "低落", r
    summary = rules.build_summary(r)
    assert "紧急" in summary, summary

    # 第三方/媒体语境的紧急词：不直接升级 urgent，给子女端复核提示
    third_party = [{"role": "user", "text": "刚才电视里说有个邻居摔倒了，挺吓人的。"}]
    r = rules.analyze_text(third_party)
    assert r["level"] == rules.LEVEL_ATTENTION, r
    assert r["review_required"] is True, r
    print("[PASS] 规则引擎：正常/关注/紧急 三级判定正确")


async def _test_privacy_and_persist() -> None:
    tmp = Path(tempfile.gettempdir()) / "signals_test.db"
    if tmp.exists():
        tmp.unlink()
    # 强制关闭方舟，走纯规则摘要，确保可离线断言
    ark_off = ArkConfig.__new__(ArkConfig)
    object.__setattr__(ark_off, "base_url", "x")
    object.__setattr__(ark_off, "text_model", "x")
    svc = SignalService(str(tmp), ark_off)
    await svc.ensure_schema()

    secret = "我儿子叫张伟，住在朝阳区某某小区三号楼"
    transcript = [
        {"role": "user", "text": f"我有点难过，{secret}，他好久没来看我了。"},
        {"role": "assistant", "text": "他工作忙，心里一直惦记着您呢。"},
    ]
    sig = await svc.generate(ELDER, transcript)
    assert sig is not None and sig["level"] == rules.LEVEL_ATTENTION, sig

    items = await svc.list_signals(ELDER)
    assert len(items) == 1, items
    stored = items[0]
    assert "confidence" in stored and "review_required" in stored, stored
    # 隐私硬边界：落库内容绝不含原始对话片段
    blob = str(stored)
    assert secret not in blob, "信号泄露了原始对话！"
    assert "好久没来看我" not in blob, "信号泄露了原始对话！"
    assert "情绪低落" in stored["summary"], stored
    print("[PASS] 隐私边界：信号落库不含任何原始对话")
    print(f"  落库信号：level={stored['level']} mood={stored['mood']} summary={stored['summary']}")


async def main() -> None:
    _test_rules()
    await _test_privacy_and_persist()
    print("\n所有 L4 信号自测通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
