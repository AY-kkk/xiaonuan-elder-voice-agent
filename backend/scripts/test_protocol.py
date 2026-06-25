"""协议编解码自测：不依赖网络/凭证，验证帧组装与解析的正确性。

运行：python -m backend.scripts.test_protocol
"""
from __future__ import annotations

import json

from ..volc import protocol as proto
from ..volc.events import ClientEvent, ServerEvent


def _check(name: str, cond: bool) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        raise AssertionError(name)


def test_header_bytes() -> None:
    """通过编码一帧并检查 header 4 字节是否符合规范。"""
    frame = proto.encode(
        event_id=ClientEvent.START_CONNECTION,
        payload=b"{}",
        message_type=proto.MSG_FULL_CLIENT,
        serialization=proto.SER_JSON,
        connect_id="cid-123",
    )
    _check("Byte0 == 0x11", frame[0] == 0x11)
    _check("Byte1 高4位是 FullClient", (frame[1] >> 4) == proto.MSG_FULL_CLIENT)
    _check("Byte1 低4位携带 event flag", (frame[1] & 0x0F) == proto.FLAG_WITH_EVENT)
    _check("Byte2 高4位是 JSON", (frame[2] >> 4) == proto.SER_JSON)
    _check("Byte3 保留为 0", frame[3] == 0x00)


def test_connect_event_carries_connect_id() -> None:
    frame = proto.encode(
        event_id=ClientEvent.START_CONNECTION,
        payload=b"{}",
        message_type=proto.MSG_FULL_CLIENT,
        serialization=proto.SER_JSON,
        connect_id="abc",
    )
    # header(4) + event(4) + connect_id_size(4) + "abc"(3) + payload_size(4) + "{}"(2)
    _check("Connect 帧总长正确", len(frame) == 4 + 4 + 4 + 3 + 4 + 2)
    event_id = int.from_bytes(frame[4:8], "big")
    _check("event_id == StartConnection", event_id == ClientEvent.START_CONNECTION)
    cid_size = int.from_bytes(frame[8:12], "big")
    _check("connect_id size == 3", cid_size == 3)
    _check("connect_id == abc", frame[12:15] == b"abc")


def test_audio_frame_uses_session_id() -> None:
    pcm = b"\x01\x02" * 320  # 640B 一帧
    frame = proto.encode(
        event_id=ClientEvent.TASK_REQUEST,
        payload=pcm,
        message_type=proto.MSG_AUDIO_ONLY_CLIENT,
        serialization=proto.SER_RAW,
        session_id="sess-xyz",
    )
    _check("Byte1 高4位是 AudioOnly", (frame[1] >> 4) == proto.MSG_AUDIO_ONLY_CLIENT)
    _check("Byte2 高4位是 Raw", (frame[2] >> 4) == proto.SER_RAW)


def test_decode_server_text_frame() -> None:
    """构造一个服务端 Chat 文本帧并解码。"""
    body = json.dumps({"text": "你好呀"}, ensure_ascii=False).encode("utf-8")
    sid = b"sess-xyz"
    raw = bytearray(
        [
            (proto.PROTOCOL_VERSION << 4) | proto.HEADER_SIZE,
            (proto.MSG_FULL_SERVER << 4) | proto.FLAG_WITH_EVENT,
            (proto.SER_JSON << 4) | proto.COMPRESS_NONE,
            0x00,
        ]
    )
    raw.extend(int(ServerEvent.CHAT_RESPONSE).to_bytes(4, "big"))
    raw.extend(len(sid).to_bytes(4, "big"))
    raw.extend(sid)
    raw.extend(len(body).to_bytes(4, "big"))
    raw.extend(body)

    resp = proto.decode(bytes(raw))
    _check("解码事件 == ChatResponse", resp.event_id == ServerEvent.CHAT_RESPONSE)
    _check("解码 session_id 正确", resp.session_id == "sess-xyz")
    _check("解码文本正确", json.loads(resp.payload)["text"] == "你好呀")


def test_decode_server_audio_frame() -> None:
    pcm = b"\xaa\xbb" * 100
    sid = b"s1"
    raw = bytearray(
        [
            (proto.PROTOCOL_VERSION << 4) | proto.HEADER_SIZE,
            (proto.MSG_AUDIO_ONLY_SERVER << 4) | proto.FLAG_WITH_EVENT,
            (proto.SER_RAW << 4) | proto.COMPRESS_NONE,
            0x00,
        ]
    )
    raw.extend(int(ServerEvent.TTS_RESPONSE).to_bytes(4, "big"))
    raw.extend(len(sid).to_bytes(4, "big"))
    raw.extend(sid)
    raw.extend(len(pcm).to_bytes(4, "big"))
    raw.extend(pcm)

    resp = proto.decode(bytes(raw))
    _check("解码识别为音频帧", resp.is_audio)
    _check("音频负载完整", resp.payload == pcm)


def main() -> None:
    test_header_bytes()
    test_connect_event_carries_connect_id()
    test_audio_frame_uses_session_id()
    test_decode_server_text_frame()
    test_decode_server_audio_frame()
    print("\n所有协议自测通过 ✅")


if __name__ == "__main__":
    main()
