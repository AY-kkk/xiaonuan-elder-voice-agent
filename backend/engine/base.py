"""语音引擎抽象接口：统一 Seeduplex（端到端）与方舟级联两条链路。

ConversationSession 只依赖此抽象（依赖倒置），不关心底层是哪种引擎。
所有引擎都通过三个回调把结果交给上层：
  - on_audio(pcm): 下行 TTS 音频（24k/mono/s16le）
  - on_text(role, text): 字幕（user=ASR 结果 / assistant=回复）
  - on_event(event_id, data): 控制事件（如 barge-in 触发判定）
"""
from __future__ import annotations

import abc
from typing import Awaitable, Callable, Optional

AudioCallback = Callable[[bytes], Awaitable[None]]
TextCallback = Callable[[str, str], Awaitable[None]]
EventCallback = Callable[[int, dict], Awaitable[None]]

# 引擎无关的内部事件码（与火山 ServerEvent 解耦，供上层统一处理）
EVENT_USER_SPEECH_START = 1001  # 检测到用户开口 -> barge-in
EVENT_SESSION_ERROR = 1002


class VoiceEngine(abc.ABC):
    """语音引擎统一接口。"""

    def __init__(self) -> None:
        self.on_audio: Optional[AudioCallback] = None
        self.on_text: Optional[TextCallback] = None
        self.on_event: Optional[EventCallback] = None

    @abc.abstractmethod
    async def connect(self) -> None:
        ...

    @abc.abstractmethod
    async def start_session(
        self,
        system_prompt: str = "",
        dialog_context: Optional[list] = None,
        speaker: Optional[str] = None,
    ) -> None:
        """speaker：本次会话发声音色（克隆 speaker_id）；None 则用引擎默认音色。"""
        ...

    @abc.abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """上行一帧 PCM（16k/mono/int16/LE）。"""
        ...

    @abc.abstractmethod
    async def finish_session(self) -> None:
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...

    @abc.abstractmethod
    async def receive_loop(self) -> None:
        """持续接收并分发回调，直到会话结束或连接关闭。"""
        ...
