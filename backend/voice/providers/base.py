"""Provider contract for voice cloning backends.

The app-facing API is intentionally provider-neutral. GPT-SoVITS, CosyVoice,
OpenVoice, Volc, or a hosted internal service can all implement this contract
without changing the parent/elder product flows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VoiceCloneResult:
    status: str
    provider: str
    provider_voice_id: str
    detail: str = ""


@dataclass(frozen=True)
class VoicePreviewResult:
    audio_bytes: bytes
    content_type: str = "audio/wav"
    filename: str = "voice-preview.wav"


class VoiceCloneProvider(Protocol):
    name: str

    async def clone(
        self,
        *,
        elder_id: str,
        character_id: int,
        sample_id: int,
        audio_bytes: bytes,
        audio_format: str,
        text: str = "",
    ) -> VoiceCloneResult:
        """Start or finish a clone task for the supplied authorized sample."""

    async def status(self, provider_voice_id: str) -> VoiceCloneResult:
        """Return the current provider-side clone status."""

    async def preview(self, provider_voice_id: str, text: str) -> VoicePreviewResult:
        """Generate a short preview audio clip for the cloned voice."""
