"""火山「声音复刻」客户端：把一段音频样本训练成可用于发声的音色（speaker_id）。

落在后端的理由（第一性原理）：
  - 本项目是瘦客户端 + 云端语音架构，凭证只在后端持有（隐私/安全硬边界），
    端侧既不能持 VOLC 凭证，也跑不动实时声学模型 —— 故声音克隆必须走云端 API。
  - 训练得到的 speaker_id 正是发声链路 Seeduplex 合成时 tts.speaker 字段所需，
    天然闭环：录音上传 → 训练 speaker_id → 通话时注入。

鉴权复用 VOLC_*（与 Seeduplex 同体系）：
  Authorization: Bearer;{access_token}（注意是分号分隔，火山特有写法）

两个接口（openspeech.bytedance.com）：
  - POST /api/v1/mega_tts/audio/upload  提交音频训练
  - POST /api/v1/mega_tts/status        查询训练状态

speaker_id（S_xxx）需先在火山控制台购买声音复刻资源包获取，API 不自动生成。

降级：未配置 VOLC 凭证时进入 mock 模式（直接判定就绪），保证 fake 链路与
本地联调零凭证可跑通整条「角色再生」端口，凭证到位即接真实克隆。
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import VolcConfig

logger = logging.getLogger(__name__)

_HOST = "https://openspeech.bytedance.com"
_UPLOAD_PATH = "/api/v1/mega_tts/audio/upload"
_STATUS_PATH = "/api/v1/mega_tts/status"

# 训练/查询接口的 Resource-Id（与双向流式合成的 seed-icl-* 不同，是训练域资源）
_RESOURCE_ID = "volc.megatts.voiceclone"

# mock 模式下模拟训练耗时（秒）：让前端轮询能看到 training→ready 的真实流转。
_MOCK_TRAIN_SECONDS = 6.0

# 火山 status 接口返回的训练态映射到本项目内部状态
_STATUS_MAP = {
    0: "none",      # 未训练
    1: "training",  # 训练中
    2: "ready",     # 训练完成，可用
    3: "failed",    # 训练失败
    4: "failed",    # 异常态统一归为 failed
}


@dataclass(frozen=True)
class CloneResult:
    status: str            # none/training/ready/failed
    speaker_id: str = ""
    detail: str = ""       # 失败/异常时的可读说明


class VoiceCloneClient:
    """声音复刻 API 封装。无凭证时 mock，绝不抛断主流程。"""

    def __init__(self, cfg: VolcConfig) -> None:
        self._cfg = cfg
        # mock 模式下记录各 speaker 的「模拟训练开始时间」，用于演示 training→ready 流转。
        # 仅内存、进程级；真实凭证下不使用此字段（状态以火山接口为准）。
        self._mock_train_started: dict[str, float] = {}

    @property
    def available(self) -> bool:
        """凭证是否就绪。读 property 触发惰性校验，缺失走 mock。"""
        try:
            return bool(self._cfg.app_id and self._cfg.access_token)
        except RuntimeError:
            return False

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{self._cfg.access_token}",
            "Resource-Id": _RESOURCE_ID,
        }

    async def train(
        self,
        speaker_id: str,
        audio_bytes: bytes,
        audio_format: str,
        *,
        text: str = "",
        timeout: float = 60.0,
    ) -> CloneResult:
        """提交音频训练音色。返回提交后的状态（通常为 training）。

        speaker_id：控制台购买资源包获取的音色代号（S_xxx）。
        audio_bytes：原始音频字节（wav/mp3/m4a/aac/ogg；pcm 须 24k 单声道）。
        """
        speaker_id = (speaker_id or "").strip()
        if not speaker_id:
            return CloneResult("failed", detail="缺少 speaker_id（请先在火山控制台购买资源包获取）")
        if not audio_bytes:
            return CloneResult("failed", detail="音频为空")
        if not self.available:
            # mock 模式：返回 training 并记录起始时间，由后续 status() 轮询在
            # _MOCK_TRAIN_SECONDS 后转 ready，从而演示真实的「克隆中→已就绪」流转。
            logger.info("VOLC 凭证缺失，声音复刻进入 mock 模式（模拟训练流转）")
            self._mock_train_started[speaker_id] = time.monotonic()
            return CloneResult("training", speaker_id=speaker_id, detail="mock")

        audio = {
            "audio_bytes": base64.b64encode(audio_bytes).decode("ascii"),
            "audio_format": audio_format,
        }
        if text:
            audio["text"] = text
        payload = {
            "appid": self._cfg.app_id,
            "speaker_id": speaker_id,
            "audios": [audio],
            "source": 2,
            "language": 0,        # 中文（老年陪伴主语种）
            "model_type": 4,      # ICL2.0：情感表现力强、秒级克隆、支持双向流式
            "extra_params": json.dumps({"enable_audio_denoise": True}),
        }
        try:
            async with httpx.AsyncClient(base_url=_HOST, timeout=timeout) as client:
                resp = await client.post(_UPLOAD_PATH, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # 网络/鉴权/格式错误统一收敛为 failed，不抛断
            logger.warning("声音复刻提交失败：%s", exc)
            return CloneResult("failed", speaker_id=speaker_id, detail=str(exc)[:200])

        code = _resp_code(data)
        if code not in (0, 1000):  # 火山约定 0/1000 表示提交成功
            return CloneResult("failed", speaker_id=speaker_id, detail=_resp_message(data))
        return CloneResult("training", speaker_id=speaker_id)

    async def status(self, speaker_id: str, *, timeout: float = 30.0) -> CloneResult:
        """查询音色训练状态。"""
        speaker_id = (speaker_id or "").strip()
        if not speaker_id:
            return CloneResult("none")
        if not self.available:
            # mock：未提交过训练→none；训练中且未到模拟时长→training；到时→ready。
            started = self._mock_train_started.get(speaker_id)
            if started is None:
                return CloneResult("none", speaker_id=speaker_id, detail="mock")
            elapsed = time.monotonic() - started
            status = "ready" if elapsed >= _MOCK_TRAIN_SECONDS else "training"
            return CloneResult(status, speaker_id=speaker_id, detail="mock")

        payload = {"appid": self._cfg.app_id, "speaker_id": speaker_id}
        try:
            async with httpx.AsyncClient(base_url=_HOST, timeout=timeout) as client:
                resp = await client.post(_STATUS_PATH, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("声音复刻状态查询失败：%s", exc)
            return CloneResult("failed", speaker_id=speaker_id, detail=str(exc)[:200])

        raw_status = data.get("status")
        mapped = _STATUS_MAP.get(raw_status, "training")
        return CloneResult(mapped, speaker_id=speaker_id)


def _resp_code(data: dict) -> int:
    for key in ("code", "BaseResp_StatusCode", "status_code"):
        val = data.get(key)
        if isinstance(val, int):
            return val
    return 0  # 缺字段时按成功处理（部分版本仅在失败时回 code）


def _resp_message(data: dict) -> str:
    for key in ("message", "Message", "msg"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val[:200]
    return "训练接口返回异常"
