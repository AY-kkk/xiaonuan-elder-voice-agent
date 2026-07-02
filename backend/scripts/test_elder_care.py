"""老人端今日关怀链路测试。

运行：.venv/bin/python -m backend.scripts.test_elder_care
"""
from __future__ import annotations

import asyncio
import os
import tempfile


async def main() -> None:
    import httpx

    from .. import config as cfg_mod

    tmp_db = os.path.join(tempfile.gettempdir(), "elder_care_test.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    orig_load = cfg_mod.load_config

    def _patched_load():
        c = orig_load()
        object.__setattr__(c, "db_path", tmp_db)
        return c

    cfg_mod.load_config = _patched_load

    from .. import server as srv

    transport = httpx.ASGITransport(app=srv.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with srv.app.router.lifespan_context(srv.app):
            elder = "elder-care"

            r = await client.get(f"/api/parent/{elder}/daily_greeting")
            assert r.status_code == 200, r.text
            assert r.json()["greeting"] is None and r.json()["suggestion"], r.text

            r = await client.post(
                f"/api/parent/{elder}/daily_greeting",
                json={"sender_name": "女儿小芳", "text": "爸，今天慢慢来，晚上我给你打电话。"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["greeting"]["sender_name"] == "女儿小芳", r.text

            await client.post(
                f"/api/parent/{elder}/key_facts",
                json={"category": "用药", "content": "早饭后吃降压药 1 片"},
            )
            r = await client.post(
                f"/api/parent/{elder}/emergency_contacts",
                json={
                    "name": "女儿小芳",
                    "relation": "女儿",
                    "phone": "13800138000",
                    "priority": 1,
                },
            )
            assert r.status_code == 200, r.text
            contact_id = r.json()["contact"]["id"]
            r = await client.post(
                f"/api/parent/{elder}/medications",
                json={
                    "medicine_name": "降压药",
                    "schedule_text": "早饭后",
                    "dosage": "1 片",
                    "note": "温水服用",
                },
            )
            assert r.status_code == 200, r.text
            medication_id = r.json()["medication"]["id"]

            r = await client.get(f"/api/elder/{elder}/today")
            assert r.status_code == 200, r.text
            today = r.json()
            assert today["greeting"]["source"] == "family", today
            assert today["greeting"]["from"] == "女儿小芳", today
            assert today["medication"]["id"] == medication_id, today
            assert today["medication"]["text"] == "降压药", today
            assert today["medication"]["confirmable"] is True, today
            assert today["emergency"]["contacts"][0]["id"] == contact_id, today
            assert today["emergency"]["phone"] == "13800138000", today
            quiet = today["quiet_companion"]
            assert quiet["first_prompt_after_seconds"] > 0, quiet
            assert any(item["kind"] == "medication" for item in quiet["reminders"]), quiet

            r = await client.post(f"/api/elder/{elder}/medications/{medication_id}/taken")
            assert r.status_code == 200, r.text
            assert r.json()["action"]["action_type"] == "medication_taken", r.text
            r = await client.post(
                f"/api/elder/{elder}/actions",
                json={
                    "action_type": "emergency_call_clicked",
                    "target_type": "contact",
                    "target_id": contact_id,
                    "detail": "女儿小芳 13800138000",
                },
            )
            assert r.status_code == 200, r.text
            r = await client.get(f"/api/parent/{elder}/care_actions")
            assert r.status_code == 200, r.text
            actions = r.json()["items"]
            assert any(a["action_type"] == "medication_taken" for a in actions), actions
            assert any(a["action_type"] == "emergency_call_clicked" for a in actions), actions

    print("[PASS] 老人端今日关怀：家人问候/用药/紧急联系人/安心提醒策略全部通过")


if __name__ == "__main__":
    asyncio.run(main())
