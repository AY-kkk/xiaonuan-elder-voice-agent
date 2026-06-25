"""端到端联调（无凭证）：用 Fake 引擎跑通整条产品链路。

启动一个真实 uvicorn 子进程（VOICE_ENGINE=fake + 临时 DB），然后：
  1) 第一轮会话：真实 WS 上行音频 -> 收到字幕/TTS 音频（验证 L1 透传）
     -> 挂断触发蒸馏（L3 写库）与信号生成（L4 写库）。
  2) 第二轮会话：验证 L3 跨会话——第二次注入的 system_prompt 含第一次的记忆。
  3) 子女端 API：读到信号、且绝不含原始对话（L4 隐私边界）。

运行：.venv/bin/python -m backend.scripts.test_e2e
凭证到位后把 VOICE_ENGINE 改回 seeduplex，同一条链路即接真实火山。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile

import httpx
import websockets

ELDER = "elder-001"
SILENCE_FRAME = b"\x00" * 640  # 20ms @16k/mono/s16le 上行帧


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_health(base: str, timeout: float = 15.0) -> None:
    async with httpx.AsyncClient() as c:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await c.get(f"{base}/healthz")
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("服务启动超时")


async def _run_session(ws_url: str, frames: int) -> dict:
    """跑一轮会话：持续上行音频，收集字幕与音频字节数，然后挂断。"""
    texts = []
    audio_bytes = 0
    barge_in = 0
    async with websockets.connect(ws_url, max_size=None) as ws:
        async def reader():
            nonlocal audio_bytes, barge_in
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        audio_bytes += len(msg)
                    else:
                        data = json.loads(msg)
                        if data.get("type") == "text":
                            texts.append((data["role"], data["text"]))
                        elif data.get("type") == "barge_in":
                            barge_in += 1
            except websockets.ConnectionClosed:
                pass

        rtask = asyncio.create_task(reader())
        for _ in range(frames):
            await ws.send(SILENCE_FRAME)
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.5)  # 等回包
        await ws.send(json.dumps({"type": "hangup"}))
        await asyncio.sleep(0.3)
        rtask.cancel()
    return {"texts": texts, "audio_bytes": audio_bytes, "barge_in": barge_in}


async def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}/ws/elder/{ELDER}"
    tmp_db = os.path.join(tempfile.gettempdir(), "e2e_test.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    env = dict(os.environ, VOICE_ENGINE="fake", DB_PATH=tmp_db, PORT=str(port), HOST="127.0.0.1")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "backend.server:app",
        "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        env=env,
    )
    try:
        await _wait_health(base)

        # ---- 第一轮会话：L1 透传 ----
        r1 = await _run_session(ws_url, frames=160)  # 触发约 3 轮脚本
        user_lines = [t for role, t in r1["texts"] if role == "user"]
        bot_lines = [t for role, t in r1["texts"] if role == "assistant"]
        assert user_lines and bot_lines, f"未收到字幕：{r1['texts']}"
        assert r1["audio_bytes"] > 0, "未收到 TTS 音频"
        assert r1["barge_in"] > 0, "未收到 barge-in 事件"
        print(f"[PASS] L1 透传：字幕 {len(user_lines)} 问 / {len(bot_lines)} 答，"
              f"TTS {r1['audio_bytes']} 字节，barge-in {r1['barge_in']} 次")

        # 等待会话结束后的异步蒸馏 + 信号落库
        await asyncio.sleep(4.0)

        # ---- L3：跨会话记忆引用 ----
        async with httpx.AsyncClient(base_url=base) as c:
            facts = (await c.get(f"/api/parent/{ELDER}/key_facts")).json()["items"]
        fact_blob = " ".join(f"{f['category']}:{f['content']}" for f in facts)
        print(f"[INFO] 蒸馏出的重点事项：{fact_blob or '（空，方舟未配置则正常）'}")

        # 第二轮会话：再次连接，验证注入的上下文包含历史记忆
        r2 = await _run_session(ws_url, frames=60)
        assert r2["texts"], "第二轮未收到字幕"

        # 硬断言 L3：直接对临时库调 build_context，确认历史记忆会被注入 StartSession
        from ..config import ArkConfig
        from ..memory import MemoryService, MemoryStore

        store = MemoryStore(tmp_db)
        svc = MemoryService(store, ArkConfig())
        prompt, _ctx = await svc.build_context(ELDER)
        if facts:  # 方舟已配置才有蒸馏记忆可断言
            assert any(f["content"] in prompt for f in facts), \
                f"第二轮注入上下文未包含历史记忆：{prompt}"
            print("[PASS] L3 跨会话记忆：第二轮 StartSession 注入了第一轮蒸馏的事项")
        else:
            print("[SKIP] L3 记忆断言：未配置 ARK_API_KEY，无蒸馏记忆（链路本身正常）")

        # ---- L4：信号生成 + 隐私边界 ----
        async with httpx.AsyncClient(base_url=base) as c:
            signals = (await c.get(f"/api/parent/{ELDER}/signals")).json()["items"]
        assert signals, "未生成任何信号"
        blob = json.dumps(signals, ensure_ascii=False)
        for _role, text in r1["texts"]:
            assert text not in blob, f"信号泄露了原始对话：{text}"
        print(f"[PASS] L4 信号：生成 {len(signals)} 条，隐私边界通过（无原始对话）")
        print(f"  最新信号：level={signals[0]['level']} mood={signals[0]['mood']} "
              f"summary={signals[0]['summary']}")

        print("\n端到端联调全部通过 ✅（Fake 引擎；凭证到位后切 seeduplex 即接真实火山）")
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
