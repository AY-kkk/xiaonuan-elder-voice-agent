"""会话管理：桥接「适老端客户端」与「语音引擎（VoiceEngine）」。

职责单一——双向音频/文本转发与会话生命周期管理，不关心底层引擎实现。
记忆注入与蒸馏通过回调注入（依赖倒置），与记忆模块解耦：
  - context_provider(): StartSession 前提供要注入的 system_prompt 与历史 QA
  - on_turn_text(role, text): 文本回调，用于累积转写、推送子女端信号
  - on_session_end(transcript): 会话结束回调，触发异步蒸馏

转发约定（适老端 <-> 后端）：
  - 二进制帧 = PCM 音频（上行 16k 下行 24k）
  - 文本帧(JSON) = 控制信令与字幕：{"type": "barge_in"|"text"|"status", ...}
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional, Union

from ..engine.base import EVENT_SESSION_ERROR, EVENT_USER_SPEECH_START, VoiceEngine

logger = logging.getLogger(__name__)

ContextProvider = Callable[[], Awaitable[tuple]]
SpeakerProvider = Callable[[], Awaitable[Optional[str]]]
TurnTextHandler = Callable[[str, str], Awaitable[None]]
SessionEndHandler = Callable[[list], Awaitable[None]]
ClientSend = Callable[[Union[bytes, str]], Awaitable[None]]


class ConversationSession:
    def __init__(
        self,
        elder_id: str,
        engine: VoiceEngine,
        client_send: ClientSend,
        context_provider: Optional[ContextProvider] = None,
        on_turn_text: Optional[TurnTextHandler] = None,
        on_session_end: Optional[SessionEndHandler] = None,
        speaker_provider: Optional[SpeakerProvider] = None,
    ) -> None:
        self._elder_id = elder_id
        self._client_send = client_send
        self._context_provider = context_provider
        self._speaker_provider = speaker_provider
        self._on_turn_text = on_turn_text
        self._on_session_end = on_session_end
        self._engine = engine
        self._transcript: list = []
        self._closed = False

    async def start(self) -> None:
        self._engine.on_audio = self._handle_audio
        self._engine.on_text = self._handle_text
        self._engine.on_event = self._handle_event

        await self._engine.connect()
        system_prompt, dialog_context = "", []
        if self._context_provider:
            system_prompt, dialog_context = await self._context_provider()
        speaker: Optional[str] = None
        if self._speaker_provider:
            speaker = await self._speaker_provider()
        await self._engine.start_session(
            system_prompt=system_prompt, dialog_context=dialog_context, speaker=speaker
        )
        await self._send_status("connected")

    async def push_audio(self, pcm: bytes) -> None:
        if not self._closed:
            await self._engine.send_audio(pcm)

    async def handle_client_text(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("type") == "hangup":
            await self.stop()

    async def run_until_end(self) -> None:
        try:
            await self._engine.receive_loop()
        finally:
            await self._finalize()

    async def stop(self) -> None:
        if self._closed:
            return
        await self._engine.finish_session()

    # ---- 引擎回调 ----
    async def _handle_audio(self, chunk: bytes) -> None:
        await self._client_send(chunk)

    async def _handle_text(self, role: str, text: str) -> None:
        if not text:
            return
        self._transcript.append({"role": role, "text": text})
        await self._client_send(json.dumps({"type": "text", "role": role, "text": text}, ensure_ascii=False))
        if self._on_turn_text:
            await self._on_turn_text(role, text)

    async def _handle_event(self, event_id: int, data: dict) -> None:
        if event_id == EVENT_USER_SPEECH_START:
            await self._client_send(json.dumps({"type": "barge_in"}))
        elif event_id == EVENT_SESSION_ERROR:
            await self._send_status("error", detail=str(data.get("error", "")))

    async def _finalize(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._send_status("ended")
        await self._engine.close()
        if self._on_session_end and self._transcript:
            try:
                await self._on_session_end(self._transcript)
            except Exception:  # 蒸馏失败绝不影响通话链路
                logger.exception("会话结束回调失败（已忽略，不影响下次通话）")

    async def _send_status(self, status: str, detail: str = "") -> None:
        await self._client_send(json.dumps({"type": "status", "status": status, "detail": detail}))
