"""语音引擎工厂：按配置创建对应引擎实例。"""
from __future__ import annotations

from ..config import AppConfig
from .base import VoiceEngine


def create_engine(cfg: AppConfig) -> VoiceEngine:
    """创建发声引擎。

    发声唯一走豆包端到端实时语音（Seeduplex），这是 PRD 达标路线
    （原生全双工、可打断、亚秒延迟）。方舟 api_key 不参与发声，
    仅用于 L3 记忆蒸馏 / L4 信号的纯文本 LLM 调用（鉴权体系不同）。
    """
    if cfg.engine == "seeduplex":
        from .seeduplex import SeeduplexEngine

        return SeeduplexEngine(cfg.volc)
    if cfg.engine == "fake":
        # 无凭证联调：脚本化模拟火山回包，用于端到端验证 L1/L3/L4。
        from .fake import FakeEngine

        return FakeEngine()
    raise ValueError(f"未知 VOICE_ENGINE={cfg.engine!r}，可选 seeduplex（达标路线）/ fake（无凭证联调）")
