"""Fake 语音引擎：无凭证下脚本化模拟火山端到端回包。

用途：在没有 openspeech 语音凭证时，把「客户端 WS 网关 → 记忆注入 →
会话结束蒸馏 → 信号生成 → 子女端」整条链路端到端跑通。

它实现与 SeeduplexEngine 完全相同的 VoiceEngine 抽象与回调契约，
因此对 ConversationSession 而言行为一致——凭证到位后把 VOICE_ENGINE
切回 seeduplex 即可，上层无需任何改动（依赖倒置的价值）。

行为约定：
- start_session 记录注入的 system_prompt（供测试断言记忆是否生效）。
- 每收到约 1 秒上行音频（50 帧 20ms），驱动一轮脚本化问答：
  回传 user ASR 文本 -> assistant 文本 -> 若干帧 TTS 音频。
- 可注入 barge-in：在 Agent「说话」期间收到高能量音频时下发打断事件。
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from .base import EVENT_USER_SPEECH_START, VoiceEngine

# 脚本化对话：老人说的话 -> 小陪的回复。按轮次顺序消费。
_DEFAULT_SCRIPT = [
    ("我今天有点没精神，膝盖还疼。", "您要多注意休息，膝盖疼得厉害就去医院看看。"),
    ("我有高血压，得记得吃药。", "好的，我会记得提醒您按时吃降压药。"),
    ("跟你聊聊天我心里舒服多了。", "能陪您说说话我也很开心，您随时找我。"),
]

_FRAMES_PER_TURN = 50  # 约 1s（20ms/帧）上行后触发一轮回复
_TTS_FRAMES = 12       # 每轮回复模拟下发的 TTS 音频帧数
_TTS_FRAME_BYTES = 960  # 20ms @24k/mono/s16le = 480 采样 * 2 字节


class FakeEngine(VoiceEngine):
    def __init__(self, script: Optional[List[tuple]] = None) -> None:
        super().__init__()
        self._script = script or _DEFAULT_SCRIPT
        self._turn = 0
        self._frame_count = 0
        self._running = False
        self._finished = asyncio.Event()
        self.injected_prompt = ""  # 供测试断言记忆注入

    async def connect(self) -> None:
        self._running = True

    async def start_session(self, system_prompt: str = "", dialog_context: Optional[list] = None) -> None:
        self.injected_prompt = system_prompt

    async def send_audio(self, pcm: bytes) -> None:
        """累计上行帧；达到阈值即驱动一轮脚本化问答。"""
        if not self._running:
            return
        self._frame_count += 1
        if self._frame_count >= _FRAMES_PER_TURN:
            self._frame_count = 0
            await self._emit_turn()

    async def _emit_turn(self) -> None:
        if self._turn >= len(self._script):
            return
        user_text, bot_text = self._script[self._turn]
        self._turn += 1

        if self.on_event:
            await self.on_event(EVENT_USER_SPEECH_START, {})  # 老人开口
        if self.on_text:
            await self.on_text("user", user_text)
            await self.on_text("assistant", bot_text)
        if self.on_audio:
            for _ in range(_TTS_FRAMES):
                await self.on_audio(b"\x00" * _TTS_FRAME_BYTES)  # 静音 PCM 占位

    async def finish_session(self) -> None:
        self._finished.set()

    async def close(self) -> None:
        self._running = False
        self._finished.set()

    async def receive_loop(self) -> None:
        """阻塞直到 finish/close，模拟真实引擎的接收循环生命周期。"""
        await self._finished.wait()
