"""Elder care aggregation service.

This keeps product-facing care logic out of HTTP routers. The elder app gets a
small, stable "today" contract; parent APIs can update family-authored content
without knowing how the elder page renders it.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from ..memory import MemoryStore
from .store import CareStore


class ElderCareService:
    def __init__(self, memory_store: MemoryStore, care_store: CareStore) -> None:
        self._memory = memory_store
        self._care = care_store

    async def today(self, elder_id: str) -> dict:
        facts = await self._memory.active_key_facts(elder_id)
        meds = [f for f in facts if f.get("category") == "用药"]
        contacts = [f for f in facts if f.get("category") == "紧急联系人"]
        important = [
            f for f in facts if f.get("category") in ("慢病", "忌口", "重要日期", "其他")
        ]
        medication_rows = await self._care.list_medication_reminders(elder_id)
        contact_rows = await self._care.list_emergency_contacts(elder_id)
        medication = (
            _structured_medication_card(medication_rows[0])
            if medication_rows
            else _medication_card(meds[0])
            if meds
            else _default_medication_card()
        )
        emergency = (
            _structured_emergency_card(contact_rows)
            if contact_rows
            else _emergency_card(contacts[0])
            if contacts
            else _default_emergency_card()
        )
        greeting = await self.elder_greeting(elder_id)
        quiet_reminders = [
            {"kind": "greeting", "text": greeting["text"]},
        ]
        if medication["source"] in ("family", "structured"):
            quiet_reminders.append({"kind": "medication", "text": medication["text"]})
        return {
            "date": time.strftime("%Y-%m-%d", time.localtime()),
            "greeting": greeting,
            "medication": medication,
            "emergency": emergency,
            "quiet_companion": {
                "title": "安心陪伴",
                "text": "打开后，小暖会安安静静陪着你，只在提醒和你主动说话时出声。",
                "enabled_default": False,
                "first_prompt_after_seconds": 90,
                "reminder_interval_seconds": 1800,
                "reminders": quiet_reminders,
            },
            "fallback": {
                "title": "没接通也没关系",
                "text": greeting["text"],
            },
            "reminders": [_public_fact(f) for f in important[:3]],
            "actions": {
                "log_endpoint": f"/api/elder/{elder_id}/actions",
                "medication_confirm_endpoint": f"/api/elder/{elder_id}/medications/{{id}}/taken",
            },
        }

    async def elder_greeting(self, elder_id: str) -> dict:
        family = await self._care.active_daily_greeting(elder_id)
        if family:
            return {
                "id": family["id"],
                "from": family["sender_name"],
                "title": f"{family['sender_name']}给你留了一句话",
                "text": family["text"],
                "source": "family",
                "updated_at": family["updated_at"],
            }
        greeting = _default_greeting()
        greeting["source"] = "default"
        return greeting

    async def parent_greeting(self, elder_id: str) -> dict:
        greeting = await self._care.active_daily_greeting(elder_id)
        if greeting:
            return {"greeting": _parent_greeting(greeting)}
        return {"greeting": None, "suggestion": _default_greeting()["text"]}

    async def set_parent_greeting(self, elder_id: str, sender_name: str, text: str) -> dict:
        greeting = await self._care.set_daily_greeting(elder_id, sender_name, text)
        return {"greeting": _parent_greeting(greeting)}

    async def parent_contacts(self, elder_id: str) -> dict:
        return {"items": await self._care.list_emergency_contacts(elder_id)}

    async def add_parent_contact(
        self, elder_id: str, name: str, phone: str, relation: str = "", priority: int = 1
    ) -> dict:
        contact = await self._care.upsert_emergency_contact(
            elder_id, name, phone, relation=relation, priority=priority
        )
        return {"contact": contact}

    async def delete_parent_contact(self, elder_id: str, contact_id: int) -> None:
        await self._care.delete_emergency_contact(elder_id, contact_id)

    async def parent_medications(self, elder_id: str) -> dict:
        return {"items": await self._care.list_medication_reminders(elder_id)}

    async def add_parent_medication(
        self,
        elder_id: str,
        medicine_name: str,
        schedule_text: str,
        dosage: str = "",
        note: str = "",
    ) -> dict:
        medication = await self._care.add_medication_reminder(
            elder_id,
            medicine_name,
            schedule_text,
            dosage=dosage,
            note=note,
        )
        return {"medication": medication}

    async def delete_parent_medication(self, elder_id: str, medication_id: int) -> None:
        await self._care.delete_medication_reminder(elder_id, medication_id)

    async def log_elder_action(
        self,
        elder_id: str,
        action_type: str,
        *,
        target_type: str = "",
        target_id: int | None = None,
        detail: str = "",
    ) -> dict:
        return await self._care.log_action(
            elder_id,
            action_type,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )

    async def medication_taken(self, elder_id: str, medication_id: int) -> dict:
        medication = await self._care.get_medication_reminder(elder_id, medication_id)
        if medication is None:
            raise ValueError("用药提醒不存在")
        detail = f"{medication['medicine_name']} {medication.get('dosage') or ''}".strip()
        return await self.log_elder_action(
            elder_id,
            "medication_taken",
            target_type="medication",
            target_id=medication_id,
            detail=detail,
        )

    async def recent_elder_actions(self, elder_id: str, limit: int = 20) -> dict:
        return {"items": await self._care.recent_actions(elder_id, limit=limit)}


def _default_greeting() -> dict:
    hour = time.localtime().tm_hour
    if 5 <= hour < 11:
        text = "早上好，今天别着急，慢慢来。想聊天就点小暖，我们都惦记着你。"
    elif 11 <= hour < 18:
        text = "今天过得还好吗？累了就歇一会儿。想聊天就点小暖，我们都惦记着你。"
    else:
        text = "晚上好，今天也辛苦了。早点休息，想说说话就点小暖。"
    return {"from": "家人", "title": "家人给你留了一句话", "text": text}


def _medication_card(fact: dict) -> dict:
    return {
        "title": "用药提醒",
        "id": None,
        "text": fact.get("content") or "今天记得按时吃药",
        "sub": "家人已经帮你记着",
        "confirmable": False,
        "source": "family",
    }


def _default_medication_card() -> dict:
    return {
        "title": "今日提醒",
        "id": None,
        "text": "今天先照顾好自己，按平时习惯吃饭、喝水、休息。",
        "sub": "有不舒服就点上面和小暖说",
        "confirmable": False,
        "source": "default",
    }


def _emergency_card(fact: dict) -> dict:
    content = fact.get("content") or ""
    phone = _extract_phone(content)
    return {
        "title": "叫家人",
        "text": "不舒服或着急时，直接点这里。",
        "contact": content,
        "phone": phone,
        "contacts": (
            [{"id": None, "name": content or "家人", "relation": "", "phone": phone, "priority": 1}]
            if phone
            else []
        ),
        "enabled": bool(phone),
    }


def _default_emergency_card() -> dict:
    return {
        "title": "叫家人",
        "text": "家人还没设置电话。着急时请直接拨打常用联系人或 120。",
        "contact": "",
        "phone": "",
        "contacts": [],
        "enabled": False,
    }


def _structured_medication_card(row: dict) -> dict:
    dosage = row.get("dosage") or ""
    schedule = row.get("schedule_text") or ""
    note = row.get("note") or ""
    sub_parts = [p for p in (schedule, dosage, note) if p]
    return {
        "title": "用药提醒",
        "id": row["id"],
        "text": row.get("medicine_name") or "今天记得按时吃药",
        "sub": " · ".join(sub_parts) if sub_parts else "家人已经帮你记着",
        "confirmable": True,
        "source": "structured",
    }


def _structured_emergency_card(rows: list[dict]) -> dict:
    contacts = [
        {
            "id": row["id"],
            "name": row["name"],
            "relation": row.get("relation") or "",
            "phone": row["phone"],
            "priority": row["priority"],
        }
        for row in rows
    ]
    first = contacts[0]
    return {
        "title": "叫家人",
        "text": f"不舒服或着急时，先联系{first['name']}。",
        "contact": f"{first['name']} {first['phone']}",
        "phone": first["phone"],
        "contacts": contacts,
        "enabled": True,
    }


def _public_fact(fact: dict) -> dict:
    return {"category": fact.get("category", "其他"), "content": fact.get("content", "")}


def _parent_greeting(greeting: dict) -> dict:
    return {
        "id": greeting["id"],
        "sender_name": greeting["sender_name"],
        "text": greeting["text"],
        "updated_at": greeting["updated_at"],
    }


def _extract_phone(text: str) -> str:
    match = re.search(r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}-?\d{7,8})(?!\d)", text or "")
    return match.group(0) if match else ""
