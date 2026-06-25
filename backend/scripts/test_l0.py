"""L0 验证脚本：后端 <-> 火山 端到端打通。

流程：连接 -> StartSession -> 流式发送一段测试 PCM（默认朗读一句话的录音，
若无录音则发送静音帧触发模型问候）-> 接收 TTS 音频与文本 -> 落地音频到文件。

用法：
  1. 在项目根目录 .env 填好 VOLC_APP_ID / VOLC_ACCESS_TOKEN
  2. python -m backend.scripts.test_l0 [可选: 输入PCM文件路径 16k/mono/s16le]
  3. 输出音频写入 backend/scripts/l0_output.pcm（24k/mono/s16le）

注意：本脚本需真实凭证联网运行，属于人工验证步骤。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from ..config import load_config
from ..volc.client import VolcRealtimeClient
from ..volc.events import ServerEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test_l0")

_FRAME_BYTES = 640  # 20ms @ 16k/mono/s16le
_OUTPUT = Path(__file__).resolve().parent / "l0_output.pcm"


def _load_input_pcm(path: str | None) -> bytes:
    if path and Path(path).exists():
        return Path(path).read_bytes()
    # 无输入则发送 2s 静音，依赖 keep_alive，模型通常会主动问候
    return b"\x00" * (_FRAME_BYTES * 100)


async def main() -> None:
    cfg = load_config()
    pcm = _load_input_pcm(sys.argv[1] if len(sys.argv) > 1 else None)

    audio_chunks: list[bytes] = []

    client = VolcRealtimeClient(cfg.volc)

    async def on_audio(chunk: bytes) -> None:
        audio_chunks.append(chunk)

    async def on_text(role: str, text: str) -> None:
        if text:
            logger.info("[%s] %s", role, text)

    async def on_event(event_id: int, data: dict) -> None:
        logger.info("event=%s data=%s", event_id, data)

    client.on_audio = on_audio
    client.on_text = on_text
    client.on_event = on_event

    await client.connect()
    await client.start_session(system_prompt="你是老人的语音陪伴助手，温和、有耐心。")

    async def feed() -> None:
        for i in range(0, len(pcm), _FRAME_BYTES):
            await client.send_audio(pcm[i : i + _FRAME_BYTES])
            await asyncio.sleep(0.02)  # 20ms 节流
        await client.finish_session()

    recv_task = asyncio.create_task(client.receive_loop())
    feed_task = asyncio.create_task(feed())

    try:
        await asyncio.wait_for(recv_task, timeout=30)
    except asyncio.TimeoutError:
        logger.warning("接收超时（30s），结束")
    finally:
        feed_task.cancel()
        await client.close()

    if audio_chunks:
        _OUTPUT.write_bytes(b"".join(audio_chunks))
        logger.info("✅ 收到 TTS 音频 %d 字节，已写入 %s", sum(len(c) for c in audio_chunks), _OUTPUT)
    else:
        logger.error("❌ 未收到任何音频，请检查凭证/网络/事件日志")


if __name__ == "__main__":
    asyncio.run(main())
