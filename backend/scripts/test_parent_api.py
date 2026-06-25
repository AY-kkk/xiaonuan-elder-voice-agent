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

            # 5) 子女端读取信号
            r = await client.get(f"/api/parent/{elder}/signals")
            assert r.status_code == 200, r.text
            sigs = r.json()["items"]
            assert len(sigs) == 1 and sigs[0]["level"] == "urgent", sigs
            blob = str(sigs[0])
            assert "摔了一下" not in blob and "膝盖有点疼" not in blob, "API 泄露原始对话！"

            # 6) 删除事项
            fid = items[0]["id"]
            r = await client.delete(f"/api/parent/{elder}/key_facts/{fid}")
            assert r.status_code == 200, r.text
            r = await client.get(f"/api/parent/{elder}/key_facts")
            assert all(f["id"] != fid for f in r.json()["items"]), "删除未生效"

    print("[PASS] 子女端 API 全链路：配置事项 / 校验 / 信号生成 / 隐私读取 / 删除")
    print(f"  信号摘要：{sigs[0]['summary']}")
    print("\n所有子女端 API 冒烟通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
