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
