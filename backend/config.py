"""全局配置：集中读取环境变量，供各模块复用。

凭证只在后端持有，客户端永不接触（隐私/安全硬边界）。

两套独立鉴权体系（火山产品边界，不可混用）：
  - VOLC_*（openspeech 语音控制台 AppID + Access Token）：豆包端到端实时
    语音（Seeduplex），负责"听+说"全双工发声链路。
  - ARK_API_KEY（方舟大模型平台）：纯文本 LLM，仅用于 L3 记忆蒸馏 / L4 信号。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def require(name: str) -> str:
    """惰性校验：仅在真正使用某引擎时调用，避免未用到的凭证导致启动失败。"""
    value = _get(name)
    if not value:
        raise RuntimeError(
            f"缺少必需的环境变量 {name}，请在项目根目录 .env 中配置（参考 .env.example）"
        )
    return value


@dataclass(frozen=True)
class VolcConfig:
    """豆包端到端实时语音（Seeduplex）连接配置。凭证惰性读取。"""

    endpoint: str = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
    resource_id: str = "volc.speech.dialog"
    app_key: str = "PlgvMymc7f3tQnJ6"  # 官方固定值
    model_version: str = field(default_factory=lambda: _get("VOLC_MODEL", "O"))
    speaker: str = field(default_factory=lambda: _get("VOLC_SPEAKER", "zh_female_vv_jupiter_bigtts"))

    @property
    def app_id(self) -> str:
        return require("VOLC_APP_ID")

    @property
    def access_token(self) -> str:
        return require("VOLC_ACCESS_TOKEN")


@dataclass(frozen=True)
class ArkConfig:
    """火山方舟文本链路配置：仅用于 L3 记忆蒸馏 / L4 信号生成。

    方舟 api_key 鉴权体系与 openspeech 语音服务不通用，不参与发声。
    """

    base_url: str = field(default_factory=lambda: _get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    text_model: str = field(default_factory=lambda: _get("ARK_TEXT_MODEL", "doubao-seed-1-6-flash"))

    @property
    def api_key(self) -> str:
        return require("ARK_API_KEY")

    @property
    def enabled(self) -> bool:
        """无 api_key 时降级为规则提炼，不阻塞主链路。"""
        return bool(_get("ARK_API_KEY"))


@dataclass(frozen=True)
class AppConfig:
    host: str = field(default_factory=lambda: _get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_get("PORT", "8000")))
    engine: str = field(default_factory=lambda: _get("VOICE_ENGINE", "seeduplex").lower())
    db_path: str = field(default_factory=lambda: _get("DB_PATH") or str(_PROJECT_ROOT / "backend" / "memory.db"))
    volc: VolcConfig = field(default_factory=VolcConfig)
    ark: ArkConfig = field(default_factory=ArkConfig)


def load_config() -> AppConfig:
    return AppConfig()
