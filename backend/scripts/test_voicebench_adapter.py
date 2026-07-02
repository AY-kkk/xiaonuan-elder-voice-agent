"""VoiceBench 适配器 smoke test：fake 引擎 + 本地 wav manifest。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

from .test_e2e import _free_port, _wait_health

ELDER = "voicebench-elder"


def _check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} 失败：{detail}")
    print(f"[PASS] {label}")


def _write_silence_wav(path: Path, seconds: float = 3.2) -> None:
    sample_rate = 16000
    sample_count = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * sample_count)


async def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "voicebench_adapter.db"
        wav_path = tmp_path / "sample.wav"
        manifest_path = tmp_path / "manifest.jsonl"
        output_path = tmp_path / "results.jsonl"
        _write_silence_wav(wav_path)
        manifest_path.write_text(
            json.dumps(
                {"id": "sample-001", "audio_path": str(wav_path), "subset": "smoke"},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        env = dict(
            os.environ,
            VOICE_ENGINE="fake",
            DB_PATH=str(db_path),
            PORT=str(port),
            HOST="127.0.0.1",
            ARK_API_KEY="",
            DISTILLATION_EXPORT_ENABLED="0",
        )
        server = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "backend.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            env=env,
        )
        try:
            await _wait_health(base)
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tools.evals.voicebench_adapter.run_manifest",
                "--base-url",
                base,
                "--elder-id",
                ELDER,
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            _check(
                "CLI 正常退出",
                proc.returncode == 0,
                (stdout + stderr).decode("utf-8", errors="replace"),
            )
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            _check("输出一条结果", len(rows) == 1, str(rows))
            _check("结果 id 保持一致", rows[0]["id"] == "sample-001")
            _check("assistant 回复非空", bool(rows[0]["response"]), json.dumps(rows[0], ensure_ascii=False))
            _check("记录收到下行音频", rows[0]["metadata"]["audio_bytes"] > 0)
            print("\nVoiceBench adapter smoke test 全部通过")
        finally:
            server.terminate()
            try:
                await asyncio.wait_for(server.wait(), timeout=5)
            except asyncio.TimeoutError:
                server.kill()


if __name__ == "__main__":
    asyncio.run(main())
