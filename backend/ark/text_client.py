"""火山方舟纯文本 LLM 客户端：仅供 L3 记忆蒸馏 / L4 信号生成使用。

与发声链路（Seeduplex）完全解耦：
- 鉴权用方舟 api_key（Bearer），与 openspeech 语音凭证不同。
- 任何调用失败都向上抛出，由调用方决定降级（绝不阻塞语音通话）。
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

import httpx

from ..config import ArkConfig

logger = logging.getLogger(__name__)

# 用量上报回调：参数为方舟响应里的 usage dict（含 prompt/completion/total_tokens）。
# 由上层注入（带 elder_id/scene 上下文），客户端本身不碰 DB（单一职责）。
UsageSink = Callable[[dict], Awaitable[None]]


class ArkTextClient:
    def __init__(self, cfg: ArkConfig, usage_sink: Optional[UsageSink] = None) -> None:
        self._cfg = cfg
        self._usage_sink = usage_sink

    async def chat(self, messages: list, *, temperature: float = 0.3, timeout: float = 30.0) -> str:
        """调用方舟 chat/completions，返回文本内容。失败抛出异常。"""
        payload = {
            "model": self._cfg.text_model,
            "messages": messages,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(
            base_url=self._cfg.base_url,
            headers={"Authorization": f"Bearer {self._cfg.api_key}"},
            timeout=timeout,
        ) as client:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        await self._report_usage(data.get("usage"))
        return _extract_message(data)

    async def _report_usage(self, usage: Optional[dict]) -> None:
        """上报 token 用量。任何异常都吞掉——记账绝不能影响主链路。"""
        if not (self._usage_sink and isinstance(usage, dict)):
            return
        try:
            await self._usage_sink(usage)
        except Exception:
            logger.warning("用量上报失败（已忽略，不影响主链路）")

    async def chat_json(self, messages: list, *, timeout: float = 30.0) -> Optional[dict]:
        """要求模型输出 JSON 并解析；解析失败返回 None（由调用方降级）。"""
        text = await self.chat(messages, temperature=0.2, timeout=timeout)
        return _safe_json(text)


def _extract_message(data: dict) -> str:
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _safe_json(text: str) -> Optional[dict]:
    """容错解析：剥离可能的 ```json 围栏后再解析。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.lstrip("json").strip().rstrip("`").strip()
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        logger.warning("方舟返回非合法 JSON，已降级：%s", text[:200])
        return None
