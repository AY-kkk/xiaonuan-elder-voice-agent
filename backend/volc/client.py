"""火山豆包端到端实时语音 WebSocket 客户端。

封装一次完整对话链路：
  StartConnection -> StartSession -> (TaskRequest 音频流) <-> (TTS/ASR/Chat 回包) -> FinishSession

设计要点：
- 后端是唯一持 Key 的代理层；鉴权 Header 全部在此注入。
- 上行音频为 PCM 16k/mono/int16/LE，20ms 一包（约 640B）。
- 下行 TTS 配置为 pcm_s16le / 24k，便于客户端直接播放。
- 通过回调把音频/文本/打断信号交给上层（gateway）处理，链路与业务解耦。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Awaitable, Callable

import websockets
from websockets.client import WebSocketClientProtocol

from ..config import VolcConfig
from . import protocol as proto
from .events import ClientEvent, ServerEvent

logger = logging.getLogger(__name__)

AudioCallback = Callable[[bytes], Awaitable[None]]
TextCallback = Callable[[str, str], Awaitable[None]]  # (role, text)
EventCallback = Callable[[int, dict], Awaitable[None]]  # (event_id, payload_json)


class VolcRealtimeClient:
    def __init__(self, cfg: VolcConfig) -> None:
        self._cfg = cfg
        self._ws: WebSocketClientProtocol | None = None
        self._session_id = ""
        self._connect_id = str(uuid.uuid4())
        self.on_audio: AudioCallback | None = None
        self.on_text: TextCallback | None = None
        self.on_event: EventCallback | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    # ---- 连接与鉴权 ----
    async def connect(self) -> None:
        headers = {
            "X-Api-App-ID": self._cfg.app_id,
            "X-Api-Access-Key": self._cfg.access_token,
            "X-Api-Resource-Id": self._cfg.resource_id,
            "X-Api-App-Key": self._cfg.app_key,
            "X-Api-Connect-Id": self._connect_id,
        }
        self._ws = await websockets.connect(
            self._cfg.endpoint,
            extra_headers=headers,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
        await self._send_json(ClientEvent.START_CONNECTION, {})
        resp = proto.decode(await self._ws.recv())
        if resp.event_id != ServerEvent.CONNECTION_STARTED:
            raise RuntimeError(f"建连失败：收到事件 {resp.event_id}，payload={resp.payload!r}")
        logger.info("火山连接已建立 connect_id=%s", self._connect_id)

    # ---- 会话生命周期 ----
    async def start_session(
        self,
        system_prompt: str = "",
        dialog_context: list | None = None,
        speaker: str | None = None,
    ) -> None:
        self._session_id = str(uuid.uuid4()).replace("-", "")
        dialog: dict = {
            "extra": {"input_mod": "keep_alive"},  # 静音时保活，防音频流超时
        }
        if self._cfg.model_version.upper().startswith("O"):
            dialog["bot_name"] = "小陪"
            if system_prompt:
                dialog["system_role"] = system_prompt
            dialog["speaking_style"] = "你说话温和、亲切、有耐心，像家里晚辈陪长辈聊天，语速放慢。"
        if dialog_context:
            dialog["dialog_context"] = dialog_context

        # 克隆音色：传入则覆盖默认音色（active 角色的 speaker_id），否则用配置默认音色。
        active_speaker = (speaker or self._cfg.speaker)
        payload = {
            "asr": {"end_smooth_window_ms": 1000},  # 老年端静音判停 800-1200ms
            "tts": {
                "speaker": active_speaker,
                "audio_config": {"channel": 1, "format": "pcm_s16le", "sample_rate": 24000},
            },
            "dialog": dialog,
        }
        await self._send_json(ClientEvent.START_SESSION, payload)
        resp = proto.decode(await self._ws.recv())
        if resp.event_id != ServerEvent.SESSION_STARTED:
            raise RuntimeError(f"会话启动失败：事件 {resp.event_id}，payload={resp.payload!r}")
        logger.info("会话已启动 session_id=%s", self._session_id)

    async def send_audio(self, pcm: bytes) -> None:
        """上行一帧音频（PCM 16k/mono/int16/LE）。"""
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        frame = proto.encode(
            event_id=ClientEvent.TASK_REQUEST,
            payload=pcm,
            message_type=proto.MSG_AUDIO_ONLY_CLIENT,
            serialization=proto.SER_RAW,
            session_id=self._session_id,
        )
        await self._ws.send(frame)

    async def finish_session(self) -> None:
        if self._ws is None:
            return
        await self._send_json(ClientEvent.FINISH_SESSION, {})

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._send_json(ClientEvent.FINISH_CONNECTION, {})
        finally:
            await self._ws.close()
            self._ws = None

    # ---- 接收循环 ----
    async def receive_loop(self) -> None:
        """持续接收服务端回包并分发到回调。直到会话结束或连接关闭。"""
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        async for raw in self._ws:
            resp = proto.decode(raw)
            if resp.is_error:
                logger.error("火山错误帧 code=%s payload=%s", resp.error_code, resp.payload)
                if self.on_event:
                    await self.on_event(ServerEvent.SESSION_FAILED, {"error": resp.payload.decode("utf-8", "ignore")})
                continue

            if resp.is_audio:
                if self.on_audio and resp.payload:
                    await self.on_audio(resp.payload)
                continue

            await self._dispatch_text_frame(resp)
            if resp.event_id == ServerEvent.SESSION_FINISHED:
                logger.info("会话结束 session_id=%s", self._session_id)
                break

    async def _dispatch_text_frame(self, resp: proto.ServerResponse) -> None:
        data: dict = {}
        if resp.payload:
            try:
                data = json.loads(resp.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = {}
        if resp.event_id == ServerEvent.ASR_RESPONSE and self.on_text:
            await self.on_text("user", _extract_text(data))
        elif resp.event_id == ServerEvent.CHAT_RESPONSE and self.on_text:
            await self.on_text("assistant", _extract_text(data))
        if self.on_event and resp.event_id is not None:
            await self.on_event(resp.event_id, data)

    async def _send_json(self, event_id: int, payload: dict) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket 未连接")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        frame = proto.encode(
            event_id=event_id,
            payload=body,
            message_type=proto.MSG_FULL_CLIENT,
            serialization=proto.SER_JSON,
            session_id=self._session_id,
            connect_id=self._connect_id,
        )
        await self._ws.send(frame)


def _extract_text(data: dict) -> str:
    for key in ("text", "content", "result"):
        if isinstance(data.get(key), str):
            return data[key]
    return ""
