"""Fake 语音引擎：无凭证下脚本化模拟火山端到端回包，并真实反映注入的上下文。

用途：在没有 openspeech 语音凭证时，把「客户端 WS 网关 → 记忆注入 →
会话结束蒸馏 → 信号生成 → 子女端」整条链路端到端跑通，并可**演示三大功能**：

  1. 实时通话：累计上行音频帧驱动多轮脚本化问答，模拟双向语音交互。
  2. 上下文记忆：读取 start_session 注入的 system_prompt，若含历史记忆
     （重点事项/生活记忆），首轮回复主动引用，演示「记得之前聊过的事」。
  3. 语音克隆 + 人格：读取注入的 speaker（克隆音色）与人格设定，
     在回复里体现「用谁的音色/口吻说话」，演示克隆与角色再生。

它实现与 SeeduplexEngine 完全相同的 VoiceEngine 抽象与回调契约，
因此对 ConversationSession 而言行为一致——凭证到位后把 VOICE_ENGINE
切回 seeduplex 即可，上层无需任何改动（依赖倒置的价值）。
"""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional

from .base import EVENT_USER_SPEECH_START, VoiceEngine

# 基础脚本化对话：老人说的话 -> 小陪的回复。按轮次顺序消费。
_BASE_SCRIPT = [
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
        self._script = script or _BASE_SCRIPT
        self._turn = 0
        self._frame_count = 0
        self._running = False
        self._finished = asyncio.Event()
        # 供测试断言：注入的人设与音色
        self.injected_prompt = ""
        self.injected_speaker: Optional[str] = None
        # 演示用：从注入的 system_prompt 解析出的记忆与角色信息
        self._memory_hint = ""
        self._persona_name = ""
        self._opened = False  # 是否已发过「开场白」（首轮引用记忆）

    async def connect(self) -> None:
        self._running = True

    async def start_session(
        self,
        system_prompt: str = "",
        dialog_context: Optional[list] = None,
        speaker: Optional[str] = None,
    ) -> None:
        self.injected_prompt = system_prompt
        self.injected_speaker = speaker
        self._memory_hint = _extract_memory_hint(system_prompt)
        self._persona_name = _extract_persona_name(system_prompt)

    async def send_audio(self, pcm: bytes) -> None:
        """累计上行帧；达到阈值即驱动一轮脚本化问答。"""
        if not self._running:
            return
        # 首次收到音频：若注入了历史记忆，先发一句「主动引用记忆」的开场白，
        # 直观演示「上下文记忆」——AI 记得之前聊过的事。
        if not self._opened:
            self._opened = True
            if self._memory_hint:
                await self._emit_opening()
        self._frame_count += 1
        if self._frame_count >= _FRAMES_PER_TURN:
            self._frame_count = 0
            await self._emit_turn()

    async def _emit_opening(self) -> None:
        """开场白：引用注入的历史记忆，演示跨会话上下文连贯。"""
        text = f"{self._voice_prefix()}我记得您之前说过{self._memory_hint}，今天感觉怎么样？"
        if self.on_text:
            await self.on_text("assistant", text)
        await self._emit_tts()

    async def _emit_turn(self) -> None:
        if self._turn >= len(self._script):
            return
        user_text, bot_text = self._script[self._turn]
        self._turn += 1

        if self.on_event:
            await self.on_event(EVENT_USER_SPEECH_START, {})  # 老人开口
        if self.on_text:
            await self.on_text("user", user_text)
            await self.on_text("assistant", self._voice_prefix() + bot_text)
        await self._emit_tts()

    async def _emit_tts(self) -> None:
        if self.on_audio:
            for _ in range(_TTS_FRAMES):
                await self.on_audio(b"\x00" * _TTS_FRAME_BYTES)  # 静音 PCM 占位

    def _voice_prefix(self) -> str:
        """演示用前缀：体现当前发声音色/角色（克隆音色生效的可视化）。

        真实 Seeduplex 下音色由 speaker 决定、听感即知；fake 无音频内容，
        故在字幕里标注，便于联调时确认克隆音色/人格已正确注入。
        """
        if self._persona_name:
            return f"（{self._persona_name}的声音）"
        if self.injected_speaker:
            return f"（音色 {self.injected_speaker}）"
        return ""

    async def finish_session(self) -> None:
        self._finished.set()

    async def close(self) -> None:
        self._running = False
        self._finished.set()

    async def receive_loop(self) -> None:
        """阻塞直到 finish/close，模拟真实引擎的接收循环生命周期。"""
        await self._finished.wait()


def _extract_memory_hint(system_prompt: str) -> str:
    """从注入的 system_prompt 中提取一条最具体的历史记忆，用于开场白引用。

    优先取「最近聊到的事」(生活记忆)，其次「重点事项」。仅取一条、去掉前缀符号，
    保证开场白自然。无记忆则返回空串（首轮不发开场白）。
    """
    if not system_prompt:
        return ""
    # 生活记忆块：形如「- 老人提到膝盖疼」
    for marker in ("【最近聊到的事", "【需要特别记住"):
        idx = system_prompt.find(marker)
        if idx == -1:
            continue
        block = system_prompt[idx:]
        m = re.search(r"-\s*(?:\[[^\]]+\]\s*)?(.+)", block)
        if m:
            hint = m.group(1).strip()
            return hint[:30]  # 控制长度，开场白自然
    return ""


def _extract_persona_name(system_prompt: str) -> str:
    """从注入的人格段落中提取角色名（形如「- 角色：女儿小芳（女儿）」）。"""
    if not system_prompt:
        return ""
    m = re.search(r"角色[:：]\s*([^\n（(]+)", system_prompt)
    return m.group(1).strip()[:12] if m else ""
