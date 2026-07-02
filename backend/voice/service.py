"""Application service for authorized voice samples and clone profiles."""
from __future__ import annotations

from typing import Optional

from ..character import CharacterStore
from .providers import MockVoiceCloneProvider, VoiceCloneProvider, VoicePreviewResult
from .store import VoiceStore


class VoiceService:
    def __init__(
        self,
        store: VoiceStore,
        character_store: CharacterStore,
        provider: Optional[VoiceCloneProvider] = None,
    ) -> None:
        self._store = store
        self._character_store = character_store
        self._provider = provider or MockVoiceCloneProvider()

    async def save_sample(
        self,
        *,
        elder_id: str,
        character_id: int,
        filename: str,
        audio_format: str,
        audio_bytes: bytes,
        consent: bool,
        consent_text: str,
    ) -> dict:
        char = await self._require_character(elder_id, character_id)
        if not consent:
            raise ValueError("需要先确认声音授权")
        sample = await self._store.create_sample(
            elder_id=elder_id,
            character_id=character_id,
            filename=filename,
            audio_format=audio_format,
            audio_bytes=audio_bytes,
            consent=consent,
            consent_text=consent_text,
        )
        return {"sample": _public_sample(sample), "character": char}

    async def clone_latest(
        self,
        *,
        elder_id: str,
        character_id: int,
        sample_id: Optional[int] = None,
        text: str = "",
    ) -> dict:
        char = await self._require_character(elder_id, character_id)
        sample = (
            await self._store.get_sample(elder_id, character_id, sample_id)
            if sample_id is not None
            else await self._store.latest_sample(elder_id, character_id)
        )
        if not sample:
            raise ValueError("请先录音或上传一段授权音频")
        if not sample.get("consent"):
            raise ValueError("该声音样本缺少授权确认")
        audio_bytes = self._store.read_sample_bytes(sample)
        result = await self._provider.clone(
            elder_id=elder_id,
            character_id=character_id,
            sample_id=sample["id"],
            audio_bytes=audio_bytes,
            audio_format=sample["audio_format"],
            text=text,
        )
        profile = await self._store.upsert_profile(
            elder_id=elder_id,
            character_id=character_id,
            sample_id=sample["id"],
            provider=result.provider,
            provider_voice_id=result.provider_voice_id,
            status=result.status,
            detail=result.detail,
            preview_text=text,
        )
        await self._character_store.update_voice(
            elder_id,
            character_id,
            speaker_id=result.provider_voice_id,
            status=result.status,
        )
        character = await self._character_store.get(elder_id, character_id)
        return {"profile": _public_profile(profile), "character": character or char}

    async def refresh_status(self, elder_id: str, character_id: int) -> dict:
        char = await self._require_character(elder_id, character_id)
        profile = await self._store.latest_profile(elder_id, character_id)
        if not profile:
            return {"profile": None, "character": char}
        result = await self._provider.status(profile["provider_voice_id"])
        profile = await self._store.upsert_profile(
            elder_id=elder_id,
            character_id=character_id,
            sample_id=profile["sample_id"],
            provider=result.provider,
            provider_voice_id=result.provider_voice_id,
            status=result.status,
            detail=result.detail,
            preview_text=profile.get("preview_text", ""),
        )
        if result.status != char["voice_status"] or result.provider_voice_id != char["speaker_id"]:
            await self._character_store.update_voice(
                elder_id,
                character_id,
                speaker_id=result.provider_voice_id,
                status=result.status,
            )
        if result.status == "ready":
            await self._store.delete_samples(elder_id, character_id)
        character = await self._character_store.get(elder_id, character_id)
        return {"profile": _public_profile(profile), "character": character or char}

    async def preview(self, elder_id: str, character_id: int, text: str) -> VoicePreviewResult:
        await self.refresh_status(elder_id, character_id)
        profile = await self._store.latest_profile(elder_id, character_id)
        if not profile or profile["status"] != "ready":
            raise ValueError("声音还没准备好，请稍后刷新状态")
        return await self._provider.preview(profile["provider_voice_id"], text)

    async def delete_voice_data(self, elder_id: str, character_id: int) -> dict:
        await self._require_character(elder_id, character_id)
        files_deleted = await self._store.delete_samples(elder_id, character_id)
        await self._store.delete_profiles(elder_id, character_id)
        await self._character_store.update_voice(
            elder_id,
            character_id,
            speaker_id="",
            status="none",
        )
        character = await self._character_store.get(elder_id, character_id)
        return {"files_deleted": files_deleted, "character": character}

    async def cleanup_expired_samples(self) -> dict:
        return {"files_deleted": await self._store.cleanup_expired_samples()}

    async def _require_character(self, elder_id: str, character_id: int) -> dict:
        char = await self._character_store.get(elder_id, character_id)
        if char is None:
            raise ValueError("角色不存在")
        return char


def _public_sample(sample: dict) -> dict:
    return {
        "id": sample["id"],
        "filename": sample["filename"],
        "audio_format": sample["audio_format"],
        "bytes_size": sample["bytes_size"],
        "created_at": sample["created_at"],
    }


def _public_profile(profile: dict) -> dict:
    return {
        "id": profile["id"],
        "sample_id": profile["sample_id"],
        "provider": profile["provider"],
        "provider_voice_id": profile["provider_voice_id"],
        "status": profile["status"],
        "detail": profile["detail"],
        "updated_at": profile["updated_at"],
    }
