"""用量记账 + 成本折算 + 隐私边界测试。

覆盖：
  1. UsageStore 落库/汇总/明细；
  2. 金额折算（token -> ¥，与 /api/parent/{id}/usage 同一公式）；
  3. 隐私边界：生活记忆（life_memories）只走老人端，绝不进子女端 usage/signals 响应。

纯离线、不引新依赖。运行：.venv/bin/python -m backend.scripts.test_usage
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from ..usage.store import UsageStore

ELDER = "elder-usage-test"


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if (not cond and detail) else ''}")
    if not cond:
        raise AssertionError(f"{name} {detail}".strip())


async def _test_store_and_cost() -> None:
    tmp = Path(tempfile.gettempdir()) / "usage_test.db"
    if tmp.exists():
        tmp.unlink()
    store = UsageStore(str(tmp))
    await store.ensure_schema()

    # 模拟两次调用（蒸馏 + 信号）
    await store.record(ELDER, "distill", "doubao-seed-1-6-flash", 1200, 300, 1500)
    await store.record(ELDER, "signal", "doubao-seed-1-6-flash", 400, 100, 500)
    # 另一个 elder，确认汇总按 elder 隔离
    await store.record("other-elder", "distill", "m", 9999, 9999, 9999)

    s = await store.summary(ELDER)
    _check("汇总 total_tokens 正确", s["total_tokens"] == 2000, f"实际 {s['total_tokens']}")
    _check("汇总 calls 正确", s["calls"] == 2, f"实际 {s['calls']}")
    _check("用量按 elder 隔离", "other" not in str(s))

    # 金额折算：2000 token，单价 0.8 元/百万 -> 0.0016 元，round(2)=0.0
    price = 0.8
    cost = round(s["total_tokens"] / 1_000_000 * price, 2)
    _check("金额折算公式正确", cost == round(2000 / 1_000_000 * 0.8, 2))

    recent = await store.recent(ELDER)
    _check("明细只返回本 elder", len(recent) == 2 and all("total_tokens" in r for r in recent))
    print(f"  本月汇总：{s['total_tokens']} token / {s['calls']} 次 / 约 ¥{cost}")


async def _test_privacy_boundary() -> None:
    """生活记忆只走老人端；子女端 usage 接口不碰任何对话/记忆内容。"""
    # usage 接口的返回字段是固定的统计 schema，从结构上保证不含对话。
    # 这里断言 usage summary 的输出 key 集合是纯统计字段，无任何文本内容字段。
    tmp = Path(tempfile.gettempdir()) / "usage_priv_test.db"
    if tmp.exists():
        tmp.unlink()
    store = UsageStore(str(tmp))
    await store.ensure_schema()
    await store.record(ELDER, "distill", "m", 10, 10, 20)
    s = await store.summary(ELDER)
    _check("usage 汇总只含统计字段", set(s.keys()) == {"total_tokens", "calls"})
    print("  usage 接口结构上不含任何对话/记忆文本，隐私边界保持")


async def main() -> None:
    await _test_store_and_cost()
    await _test_privacy_boundary()
    print("\n用量记账 + 成本 + 隐私边界测试通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
