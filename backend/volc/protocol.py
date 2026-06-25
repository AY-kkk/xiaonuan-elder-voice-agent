"""火山端到端实时语音 WebSocket v3 二进制协议编解码。

帧结构：Header(4B) + Optional(event/connect_id/session_id/code) + PayloadSize(4B) + Payload
- 所有整数字段为大端序（音频 PCM 负载本身为小端 int16，由上层保证）。
- Header:
  Byte0 = (协议版本<<4)|Header长度  → 0x11
  Byte1 = (MessageType<<4)|Flags
  Byte2 = (序列化<<4)|压缩
  Byte3 = 保留 0x00

依据官方文档「2.2 WebSocket二进制协议」实现。
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import ClientEvent, ServerEvent

PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001  # 单位为 4 字节，即 4B

# Message Type（Byte1 高 4 位）
MSG_FULL_CLIENT = 0b0001
MSG_AUDIO_ONLY_CLIENT = 0b0010
MSG_FULL_SERVER = 0b1001
MSG_AUDIO_ONLY_SERVER = 0b1011
MSG_ERROR = 0b1111

# Flags（Byte1 低 4 位）
FLAG_WITH_EVENT = 0b0100

# 序列化（Byte2 高 4 位）
SER_RAW = 0b0000
SER_JSON = 0b0001

# 压缩（Byte2 低 4 位）
COMPRESS_NONE = 0b0000

# Connect 类事件携带 connect_id，其余 Session 类事件携带 session_id
_CONNECT_EVENTS = {
    ClientEvent.START_CONNECTION,
    ClientEvent.FINISH_CONNECTION,
    ServerEvent.CONNECTION_STARTED,
    ServerEvent.CONNECTION_FAILED,
    ServerEvent.CONNECTION_FINISHED,
}


def _is_connect_event(event_id: int) -> bool:
    return event_id in _CONNECT_EVENTS


def _build_header(message_type: int, serialization: int) -> bytes:
    return bytes(
        [
            (PROTOCOL_VERSION << 4) | HEADER_SIZE,
            (message_type << 4) | FLAG_WITH_EVENT,
            (serialization << 4) | COMPRESS_NONE,
            0x00,
        ]
    )


def encode(
    *,
    event_id: int,
    payload: bytes,
    message_type: int,
    serialization: int,
    session_id: str = "",
    connect_id: str = "",
) -> bytes:
    """组装一个客户端请求帧。"""
    frame = bytearray(_build_header(message_type, serialization))
    frame.extend(event_id.to_bytes(4, "big"))

    if _is_connect_event(event_id):
        cid = connect_id.encode("utf-8")
        frame.extend(len(cid).to_bytes(4, "big"))
        frame.extend(cid)
    else:
        sid = session_id.encode("utf-8")
        frame.extend(len(sid).to_bytes(4, "big"))
        frame.extend(sid)

    frame.extend(len(payload).to_bytes(4, "big"))
    frame.extend(payload)
    return bytes(frame)


@dataclass
class ServerResponse:
    """解析后的服务端帧。"""

    message_type: int
    event_id: int | None
    session_id: str
    connect_id: str
    payload: bytes
    error_code: int | None = None

    @property
    def is_audio(self) -> bool:
        return self.message_type == MSG_AUDIO_ONLY_SERVER

    @property
    def is_error(self) -> bool:
        return self.message_type == MSG_ERROR


def decode(data: bytes) -> ServerResponse:
    """解析服务端返回帧。"""
    if len(data) < 4:
        raise ValueError(f"帧长度不足，无法解析 header：{len(data)} 字节")

    header_size = data[0] & 0x0F
    message_type = data[1] >> 4
    flags = data[1] & 0x0F

    cursor = header_size * 4
    event_id: int | None = None
    session_id = ""
    connect_id = ""
    error_code: int | None = None

    if message_type == MSG_ERROR:
        error_code = int.from_bytes(data[cursor : cursor + 4], "big")
        cursor += 4
    elif flags & FLAG_WITH_EVENT:
        event_id = int.from_bytes(data[cursor : cursor + 4], "big")
        cursor += 4
        if _is_connect_event(event_id):
            size = int.from_bytes(data[cursor : cursor + 4], "big")
            cursor += 4
            connect_id = data[cursor : cursor + size].decode("utf-8", "ignore")
            cursor += size
        else:
            size = int.from_bytes(data[cursor : cursor + 4], "big")
            cursor += 4
            session_id = data[cursor : cursor + size].decode("utf-8", "ignore")
            cursor += size

    payload_size = int.from_bytes(data[cursor : cursor + 4], "big")
    cursor += 4
    payload = data[cursor : cursor + payload_size]

    return ServerResponse(
        message_type=message_type,
        event_id=event_id,
        session_id=session_id,
        connect_id=connect_id,
        payload=payload,
        error_code=error_code,
    )
