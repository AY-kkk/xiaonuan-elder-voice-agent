"""隐私回归测试：对外产物绝不含原始对话片段（PRD 护栏指标=0）。

覆盖路径：
  1. 信号落库（signals 表）—— 纯规则可离线断言。
  2. 子女端信号列表查询（list_signals 返回给前端的结构）。

设计要点：
  - 强制关闭方舟（清空 ARK_API_KEY 环境变量），走纯规则摘要，使断言离线确定，
    不依赖外部 LLM，可在 CI 稳定复现。
  - 方舟「开启」时摘要由 LLM 生成，prompt 改动可能复述原话——那条路径需另建
    采样测试（见 交付风险解决方案 R3 步骤2 注2），不在本离线回归内。

运行：.venv/bin/python -m backend.scripts.test_privacy
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from .privacy_assert import assert_no_raw_leak

ELDER = "elder-privacy-test"

# 绝不允许出现在任何对外产物里的原始敏感片段（人名/住址/具体事件）。
SECRETS = [
    "我儿子叫张伟住朝阳区某某小区三号楼",
    "上周三在厨房摔倒了胸口疼",
]
RAW = [
    {"role": "user", "text": f"我有点难过，{SECRETS[0]}，他好久没来看我了。"},
    {"role": "assistant", "text": "他工作忙，心里一直惦记着您呢。"},
    {"role": "user", "text": f"{SECRETS[1]}，到现在还不舒服。"},
]


async def _run() -> None:
    # 关键：确保方舟关闭，摘要走纯规则，断言离线确定。
    os.environ.pop("ARK_API_KEY", None)

    # 延迟导入，确保上面的环境变量已生效（ArkConfig.enabled 读 env）。
    from ..config import ArkConfig
    from ..signals.engine import SignalService

    tmp = Path(tempfile.gettempdir()) / "privacy_test.db"
    if tmp.exists():
        tmp.unlink()

    svc = SignalService(str(tmp), ArkConfig())
    await svc.ensure_schema()

    sig = await svc.generate(ELDER, RAW)
    assert sig is not None, "信号未生成"

    items = await svc.list_signals(ELDER)
    assert len(items) == 1, f"期望 1 条信号，实际 {len(items)}"

    # 对外产物 = 生成返回值 + 落库后查询结果，两者都不得含原文。
    assert_no_raw_leak(SECRETS, str(sig))
    assert_no_raw_leak(SECRETS, str(items))

    tmp.unlink(missing_ok=True)
    print("[PASS] 信号落库 + 列表查询：无任何原始对话泄漏")
    print(f"  落库摘要：{items[0]['summary']}")


def main() -> None:
    asyncio.run(_run())
    print("\n隐私回归测试通过 ✅")


if __name__ == "__main__":
    main()
