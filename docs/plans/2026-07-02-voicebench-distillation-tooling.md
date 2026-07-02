# VoiceBench Distillation Tooling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为“小暖”接入隔离的 VoiceBench 评测能力，并新增默认关闭的匿名蒸馏轨迹导出，为后续 EasyDistill / AgentKD / Agent Distillation 训练准备数据。

**Architecture:** VoiceBench 作为外部评测工具固定在 `third_party/voicebench`，主应用通过 `tools/evals/voicebench_adapter/` 与它交换 JSONL，不把其依赖加入线上后端。轨迹导出放在 `backend/distillation/`，只在环境变量显式开启时把脱敏文本轨迹写入 `.gitignore` 覆盖的 `artifacts/` 目录。

**Tech Stack:** Python 3、FastAPI、WebSocket、SQLite、标准库 `wave/json/hashlib/re/pathlib`、现有 `websockets/httpx` 测试依赖、git submodule。

---

### Task 1: 固定 VoiceBench 外部仓库与产物忽略规则

**Files:**
- Create/Modify: `.gitmodules`
- Create: `third_party/voicebench` via git submodule
- Modify: `.gitignore`

**Step 1: 添加 VoiceBench 子模块**

Run:

```bash
git submodule add https://github.com/MatthewCYM/VoiceBench.git third_party/voicebench
```

Expected: `.gitmodules` 出现 `third_party/voicebench`，`git status --short` 显示 `.gitmodules` 和 `third_party/voicebench`。

**Step 2: 更新 `.gitignore`**

追加：

```gitignore

# ---- 离线评测 / 蒸馏轨迹产物（默认不入库）----
artifacts/
eval_outputs/
distillation_exports/
```

**Step 3: 验证子模块固定**

Run:

```bash
git submodule status third_party/voicebench
```

Expected: 输出一个 commit hash 和 `third_party/voicebench` 路径。

**Step 4: Commit**

```bash
git add .gitmodules .gitignore third_party/voicebench
git commit -m "chore: add voicebench evaluation submodule"
```

---

### Task 2: 为轨迹导出写失败测试

**Files:**
- Create: `backend/distillation/__init__.py`
- Create: `backend/distillation/trajectory_exporter.py`
- Create: `backend/scripts/test_trajectory_export.py`

**Step 1: 先创建最小空模块**

`backend/distillation/__init__.py`:

```python
"""离线蒸馏数据准备工具。"""
```

`backend/distillation/trajectory_exporter.py`:

```python
"""匿名会话轨迹导出。"""
```

**Step 2: 写失败测试**

`backend/scripts/test_trajectory_export.py`:

```python
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from backend.distillation.trajectory_exporter import (
    TrajectoryExporter,
    load_trajectory_export_config,
    redact_text,
)


def _check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} 失败：{detail}")
    print(f"[PASS] {label}")


async def _test_disabled_export_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ.pop("DISTILLATION_EXPORT_ENABLED", None)
        os.environ["DISTILLATION_EXPORT_DIR"] = tmp_dir
        exporter = TrajectoryExporter(load_trajectory_export_config())
        await exporter.export(
            elder_id="elder-001",
            session_id="session-001",
            transcript=[{"role": "user", "text": "你好"}],
            engine="fake",
        )
        _check("默认关闭时不写文件", not list(Path(tmp_dir).glob("*.jsonl")))


async def _test_enabled_export_writes_redacted_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["DISTILLATION_EXPORT_ENABLED"] = "1"
        os.environ["DISTILLATION_EXPORT_DIR"] = tmp_dir
        exporter = TrajectoryExporter(load_trajectory_export_config())
        await exporter.export(
            elder_id="elder-001",
            session_id="session-001",
            transcript=[
                {"role": "user", "text": "我的电话是 13800138000，住在幸福路 12 号。"},
                {"role": "assistant", "text": "我记下了，会提醒您。"},
            ],
            engine="fake",
        )
        files = list(Path(tmp_dir).glob("*.jsonl"))
        _check("开启后写入 JSONL", len(files) == 1, str(files))
        row = json.loads(files[0].read_text(encoding="utf-8").strip())
        blob = json.dumps(row, ensure_ascii=False)
        _check("不写真实 elder_id", "elder-001" not in blob)
        _check("手机号脱敏", "13800138000" not in blob)
        _check("地址关键词脱敏", "幸福路" not in blob)
        _check("schema 正确", row["schema_version"] == "xiaonuan-agent-trajectory-v1")


async def _test_empty_transcript_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        os.environ["DISTILLATION_EXPORT_ENABLED"] = "1"
        os.environ["DISTILLATION_EXPORT_DIR"] = tmp_dir
        exporter = TrajectoryExporter(load_trajectory_export_config())
        await exporter.export(
            elder_id="elder-001",
            session_id="session-001",
            transcript=[],
            engine="fake",
        )
        _check("空 transcript 不写文件", not list(Path(tmp_dir).glob("*.jsonl")))


def _test_redact_text() -> None:
    text = redact_text("手机号 13800138000，身份证 110101199001011234，住在幸福路。")
    _check("手机号被替换", "13800138000" not in text)
    _check("身份证被替换", "110101199001011234" not in text)
    _check("地址关键词被替换", "幸福路" not in text)


async def main() -> None:
    await _test_disabled_export_writes_nothing()
    await _test_enabled_export_writes_redacted_jsonl()
    await _test_empty_transcript_writes_nothing()
    _test_redact_text()
    print("\n轨迹导出测试全部通过")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 3: 运行测试确认失败**

Run:

```bash
python -m backend.scripts.test_trajectory_export
```

Expected: FAIL，提示 `TrajectoryExporter` 或 `load_trajectory_export_config` 不存在。

---

### Task 3: 实现匿名轨迹导出模块

**Files:**
- Modify: `backend/distillation/trajectory_exporter.py`
- Modify: `backend/distillation/__init__.py`

**Step 1: 实现导出器**

`backend/distillation/trajectory_exporter.py` 应包含：

```python
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_ADDRESS_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{0,16}(?:路|街|巷|弄|小区|社区|村|镇|区|县|市)\s*\d*号?")


@dataclass(frozen=True)
class TrajectoryExportConfig:
    enabled: bool
    output_dir: Path
    salt: str


def load_trajectory_export_config() -> TrajectoryExportConfig:
    enabled = os.getenv("DISTILLATION_EXPORT_ENABLED", "").strip() == "1"
    raw_dir = os.getenv("DISTILLATION_EXPORT_DIR", "artifacts/distillation_exports").strip()
    output_dir = Path(raw_dir)
    if not output_dir.is_absolute():
        output_dir = _PROJECT_ROOT / output_dir
    salt = os.getenv("DISTILLATION_EXPORT_SALT", "xiaonuan-local-dev").strip()
    return TrajectoryExportConfig(enabled=enabled, output_dir=output_dir, salt=salt)


def redact_text(text: object) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = _PHONE_RE.sub("[手机号]", value)
    value = _ID_RE.sub("[身份证号]", value)
    value = _LONG_NUMBER_RE.sub("[长数字]", value)
    value = _ADDRESS_RE.sub("[地址]", value)
    return value


class TrajectoryExporter:
    def __init__(self, config: TrajectoryExportConfig | None = None) -> None:
        self._config = config or load_trajectory_export_config()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def export(
        self,
        *,
        elder_id: str,
        session_id: str,
        transcript: list[dict],
        engine: str,
        created_at: float | None = None,
    ) -> Path | None:
        if not self._config.enabled or not transcript:
            return None
        turns = _clean_turns(transcript)
        if not turns:
            return None
        created = created_at or time.time()
        elder_ref = _hash_ref(elder_id, self._config.salt)
        sample_id = _hash_ref(f"{elder_id}:{session_id}:{created}", self._config.salt)
        payload = {
            "schema_version": "xiaonuan-agent-trajectory-v1",
            "sample_id": sample_id,
            "source": "xiaonuan_ws_session",
            "elder_ref": elder_ref,
            "turns": turns,
            "metadata": {
                "engine": str(engine or ""),
                "privacy": "redacted",
                "created_at": created,
            },
        }
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        path = self._config.output_dir / f"{sample_id}.jsonl"
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return path


def _clean_turns(transcript: Iterable[dict]) -> list[dict]:
    turns = []
    for item in transcript:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        if role not in {"user", "assistant"}:
            continue
        text = redact_text(item.get("text", ""))
        if text:
            turns.append({"role": role, "text": text})
    return turns


def _hash_ref(value: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return digest[:16]
```

`backend/distillation/__init__.py`:

```python
"""离线蒸馏数据准备工具。"""
from .trajectory_exporter import TrajectoryExporter, load_trajectory_export_config, redact_text

__all__ = ["TrajectoryExporter", "load_trajectory_export_config", "redact_text"]
```

**Step 2: 运行测试确认通过**

Run:

```bash
python -m backend.scripts.test_trajectory_export
```

Expected: PASS，输出“轨迹导出测试全部通过”。

**Step 3: Commit**

```bash
git add backend/distillation backend/scripts/test_trajectory_export.py
git commit -m "feat: add anonymized trajectory export"
```

---

### Task 4: 将轨迹导出接入会话结束钩子

**Files:**
- Modify: `backend/server.py`
- Modify: `backend/scripts/test_trajectory_export.py`

**Step 1: 更新容器装配**

在 `backend/server.py` 中：

- import `TrajectoryExporter`
- `AppContainer` 增加 `trajectory_exporter: TrajectoryExporter`
- `build_container()` 创建 `trajectory_exporter = TrajectoryExporter()`
- 模块级增加 `_trajectory_exporter = _container.trajectory_exporter`

**Step 2: 修改 `_on_session_end`**

把签名从：

```python
def _on_session_end(elder_id: str):
```

改成：

```python
def _on_session_end(elder_id: str, session_id: str):
```

在 handler 中增加：

```python
        if _trajectory_exporter.enabled:
            _jobs.submit(
                elder_id,
                "trajectory_export",
                lambda: _trajectory_exporter.export(
                    elder_id=elder_id,
                    session_id=session_id,
                    transcript=transcript,
                    engine=cfg.engine,
                ),
            )
```

并将 `ConversationSession` 初始化处改为：

```python
        on_session_end=_on_session_end(elder_id, session_id),
```

**Step 3: 增加集成测试**

在 `backend/scripts/test_trajectory_export.py` 增加一个轻量集成断言：直接调用 `server._on_session_end("elder-001", "session-001")` 前，使用临时目录和开启环境变量；调用 handler 后等待后台任务完成，验证文件生成。

如果直接复用模块级 `server` 会受启动时环境变量影响，则只测试 `TrajectoryExporter`，把完整集成覆盖交给 `test_e2e` 后续环境变量运行，不强行重载全局 app。

**Step 4: 运行测试**

Run:

```bash
python -m backend.scripts.test_trajectory_export
```

Expected: PASS。

Run:

```bash
python -m backend.scripts.test_e2e
```

Expected: PASS。

**Step 5: Commit**

```bash
git add backend/server.py backend/scripts/test_trajectory_export.py
git commit -m "feat: export trajectories after voice sessions"
```

---

### Task 5: 创建 VoiceBench 适配器包与 WebSocket 客户端

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/evals/__init__.py`
- Create: `tools/evals/voicebench_adapter/__init__.py`
- Create: `tools/evals/voicebench_adapter/ws_client.py`

**Step 1: 写 WebSocket 客户端**

`ws_client.py` 应包含：

- `validate_wav(path: Path) -> tuple[int, int]`
- `iter_wav_pcm_frames(path: Path, frame_ms: int = 20) -> Iterator[bytes]`
- `async run_voice_session(ws_url: str, wav_path: Path, wait_after_audio: float = 0.5) -> dict`

核心要求：

- 只接受 16kHz、mono、16-bit PCM wav。
- 用 `wave` 标准库读取，不新增依赖。
- 收集 `assistant` 文本、`user` 文本、TTS 字节数、barge-in 次数。
- `hangup` 后正常关闭。
- 捕获 JSON 解析失败并忽略非协议文本。

**Step 2: 写最小模块导出**

`tools/evals/voicebench_adapter/__init__.py`:

```python
"""小暖 VoiceBench 评测适配器。"""
```

**Step 3: 暂不提交，下一任务写 CLI 和测试后一起提交**

---

### Task 6: 增加 VoiceBench manifest CLI 与 smoke test

**Files:**
- Create: `tools/evals/voicebench_adapter/run_manifest.py`
- Create: `tools/evals/voicebench_adapter/README.md`
- Create: `backend/scripts/test_voicebench_adapter.py`

**Step 1: 写 CLI**

`run_manifest.py` 支持：

```bash
python -m tools.evals.voicebench_adapter.run_manifest \
  --base-url http://127.0.0.1:8000 \
  --elder-id eval-elder \
  --manifest path/to/manifest.jsonl \
  --output artifacts/evals/voicebench/xiaonuan-results.jsonl
```

Manifest 每行格式：

```json
{"id": "sample-001", "audio_path": "/absolute/path/to/sample.wav", "subset": "smoke"}
```

输出每行格式：

```json
{
  "id": "sample-001",
  "subset": "smoke",
  "model": "xiaonuan",
  "response": "assistant 文本拼接",
  "metadata": {
    "audio_bytes": 11520,
    "barge_in": 1,
    "ws_url": "ws://127.0.0.1:8000/ws/elder/eval-elder"
  }
}
```

**Step 2: 写 smoke test**

`backend/scripts/test_voicebench_adapter.py`：

- 使用临时目录生成 16kHz mono 16-bit 静音 wav。
- 启动 fake 引擎 uvicorn 临时服务，复用 `test_e2e.py` 的 `_free_port` / `_wait_health` 思路，避免重复复杂逻辑。
- 写一行 manifest。
- 调用 CLI 主函数或子进程。
- 验证输出 JSONL 存在且 `response` 非空。

**Step 3: 运行测试**

Run:

```bash
python -m backend.scripts.test_voicebench_adapter
```

Expected: PASS。

**Step 4: Commit**

```bash
git add tools backend/scripts/test_voicebench_adapter.py
git commit -m "feat: add voicebench websocket adapter"
```

---

### Task 7: 增加蒸馏工具选型文档

**Files:**
- Create: `docs/research/distillation_tooling.md`

**Step 1: 写文档**

内容包含：

- 当前接入：VoiceBench。
- 当前新增：匿名轨迹导出。
- 后续候选：EasyDistill/AgentKD、Agent Distillation、KDFlow。
- 方法参考：DM-Codec、DistillAV、LLaVA-KD、Ultravox、DiVA。
- 每个工具的仓库地址、适用前提、暂不接入原因。
- 升级触发条件：已有匿名轨迹、评测基线、学生模型目标、算力预算。

**Step 2: 检查占位符**

Run:

```bash
rg -n "TBD|TODO|待定" docs/research/distillation_tooling.md
```

Expected: no output, exit code 1 is acceptable.

**Step 3: Commit**

```bash
git add docs/research/distillation_tooling.md
git commit -m "docs: document distillation tooling choices"
```

---

### Task 8: 全量回归与最终检查

**Files:**
- No direct edits unless tests expose issues.

**Step 1: 运行新增测试**

Run:

```bash
python -m backend.scripts.test_trajectory_export
```

Expected: PASS。

Run:

```bash
python -m backend.scripts.test_voicebench_adapter
```

Expected: PASS。

**Step 2: 运行既有隐私与链路回归**

Run:

```bash
python -m backend.scripts.test_privacy
```

Expected: PASS。

Run:

```bash
python -m backend.scripts.test_e2e
```

Expected: PASS。

Run:

```bash
python -m backend.scripts.test_session_isolation
```

Expected: PASS。

**Step 3: 检查工作区**

Run:

```bash
git status --short
```

Expected: clean 或只剩用户明确允许的未提交产物。`artifacts/` 下文件不应出现在状态里。

**Step 4: 最终说明**

向用户说明：

- VoiceBench 子模块位置和固定 commit。
- 轨迹导出默认关闭，以及如何开启。
- 如何跑 smoke eval。
- 已跑哪些测试，哪些未跑及原因。
