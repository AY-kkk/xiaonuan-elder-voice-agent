"""L3 分层记忆单测：不依赖语音凭证，验证 store/上下文拼装/蒸馏降级。

运行：.venv/bin/python -m backend.scripts.test_memory
真实蒸馏（需 ARK_API_KEY）：.venv/bin/python -m backend.scripts.test_memory --distill
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from ..config import ArkConfig
from ..memory import MemoryService, MemoryStore

ELDER = "elder-test"


async def _test_store_and_context() -> None:
    tmp = Path(tempfile.gettempdir()) / "memory_test.db"
    if tmp.exists():
        tmp.unlink()
    store = MemoryStore(str(tmp))
    await store.ensure_schema()

    # 层级 A：子女预设 + 对话识别去重
    await store.add_key_fact(ELDER, "用药", "每天早上提醒吃降压药", source="parent")
    await store.add_key_fact(ELDER, "用药", "每天早上提醒吃降压药", source="parent")  # 重复
    await store.add_key_fact(ELDER, "慢病", "有高血压", source="dialog")
    await store.add_key_fact(ELDER, "乱填分类", "应回落到其他", source="dialog")
    facts = await store.list_key_facts(ELDER)
    assert len(facts) == 3, f"层级A去重失败：{facts}"
    assert any(f["category"] == "其他" for f in facts), "非法分类未回落到其他"
    dialog_facts = [f for f in facts if f["source"] == "dialog"]
    assert all(f["status"] == "pending" for f in dialog_facts), facts

    # 层级 B：插入 + 近优读取
    for i in range(3):
        await store.add_life_memory(ELDER, f"老人提到膝盖疼第{i}天")
    await store.add_life_memory(ELDER, "老人提到膝盖疼第0天")  # 重复忽略
    mems = await store.recent_life_memories(ELDER, limit=10)
    assert len(mems) == 3, f"层级B去重失败：{mems}"

    # 上下文拼装：应含人设 + 重点事项 + 生活记忆
    ark_off = ArkConfig.__new__(ArkConfig)
    object.__setattr__(ark_off, "base_url", "x")
    object.__setattr__(ark_off, "text_model", "x")
    service = MemoryService(store, ark_off)
    prompt, ctx = await service.build_context(ELDER)
    assert "小陪" in prompt and "重点事项" in prompt and "膝盖疼" in prompt, prompt
    assert "有高血压" not in prompt, "待确认的对话识别重点事项不应注入 prompt"
    assert ctx == []
    pending = next(f for f in facts if f["category"] == "慢病")
    await store.update_key_fact_status(ELDER, pending["id"], "active", expires_days=365)
    prompt, _ = await service.build_context(ELDER)
    assert "有高血压" in prompt, "确认后的重点事项应注入 prompt"
    print("[PASS] store 读写 + 上下文拼装")
    print("---- 注入的 system_prompt 预览 ----")
    print(prompt)
    print("----------------------------------")


async def _test_distill_degrade() -> None:
    """无 ARK_API_KEY 时 distill 应静默跳过、不抛异常。"""
    tmp = Path(tempfile.gettempdir()) / "memory_test_degrade.db"
    if tmp.exists():
        tmp.unlink()
    store = MemoryStore(str(tmp))
    await store.ensure_schema()

    import os

    saved = os.environ.pop("ARK_API_KEY", None)
    try:
        cfg = ArkConfig()
        assert not cfg.enabled, "未设置 key 时 enabled 应为 False"
        service = MemoryService(store, cfg)
        await service.distill(ELDER, [{"role": "user", "text": "你好"}])  # 不应抛
        assert await store.recent_life_memories(ELDER) == []
        print("[PASS] 无方舟凭证时蒸馏降级（跳过、不阻塞）")
    finally:
        if saved is not None:
            os.environ["ARK_API_KEY"] = saved


async def _test_distill_real() -> None:
    """真实调方舟蒸馏并落库（需 ARK_API_KEY）。"""
    tmp = Path(tempfile.gettempdir()) / "memory_test_real.db"
    if tmp.exists():
        tmp.unlink()
    store = MemoryStore(str(tmp))
    await store.ensure_schema()
    cfg = ArkConfig()
    if not cfg.enabled:
        print("[SKIP] 未配置 ARK_API_KEY，跳过真实蒸馏")
        return
    service = MemoryService(store, cfg)
    transcript = [
        {"role": "user", "text": "我这两天膝盖有点疼，走路不太利索。"},
        {"role": "assistant", "text": "您要多注意休息，疼得厉害还是去医院看看。"},
        {"role": "user", "text": "我有糖尿病，甜的东西医生让我少吃。"},
        {"role": "assistant", "text": "好的，那我以后提醒您少吃甜食。"},
    ]
    await service.distill(ELDER, transcript)
    facts = await store.list_key_facts(ELDER)
    mems = await store.recent_life_memories(ELDER)
    print(f"[REAL] 蒸馏出 {len(facts)} 条重点事项 / {len(mems)} 条生活记忆")
    for f in facts:
        print(f"  A[{f['category']}] {f['content']}")
    for m in mems:
        print(f"  B {m}")
    assert facts or mems, "真实蒸馏未产出任何记忆"
    print("[PASS] 真实蒸馏落库")


async def main() -> None:
    await _test_store_and_context()
    await _test_distill_degrade()
    if "--distill" in sys.argv:
        await _test_distill_real()
    print("\n所有 L3 记忆自测通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
