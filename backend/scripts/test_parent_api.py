"""子女端 API 端到端冒烟（ASGI 内存测试，无需起 uvicorn）。

运行：.venv/bin/python -m backend.scripts.test_parent_api
验证：建表(startup) -> 配置重点事项 -> 生成信号 -> 子女端读取，全链路接线正确。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

# 用临时库，避免污染开发库；必须在 import server 前设置
os.environ["DB_PATH_OVERRIDE"] = ""  # 占位，真实覆盖见下方 monkeypatch


async def main() -> None:
    import httpx

    from .. import config as cfg_mod

    tmp_db = os.path.join(tempfile.gettempdir(), "parent_api_test.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    # 覆盖 db_path：在 load_config 返回前打补丁
    orig_load = cfg_mod.load_config

    def _patched_load():
        c = orig_load()
        object.__setattr__(c, "db_path", tmp_db)
        return c

    cfg_mod.load_config = _patched_load

    from .. import server as srv
    from ..signals import rules

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 触发 startup 建表
        async with srv.app.router.lifespan_context(srv.app):
            elder = "elder-001"

            # 1) 分类枚举
            r = await client.get("/api/parent/categories")
            assert r.status_code == 200 and "用药" in r.json()["categories"], r.text

            # 2) 子女配置重点事项
            r = await client.post(
                f"/api/parent/{elder}/key_facts",
                json={"category": "用药", "content": "每天早上提醒吃降压药"},
            )
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert any(f["source"] == "parent" for f in items), items
            parent_fact = next(f for f in items if f["source"] == "parent")
            assert parent_fact["status"] == "active", items

            # 3) 非法分类被拒
            r = await client.post(
                f"/api/parent/{elder}/key_facts", json={"category": "瞎填", "content": "x"}
            )
            assert r.status_code == 400, r.text

            # 4) 模拟一次会话结束 -> 生成信号（直接调服务，等同 on_session_end）
            transcript = [
                {"role": "user", "text": "我今天膝盖有点疼，还摔了一下，胸口闷。"},
                {"role": "assistant", "text": "您别急，疼得厉害一定要去医院。"},
            ]
            sig = await srv._signals.generate(elder, transcript)
            assert sig and sig["level"] == rules.LEVEL_URGENT, sig

            # 4.1) 模拟蒸馏出的对话重点事项：默认待确认，子女确认后才启用
            await srv._memory_store.add_key_fact(
                elder, "慢病", "疑似有高血压，需要家属确认", source="dialog", confidence=0.62
            )
            await srv._memory_store.add_life_memory(
                elder, "昨天和老同学聊了很久，很想念过去的日子", confidence=0.8
            )
            r = await client.get(f"/api/parent/{elder}/memories")
            assert r.status_code == 200, r.text
            body = r.json()
            assert "life_memories" not in body, "子女端 memories 不能返回老人生活记忆"
            pending = next(f for f in body["reviewable_key_facts"] if f["source"] == "dialog")
            assert pending["status"] == "pending", pending
            assert "老同学" not in str(body), "子女端泄露生活记忆内容"
            r = await client.patch(
                f"/api/parent/{elder}/key_facts/{pending['id']}",
                json={"status": "active", "expires_days": 365},
            )
            assert r.status_code == 200, r.text
            r = await client.patch(
                f"/api/parent/{elder}/memories/1",
                json={"status": "archived"},
            )
            assert r.status_code == 410, r.text

            # 5) 子女端读取信号
            r = await client.get(f"/api/parent/{elder}/signals")
            assert r.status_code == 200, r.text
            sigs = r.json()["items"]
            assert len(sigs) == 1 and sigs[0]["level"] == "urgent", sigs
            blob = str(sigs[0])
            assert "摔了一下" not in blob and "膝盖有点疼" not in blob, "API 泄露原始对话！"

            # 6) 子女端创建角色 -> 准备声音/说话方式 -> 同步给老人端
            r = await client.post(
                f"/api/parent/{elder}/characters",
                json={"name": "女儿小芳", "relation": "女儿", "elder_alias": "小芳"},
            )
            assert r.status_code == 200, r.text
            cid = r.json()["character"]["id"]
            r = await client.post(
                f"/api/parent/{elder}/characters/{cid}/voice",
                data={"speaker_id": "S_parentapi1"},
                files={"audio": ("voice.wav", b"\x00\x01\x02\x03", "audio/wav")},
            )
            assert r.status_code == 200, r.text
            srv._character._voice._mock_train_started["S_parentapi1"] -= 999
            r = await client.get(f"/api/parent/{elder}/characters/{cid}/voice")
            assert r.status_code == 200 and r.json()["voice_status"] == "ready", r.text
            r = await client.post(
                f"/api/parent/{elder}/characters/{cid}/persona",
                json={"corpus": "爸，今天慢慢来，别着急，记得吃饭。"},
            )
            assert r.status_code == 200, r.text
            r = await client.post(f"/api/parent/{elder}/characters/{cid}/sync")
            assert r.status_code == 200, r.text
            r = await client.get(f"/api/elder/{elder}/companions")
            assert r.status_code == 200, r.text
            assert any(i["id"] == cid for i in r.json()["items"]), r.json()

            r = await client.post(
                f"/api/character/{elder}",
                json={"name": "旧入口", "relation": "测试", "elder_alias": "旧入口"},
            )
            assert r.status_code == 410, "旧角色写入口应明确下线，避免双写绕过父端流程"

            # 7) 充值钱包
            r = await client.post(f"/api/parent/{elder}/recharge", json={"amount_cents": 5000})
            assert r.status_code == 200, r.text
            r = await client.get(f"/api/parent/{elder}/wallet")
            assert r.status_code == 200 and r.json()["balance_cents"] >= 5000, r.text

            # 8) 删除事项
            fid = parent_fact["id"]
            r = await client.delete(f"/api/parent/{elder}/key_facts/{fid}")
            assert r.status_code == 200, r.text
            r = await client.get(f"/api/parent/{elder}/key_facts")
            assert all(f["id"] != fid for f in r.json()["items"]), "删除未生效"

    print("[PASS] 子女端 API 全链路：配置事项 / 校验 / 信号生成 / 隐私读取 / 删除")
    print(f"  信号摘要：{sigs[0]['summary']}")
    print("\n所有子女端 API 冒烟通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
