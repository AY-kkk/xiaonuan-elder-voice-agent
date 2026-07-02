"""匿名会话轨迹导出。

该模块只负责为离线蒸馏准备本地 JSONL 轨迹文件。默认关闭，不落库，
不导出音频，也不把真实 elder_id 写入训练样本。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_ADDRESS_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9]{0,16}(?:路|街|巷|弄|小区|社区|村|镇|区|县|市)\s*\d*号?"
)


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
    def __init__(self, config: Optional[TrajectoryExportConfig] = None) -> None:
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
        created_at: Optional[float] = None,
    ) -> Optional[Path]:
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
