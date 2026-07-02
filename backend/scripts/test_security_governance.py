"""Security and governance smoke tests.

运行：.venv/bin/python -m backend.scripts.test_security_governance
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


async def main() -> None:
    import httpx

    from .. import config as cfg_mod

    tmp_db = os.path.join(tempfile.gettempdir(), "security_governance_test.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    orig_load = cfg_mod.load_config

    def _patched_load():
        c = orig_load()
        object.__setattr__(c, "db_path", tmp_db)
        object.__setattr__(c, "auth_required", True)
        object.__setattr__(c, "family_api_token", "family-test-token")
        return c

    cfg_mod.load_config = _patched_load

    from .. import server as srv

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with srv.app.router.lifespan_context(srv.app):
            r = await client.get("/api/parent/elder-sec/key_facts")
            assert r.status_code == 401, r.text

            r = await client.post(
                "/api/auth/login",
                json={
                    "display_name": "女儿",
                    "role": "parent",
                    "elder_id": "elder-sec",
                    "family_id": "family-sec",
                },
            )
            assert r.status_code == 200, r.text
            token = r.json()["token"]
            r = await client.get(
                f"/api/parent/elder-sec/key_facts?session_token={token}"
            )
            assert r.status_code == 200, r.text

            r = await client.post(
                f"/api/parent/elder-sec/characters?session_token={token}",
                json={"name": "女儿", "relation": "女儿"},
            )
            assert r.status_code == 200, r.text
            cid = r.json()["character"]["id"]
            r = await client.post(
                f"/api/parent/elder-sec/characters/{cid}/voice/sample?session_token={token}",
                data={"consent": "true", "consent_text": "本人授权，仅家庭陪伴"},
                files={"audio": ("sample.webm", b"private voice sample", "audio/webm")},
            )
            assert r.status_code == 200, r.text
            sample = await srv._voice_store.latest_sample("elder-sec", cid)
            assert sample and sample["encrypted"] == 1, sample
            path = Path(sample["storage_path"])
            assert path.exists() and path.suffix == ".enc", path
            assert b"private voice sample" not in path.read_bytes()
            r = await client.delete(
                f"/api/parent/elder-sec/characters/{cid}/voice_data?session_token={token}"
            )
            assert r.status_code == 200, r.text
            assert not path.exists(), "声音样本文件应被物理删除"

    print("[PASS] 鉴权边界与声音样本加密/删除治理通过")


if __name__ == "__main__":
    asyncio.run(main())
