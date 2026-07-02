"""从 JSONL manifest 批量运行小暖语音评测样本。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlencode, urlparse, urlunparse

from .ws_client import run_voice_session


def build_ws_url(base_url: str, elder_id: str, family_token: str = "") -> str:
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        raise ValueError(f"base-url 必须以 http:// 或 https:// 开头：{base_url}")
    path = f"/ws/elder/{elder_id}"
    query = urlencode({"family_token": family_token}) if family_token else ""
    return urlunparse((scheme, parsed.netloc, path, "", query, ""))


def load_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"manifest 第 {line_no} 行不是合法 JSON") from exc
            if not row.get("id") or not row.get("audio_path"):
                raise ValueError(f"manifest 第 {line_no} 行必须包含 id 和 audio_path")
            rows.append(row)
    return rows


def resolve_audio_path(manifest_path: Path, audio_path: str) -> Path:
    path = Path(audio_path)
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


async def run_manifest(
    *,
    base_url: str,
    elder_id: str,
    manifest_path: Path,
    output_path: Path,
    model_name: str = "xiaonuan",
    family_token: str = "",
    limit: Optional[int] = None,
) -> None:
    ws_url = build_ws_url(base_url, elder_id, family_token=family_token)
    rows = load_manifest(manifest_path)
    if limit is not None:
        rows = rows[: max(0, limit)]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as output_file:
        for row in rows:
            audio_path = resolve_audio_path(manifest_path, str(row["audio_path"]))
            result = await run_voice_session(ws_url, audio_path)
            output = {
                "id": row["id"],
                "subset": row.get("subset", ""),
                "model": model_name,
                "response": "\n".join(result["assistant_texts"]),
                "metadata": {
                    "audio_bytes": result["audio_bytes"],
                    "barge_in": result["barge_in"],
                    "ws_url": ws_url,
                    "user_text_count": len(result["user_texts"]),
                    "assistant_text_count": len(result["assistant_texts"]),
                },
            }
            output_file.write(json.dumps(output, ensure_ascii=False) + "\n")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行小暖 VoiceBench manifest 评测")
    parser.add_argument("--base-url", required=True, help="小暖后端 HTTP 地址，如 http://127.0.0.1:8000")
    parser.add_argument("--elder-id", required=True, help="评测使用的 elder_id")
    parser.add_argument("--manifest", required=True, type=Path, help="JSONL manifest 路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSONL 路径")
    parser.add_argument("--model-name", default="xiaonuan", help="输出中的模型名")
    parser.add_argument("--family-token", default="", help="AUTH_REQUIRED=1 时使用的 family_token")
    parser.add_argument("--limit", type=int, default=None, help="最多运行多少条样本")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    asyncio.run(
        run_manifest(
            base_url=args.base_url,
            elder_id=args.elder_id,
            manifest_path=args.manifest,
            output_path=args.output,
            model_name=args.model_name,
            family_token=args.family_token,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
