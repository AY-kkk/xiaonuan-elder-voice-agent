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


def _default_voice_engine() -> str:
    configured = _get("VOICE_ENGINE")
    if configured:
        engine = configured.lower()
        if engine == "seeduplex" and not (_get("VOLC_APP_ID") and _get("VOLC_ACCESS_TOKEN")):
            return "fake"
        return engine
    if _get("VOLC_APP_ID") and _get("VOLC_ACCESS_TOKEN"):
        return "seeduplex"
    return "fake"


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
    # 未显式配置 VOICE_ENGINE 时：有火山语音凭证走真实 seeduplex；无凭证走 fake，
    # 保证本地演示和自动化测试选择身份后仍可完整交互。
    engine: str = field(default_factory=_default_voice_engine)
    db_path: str = field(default_factory=lambda: _get("DB_PATH") or str(_PROJECT_ROOT / "backend" / "memory.db"))
    # 跨域放行源（前后端分离部署时填，逗号分隔；留空=不额外放行，同源托管最安全）
    cors_origins: tuple = field(
        default_factory=lambda: tuple(
            o.strip() for o in _get("CORS_ALLOW_ORIGINS").split(",") if o.strip()
        )
    )
    # 方舟文本模型单价（元/百万 token），用于成本看板折算。默认按 doubao-seed-1-6-flash
    # 公开价估算；实际以方舟控制台账单为准。仅用于展示，不影响计费。
    ark_price_per_mtoken: float = field(
        default_factory=lambda: float(_get("ARK_PRICE_PER_MTOKEN", "0.8"))
    )
    volc: VolcConfig = field(default_factory=VolcConfig)
    ark: ArkConfig = field(default_factory=ArkConfig)


def load_config() -> AppConfig:
    return AppConfig()
