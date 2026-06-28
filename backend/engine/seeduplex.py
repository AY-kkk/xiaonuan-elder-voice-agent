"""Seeduplex 引擎适配器：把豆包端到端实时语音客户端包装为 VoiceEngine。

这是达标主路线——原生全双工、可打断、亚秒延迟。
仅做事件映射，复用 volc.client 的协议实现。
"""
from __future__ import annotations

from typing import Optional

from ..config import VolcConfig
from ..volc.client import VolcRealtimeClient
from ..volc.events import ServerEvent
from .base import EVENT_SESSION_ERROR, EVENT_USER_SPEECH_START, VoiceEngine


class SeeduplexEngine(VoiceEngine):
    def __init__(self, cfg: VolcConfig) -> None:
        super().__init__()
        self._client = VolcRealtimeClient(cfg)

    async def connect(self) -> None:
        self._client.on_audio = self._forward_audio
        self._client.on_text = self._forward_text
        self._client.on_event = self._forward_event
        await self._client.connect()

    async def start_session(
        self,
        system_prompt: str = "",
        dialog_context: Optional[list] = None,
        speaker: Optional[str] = None,
    ) -> None:
        await self._client.start_session(
            system_prompt=system_prompt, dialog_context=dialog_context, speaker=speaker
        )

    async def send_audio(self, pcm: bytes) -> None:
        await self._client.send_audio(pcm)

    async def finish_session(self) -> None:
        await self._client.finish_session()

    async def close(self) -> None:
        await self._client.close()

    async def receive_loop(self) -> None:
        await self._client.receive_loop()

    # ---- 事件映射 ----
    async def _forward_audio(self, chunk: bytes) -> None:
        if self.on_audio:
            await self.on_audio(chunk)

    async def _forward_text(self, role: str, text: str) -> None:
        if self.on_text:
            await self.on_text(role, text)

    async def _forward_event(self, event_id: int, data: dict) -> None:
        if not self.on_event:
            return
        if event_id == ServerEvent.ASR_INFO:
            await self.on_event(EVENT_USER_SPEECH_START, data)
        elif event_id == ServerEvent.SESSION_FAILED:
            await self.on_event(EVENT_SESSION_ERROR, data)
