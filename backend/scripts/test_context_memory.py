"""上下文记忆闭环自测：验证「记得之前聊过的事」端到端可演示。

闭环三环节（隐私 A 前提：原始对话从不落库，DB 只存蒸馏后的记忆）：
  1. 蒸馏记忆已落库（key_facts / life_memories）；
  2. build_context() 把这些记忆拼进 system_prompt；
  3. 引擎读取 system_prompt，在对话中主动引用历史记忆。

本测试用 FakeEngine 验证第 2、3 环（不需任何凭证）。真实 Seeduplex
同样接收该 system_prompt 注入，行为一致。

运行：.venv/bin/python -m backend.scripts.test_context_memory
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from ..config import ArkConfig
from ..engine.fake import FakeEngine, _extract_memory_hint
from ..memory import MemoryService, MemoryStore

ELDER = "elder-ctx-test"


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if (not cond and detail) else ''}")
    if not cond:
        raise AssertionError(f"{name} {detail}".strip())


def _ark_off() -> ArkConfig:
    """构造无凭证 ArkConfig（不触发真实蒸馏，纯测注入与引用）。"""
    cfg = ArkConfig.__new__(ArkConfig)
    object.__setattr__(cfg, "base_url", "x")
    object.__setattr__(cfg, "text_model", "x")
    return cfg


async def _test_memory_injected_into_prompt() -> str:
    """环节2：已落库的记忆应被 build_context 拼进 system_prompt。"""
    tmp = Path(tempfile.gettempdir()) / "ctx_mem.db"
    if tmp.exists():
        tmp.unlink()
    store = MemoryStore(str(tmp))
    await store.ensure_schema()

    # 模拟上次会话蒸馏出的记忆
    await store.add_key_fact(ELDER, "慢病", "有高血压", source="dialog")
    await store.add_life_memory(ELDER, "最近膝盖疼，走路不利索")

    svc = MemoryService(store, _ark_off())
    prompt, dialog_ctx = await svc.build_context(ELDER)

    _check("system_prompt 含重点事项", "高血压" in prompt)
    _check("system_prompt 含生活记忆", "膝盖疼" in prompt, prompt)
    _check("dialog_context 为空数组（会话内连贯交由引擎原生上下文）", dialog_ctx == [])
    print("---- 注入的 system_prompt ----")
    print(prompt)
    print("------------------------------")
    return prompt


async def _test_fake_engine_references_memory(prompt: str) -> None:
    """环节3：引擎读取 system_prompt 后，应在对话中主动引用历史记忆。"""
    hint = _extract_memory_hint(prompt)
    _check("能从 prompt 提取记忆提示", bool(hint), f"提取到：{hint!r}")

    captured = []

    engine = FakeEngine()
    engine.on_text = lambda role, text: captured.append((role, text)) or _noop()
    await engine.connect()
    await engine.start_session(system_prompt=prompt)

    # 模拟老人开口（送一帧音频即触发首轮开场白）
    await engine.send_audio(b"\x00" * 640)

    opening = next((t for r, t in captured if r == "assistant"), "")
    _check("引擎发出开场白", bool(opening), f"captured={captured}")
    _check("开场白引用了历史记忆（演示上下文连贯）", "我记得" in opening and hint[:6] in opening, opening)
    print(f"  引擎开场白：{opening}")


async def _test_no_memory_no_opening() -> None:
    """无历史记忆时不应硬凑开场白（首轮直接进正常脚本）。"""
    captured = []
    engine = FakeEngine()
    engine.on_text = lambda role, text: captured.append((role, text)) or _noop()
    await engine.connect()
    await engine.start_session(system_prompt="你是陪伴助手。")  # 无记忆块
    await engine.send_audio(b"\x00" * 640)
    first_assistant = next((t for r, t in captured if r == "assistant"), "")
    _check("无记忆时不发引用式开场白", "我记得" not in first_assistant, first_assistant)


def _noop():
    return asyncio.sleep(0)


async def main() -> None:
    prompt = await _test_memory_injected_into_prompt()
    await _test_fake_engine_references_memory(prompt)
    await _test_no_memory_no_opening()
    print("\n上下文记忆闭环测试全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
