"""通过小暖老人端 WebSocket 跑一条语音评测样本。"""
from __future__ import annotations

import asyncio
import json
import wave
from contextlib import suppress
from pathlib import Path
from typing import Iterator

import websockets

_EXPECTED_SAMPLE_RATE = 16000
_EXPECTED_CHANNELS = 1
_EXPECTED_SAMPLE_WIDTH = 2


def validate_wav(path: Path) -> tuple[int, int]:
    """校验输入 wav 是否能直接作为小暖上行 PCM 源。"""
    if not path.exists():
        raise FileNotFoundError(f"音频文件不存在：{path}")
    try:
        with wave.open(str(path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()
    except wave.Error as exc:
        raise ValueError(f"不是有效的 wav 文件：{path}") from exc

    if channels != _EXPECTED_CHANNELS:
        raise ValueError(f"只支持 mono wav，当前 channels={channels}")
    if sample_width != _EXPECTED_SAMPLE_WIDTH:
        raise ValueError(f"只支持 16-bit PCM wav，当前 sample_width={sample_width}")
    if sample_rate != _EXPECTED_SAMPLE_RATE:
        raise ValueError(f"只支持 16kHz wav，当前 sample_rate={sample_rate}")
    if compression != "NONE":
        raise ValueError(f"只支持未压缩 PCM wav，当前 compression={compression}")
    return sample_rate, frame_count


def iter_wav_pcm_frames(path: Path, frame_ms: int = 20) -> Iterator[bytes]:
    """把 wav 切成 WebSocket 上行所需的 PCM 帧。"""
    sample_rate, _frame_count = validate_wav(path)
    frames_per_chunk = max(1, int(sample_rate * frame_ms / 1000))
    with wave.open(str(path), "rb") as wav_file:
        while True:
            chunk = wav_file.readframes(frames_per_chunk)
            if not chunk:
                break
            yield chunk


async def run_voice_session(
    ws_url: str,
    wav_path: Path,
    *,
    wait_after_audio: float = 0.5,
    send_interval: float = 0.005,
) -> dict:
    """发送一条 wav 到小暖 WS，返回字幕和音频统计。"""
    assistant_texts = []
    user_texts = []
    statuses = []
    audio_bytes = 0
    barge_in = 0

    async with websockets.connect(ws_url, max_size=None) as ws:
        async def reader() -> None:
            nonlocal audio_bytes, barge_in
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        audio_bytes += len(message)
                        continue
                    with suppress(json.JSONDecodeError):
                        data = json.loads(message)
                        msg_type = data.get("type")
                        if msg_type == "text":
                            role = data.get("role")
                            text = str(data.get("text", ""))
                            if role == "assistant" and text:
                                assistant_texts.append(text)
                            elif role == "user" and text:
                                user_texts.append(text)
                        elif msg_type == "barge_in":
                            barge_in += 1
                        elif msg_type == "status":
                            statuses.append(data)
            except websockets.ConnectionClosed:
                return

        reader_task = asyncio.create_task(reader())
        try:
            for frame in iter_wav_pcm_frames(wav_path):
                await ws.send(frame)
                if send_interval > 0:
                    await asyncio.sleep(send_interval)
            if wait_after_audio > 0:
                await asyncio.sleep(wait_after_audio)
            with suppress(websockets.ConnectionClosed):
                await ws.send(json.dumps({"type": "hangup"}, ensure_ascii=False))
            await asyncio.sleep(0.2)
        finally:
            reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await reader_task

    return {
        "assistant_texts": assistant_texts,
        "user_texts": user_texts,
        "audio_bytes": audio_bytes,
        "barge_in": barge_in,
        "statuses": statuses,
    }
