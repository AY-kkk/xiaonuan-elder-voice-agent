"""多会话隔离回归测试：并发多个 elder_id 同时读写，断言彼此数据零串扰。

回应核心顾虑「A 用户拿到 B 用户的回答/记忆」。借鉴 X-OmniClaw 的
"isolated runtime across sessions" 思想，把"按 elder_id 作用域隔离"从
"相信它是对的"变成"CI 每次回归都验证"。

为什么这样测才有效：
  - 不 mock 存储层，用真实 MemoryStore / SignalService 打真实临时 SQLite；
  - 用 asyncio.gather 真正并发写入，复现多协程竞争场景；
  - 双向断言：每个 elder 既要读到自己的全部数据，又绝不能读到任何别人的。

纯离线、不引新依赖（强制关闭方舟走规则摘要），可在 CI 稳定复现。
运行：.venv/bin/python -m backend.scripts.test_session_isolation
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

# 在导入配置前清空方舟 key，确保 ArkConfig.enabled=False，
# 全程走规则摘要、不发起任何网络调用（离线可复现，无降级 warning 噪声）。
os.environ.pop("ARK_API_KEY", None)

from ..config import ArkConfig
from ..memory.store import MemoryStore
from ..signals.engine import SignalService

# 每个 elder 用独有 token，便于交叉污染时精准定位泄漏来源
_ELDERS = {
    "elder-A": "唯一标识AAA",
    "elder-B": "唯一标识BBB",
    "elder-C": "唯一标识CCC",
}


def _offline_ark() -> ArkConfig:
    """构造一个一定离线的 ArkConfig（绕过 env），强制走规则摘要。"""
    cfg = ArkConfig.__new__(ArkConfig)
    object.__setattr__(cfg, "base_url", "x")
    object.__setattr__(cfg, "text_model", "x")
    return cfg


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if (not cond and detail) else ''}")
    if not cond:
        raise AssertionError(f"{name} {detail}".strip())


async def _seed_one(store: MemoryStore, svc: SignalService, elder: str, token: str) -> None:
    """为单个 elder 并发写入记忆 + 信号，数据里嵌入其专属 token。"""
    await store.add_key_fact(elder, "用药", f"{token}-降压药每日一次")
    await store.add_life_memory(elder, f"{token}-今天去公园散步很开心")
    transcript = [
        {"role": "user", "text": f"{token}-我膝盖有点疼，不过心情还行。"},
        {"role": "assistant", "text": f"{token}-多注意休息。"},
    ]
    sig = await svc.generate(elder, transcript)
    assert sig is not None, f"{elder} 信号生成失败"


async def _assert_isolation(store: MemoryStore, svc: SignalService) -> None:
    """逐个 elder 校验：只读到自己的 token，绝无他人 token。"""
    for elder, mine in _ELDERS.items():
        others = [t for e, t in _ELDERS.items() if e != elder]

        facts = await store.list_key_facts(elder)
        life = await store.recent_life_memories(elder)
        signals = await svc.list_signals(elder)

        # 自己的数据齐全
        _check(f"{elder} 读到自己的重点事项", any(mine in f["content"] for f in facts))
        _check(f"{elder} 读到自己的生活记忆", any(mine in m for m in life))
        _check(f"{elder} 读到自己的信号", len(signals) == 1, f"实际 {len(signals)} 条")

        # 绝不含任何他人 token（把所有读到的内容拼成一坨做子串扫描）
        blob = " ".join(
            [f["content"] for f in facts]
            + list(life)
            + [str(s) for s in signals]
        )
        for other in others:
            _check(f"{elder} 不含他人数据({other})", other not in blob,
                   "检测到跨会话串扰！")


async def main() -> None:
    tmp = Path(tempfile.gettempdir()) / "session_isolation_test.db"
    if tmp.exists():
        tmp.unlink()

    store = MemoryStore(str(tmp))
    svc = SignalService(str(tmp), _offline_ark())
    await store.ensure_schema()
    await svc.ensure_schema()

    # 真并发：所有 elder 同时写入，复现多协程竞争
    await asyncio.gather(*(_seed_one(store, svc, e, t) for e, t in _ELDERS.items()))
    print(f"[PASS] 并发写入 {len(_ELDERS)} 个会话完成")

    await _assert_isolation(store, svc)
    print(f"\n多会话隔离测试通过 ✅（{len(_ELDERS)} 个并发会话彼此零串扰）")


if __name__ == "__main__":
    asyncio.run(main())
