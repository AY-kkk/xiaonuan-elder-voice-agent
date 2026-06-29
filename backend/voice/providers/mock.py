"""Local voice-clone provider used for product wiring and tests.

It does not clone a real voice. It gives deterministic IDs, simulates provider
state, and returns a tiny WAV tone so the front end can exercise the full flow
without credentials or GPU services.
"""
from __future__ import annotations

import hashlib
import io
import math
import struct
import time
import wave

from .base import VoiceCloneProvider, VoiceCloneResult, VoicePreviewResult

_READY_AFTER_SECONDS = 3.0


class MockVoiceCloneProvider(VoiceCloneProvider):
    name = "mock"

    def __init__(self) -> None:
        self._started_at: dict[str, float] = {}

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
        digest = hashlib.sha1(
            b"|".join(
                [
                    elder_id.encode("utf-8"),
                    str(character_id).encode("ascii"),
                    str(sample_id).encode("ascii"),
                    audio_bytes[:2048],
                    audio_format.encode("ascii", errors="ignore"),
                ]
            )
        ).hexdigest()[:16]
        voice_id = f"mock_voice_{digest}"
        self._started_at[voice_id] = time.monotonic()
        return VoiceCloneResult(
            status="training",
            provider=self.name,
            provider_voice_id=voice_id,
            detail="mock provider: replace with GPT-SoVITS/CosyVoice/OpenVoice/Volc in production",
        )

    async def status(self, provider_voice_id: str) -> VoiceCloneResult:
        started = self._started_at.get(provider_voice_id)
        if started is None:
            return VoiceCloneResult("ready", self.name, provider_voice_id, detail="mock")
        status = "ready" if time.monotonic() - started >= _READY_AFTER_SECONDS else "training"
        return VoiceCloneResult(status, self.name, provider_voice_id, detail="mock")

    async def preview(self, provider_voice_id: str, text: str) -> VoicePreviewResult:
        frequency = 420 + (sum(provider_voice_id.encode("utf-8")) % 160)
        return VoicePreviewResult(_tone_wav(frequency=frequency), filename=f"{provider_voice_id}.wav")


def _tone_wav(*, frequency: int, seconds: float = 0.9, sample_rate: int = 16000) -> bytes:
    frames = int(seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for idx in range(frames):
            envelope = min(1.0, idx / 800) * min(1.0, (frames - idx) / 800)
            sample = int(9000 * envelope * math.sin(2 * math.pi * frequency * idx / sample_rate))
            wav.writeframesraw(struct.pack("<h", sample))
    return buf.getvalue()
