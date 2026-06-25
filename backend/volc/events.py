"""火山豆包端到端实时语音事件 ID 常量。

依据官方 WS 协议文档（doc 6561/1594356）的「实时对话事件」章节整理。
事件分为客户端发送与服务端返回两类。
"""
from __future__ import annotations

from enum import IntEnum


class ClientEvent(IntEnum):
    """客户端 -> 服务端事件。"""

    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    START_SESSION = 100
    FINISH_SESSION = 102
    TASK_REQUEST = 200  # 上传音频帧（也用于文本输入）


class ServerEvent(IntEnum):
    """服务端 -> 客户端事件。"""

    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    SESSION_STARTED = 150
    SESSION_FAILED = 153
    SESSION_FINISHED = 152
    TTS_SENTENCE_START = 350
    TTS_SENTENCE_END = 351
    TTS_RESPONSE = 352  # 音频数据帧
    TTS_ENDED = 359
    ASR_INFO = 450  # 检测到用户首字 -> 可用于 barge-in 打断判定
    ASR_RESPONSE = 451  # 用户说话文本（含中间结果）
    ASR_ENDED = 459
    CHAT_RESPONSE = 550  # 模型回复文本
    CHAT_ENDED = 559
