"""控制帧契约测试（R5）：后端实现必须与 docs/protocol.md 声明的 type 集合一致。

层次说明：本测试校验「适老端 ↔ 后端」应用层控制帧（manager.py 收发的 JSON 信令），
与 test_protocol.py（火山底层二进制帧编解码）是两个不同层面，互不重叠。

校验思路（纯静态扫描，无需起服务 / 网络 / 凭证，CI 可稳定复现）：
  1. 从 manager.py 源码提取两类 type 字面量：
       - 下行 emit  ：形如  {"type": "X"}            （后端 -> 客户端）
       - 上行 compare：形如  data.get("type") == "X"   （客户端 -> 后端）
  2. 双向断言：
       - 代码出现的 type 必须都在文档声明集合内（防止代码偷加未评审的帧）
       - 文档声明的 type 必须都在代码里出现（防止文档声明了未实现/已废弃的帧）

任一方向不一致即失败，强制「文档 = 真源」与实现同步。

DOWNSTREAM_TYPES / UPSTREAM_TYPES 是 docs/protocol.md §3/§4 的机器可校验镜像，
协议变更时必须同步改文档与此处常量（见 protocol.md §6）。

运行：.venv/bin/python -m backend.scripts.test_protocol_contract
"""
from __future__ import annotations

import re
from pathlib import Path

# docs/protocol.md §4 下行控制帧（后端 -> 客户端）
DOWNSTREAM_TYPES = {"barge_in", "text", "status"}
# docs/protocol.md §3 上行控制帧（客户端 -> 后端）
UPSTREAM_TYPES = {"hangup"}

_MANAGER = Path(__file__).resolve().parent.parent / "session" / "manager.py"

# 下行：后端组装并下发的帧，形如 json.dumps({"type": "barge_in"...})
_EMIT_RE = re.compile(r'"type":\s*"(\w+)"')
# 上行：后端比较客户端帧类型，形如 data.get("type") == "hangup"
_COMPARE_RE = re.compile(r'\.get\(\s*"type"\s*\)\s*==\s*"(\w+)"')


def _check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    suffix = f"  {detail}" if (not cond and detail) else ""
    print(f"[{status}] {name}{suffix}")
    if not cond:
        raise AssertionError(f"{name} {detail}".strip())


def test_contract() -> None:
    source = _MANAGER.read_text(encoding="utf-8")
    emitted = set(_EMIT_RE.findall(source))
    compared = set(_COMPARE_RE.findall(source))

    _check(
        "下行 type 未越界（代码 ⊆ 文档）",
        emitted <= DOWNSTREAM_TYPES,
        f"越界: {emitted - DOWNSTREAM_TYPES}",
    )
    _check(
        "下行 type 无遗漏（文档 ⊆ 代码）",
        DOWNSTREAM_TYPES <= emitted,
        f"缺实现: {DOWNSTREAM_TYPES - emitted}",
    )
    _check(
        "上行 type 未越界（代码 ⊆ 文档）",
        compared <= UPSTREAM_TYPES,
        f"越界: {compared - UPSTREAM_TYPES}",
    )
    _check(
        "上行 type 无遗漏（文档 ⊆ 代码）",
        UPSTREAM_TYPES <= compared,
        f"缺处理: {UPSTREAM_TYPES - compared}",
    )


def main() -> None:
    test_contract()
    print("\n控制帧契约测试通过 ✅  （manager.py 与 docs/protocol.md 一致）")


if __name__ == "__main__":
    main()
