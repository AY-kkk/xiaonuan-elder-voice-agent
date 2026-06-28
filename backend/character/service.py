"""角色编排服务：把「数据层 + 声音复刻 + 人格蒸馏」编排成一个对外能力。

一个角色 = 声音（speaker_id）+ 灵魂（persona_prompt），两条蒸馏链路独立推进：
  - 声音：上传音频 -> 火山训练 speaker_id -> 轮询就绪
  - 灵魂：上传语料 -> 方舟蒸馏人格 -> 立即就绪

会话侧只需两个只读取数：
  - active_speaker(elder_id)        当前启用角色的 speaker_id（就绪才返回，否则 None）
  - active_persona(elder_id)        当前启用角色的人格提示词（就绪才返回，否则 None）
二者均「未启用/未就绪 -> None」，由调用方回落默认音色/基础人设（绝不阻塞通话）。
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import ArkConfig, VolcConfig
from .persona import PersonaService
from .store import CharacterStore
from .voice_clone import VoiceCloneClient

logger = logging.getLogger(__name__)


class CharacterService:
    def __init__(
        self,
        store: CharacterStore,
        volc_cfg: VolcConfig,
        ark_cfg: ArkConfig,
        usage_store=None,
    ) -> None:
        self._store = store
        self._voice = VoiceCloneClient(volc_cfg)
        self._persona = PersonaService(ark_cfg, usage_store=usage_store)

    # ---- 角色生命周期 ----
    async def create(self, elder_id: str, name: str, relation: str = "") -> dict:
        return await self._store.create(elder_id, name, relation)

    async def list(self, elder_id: str) -> list:
        return await self._store.list(elder_id)

    async def get(self, elder_id: str, char_id: int) -> Optional[dict]:
        return await self._store.get(elder_id, char_id)

    async def delete(self, elder_id: str, char_id: int) -> None:
        await self._store.delete(elder_id, char_id)

    # ---- 声音克隆链路 ----
    async def train_voice(
        self,
        elder_id: str,
        char_id: int,
        speaker_id: str,
        audio_bytes: bytes,
        audio_format: str,
        *,
        text: str = "",
    ) -> dict:
        """提交音频训练音色，并把状态写回角色。返回最新角色快照。"""
        char = await self._store.get(elder_id, char_id)
        if char is None:
            raise ValueError("角色不存在")
        result = await self._voice.train(
            speaker_id, audio_bytes, audio_format, text=text
        )
        await self._store.update_voice(
            elder_id, char_id, speaker_id=result.speaker_id or speaker_id, status=result.status
        )
        snapshot = await self._store.get(elder_id, char_id)
        snapshot["voice_detail"] = result.detail
        return snapshot

    async def refresh_voice_status(self, elder_id: str, char_id: int) -> dict:
        """轮询火山训练状态并同步落库。返回最新角色快照。"""
        char = await self._store.get(elder_id, char_id)
        if char is None:
            raise ValueError("角色不存在")
        if not char["speaker_id"]:
            return char
        result = await self._voice.status(char["speaker_id"])
        # 仅在状态变化时落库，减少无谓写入
        if result.status != char["voice_status"]:
            await self._store.update_voice(
                elder_id, char_id, speaker_id=char["speaker_id"], status=result.status
            )
        return await self._store.get(elder_id, char_id)

    # ---- 人格蒸馏链路 ----
    async def distill_persona(
        self, elder_id: str, char_id: int, corpus: str
    ) -> dict:
        """把角色语料蒸馏成人格提示词并落库（原始语料用完即弃，不入库）。"""
        char = await self._store.get(elder_id, char_id)
        if char is None:
            raise ValueError("角色不存在")
        prompt = await self._persona.distill(
            elder_id, char["name"], char["relation"], corpus
        )
        await self._store.update_persona(elder_id, char_id, prompt=prompt, status="ready")
        return await self._store.get(elder_id, char_id)

    # ---- 激活与会话注入 ----
    async def activate(self, elder_id: str, char_id: int) -> bool:
        return await self._store.set_active(elder_id, char_id)

    async def deactivate(self, elder_id: str) -> None:
        await self._store.deactivate_all(elder_id)

    async def active_speaker(self, elder_id: str) -> Optional[str]:
        """当前启用角色的已就绪音色；否则 None（回落默认音色）。"""
        char = await self._store.get_active(elder_id)
        if char and char["voice_status"] == "ready" and char["speaker_id"]:
            return char["speaker_id"]
        return None

    async def active_persona(self, elder_id: str) -> Optional[str]:
        """当前启用角色的已就绪人格提示词；否则 None（回落基础人设）。"""
        char = await self._store.get_active(elder_id)
        if char and char["persona_status"] == "ready" and char["persona_prompt"]:
            return char["persona_prompt"]
        return None
