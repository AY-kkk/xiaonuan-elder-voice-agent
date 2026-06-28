"""角色再生（声音克隆 + 人格蒸馏）自测：纯离线、不依赖任何凭证。

覆盖：
  1. CharacterStore CRUD + 多角色按 elder 隔离；
  2. 激活互斥（每个 elder 至多一个 active，切换原子）；
  3. 声音克隆 mock 降级（无 VOLC 凭证直接就绪）；
  4. 人格蒸馏模板降级（无 ARK 凭证仍产出可注入提示词）；
  5. 会话注入读数：active_speaker / active_persona 仅在就绪+启用时返回；
  6. 隐私边界：库里只存 speaker_id 与人格提示词，绝不含原始语料/音频。

运行：.venv/bin/python -m backend.scripts.test_character
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from ..config import ArkConfig, VolcConfig
from ..character import CharacterService, CharacterStore
from ..character.chat_parser import parse_chat_record
from ..character.persona import _render_prompt

ELDER = "elder-char-test"
OTHER = "elder-other"
CORPUS = "闺女平时总说：爸你别太累着，钱够花就行，记得按时吃饭啊。"


def _check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if (not cond and detail) else ''}")
    if not cond:
        raise AssertionError(f"{name} {detail}".strip())


def _fresh_db(tag: str) -> str:
    tmp = Path(tempfile.gettempdir()) / f"character_{tag}.db"
    if tmp.exists():
        tmp.unlink()
    return str(tmp)


def _service(db_path: str) -> CharacterService:
    """构造不依赖真实凭证的服务：VOLC/ARK 均无凭证 -> mock/模板降级。"""
    # 确保环境无 ARK_API_KEY，人格走模板降级
    os.environ.pop("ARK_API_KEY", None)
    store = CharacterStore(db_path)
    return store, CharacterService(store, VolcConfig(), ArkConfig())


async def _test_crud_and_isolation() -> None:
    db = _fresh_db("crud")
    store, svc = _service(db)
    await store.ensure_schema()

    c1 = await svc.create(ELDER, "女儿小芳", "女儿")
    c2 = await svc.create(ELDER, "老伴", "老伴")
    # 另一个 elder 的角色，验证隔离
    await svc.create(OTHER, "别人家的", "邻居")

    mine = await svc.list(ELDER)
    _check("多角色创建", len(mine) == 2, f"实际 {len(mine)}")
    _check("角色按 elder 隔离", all(c["name"] != "别人家的" for c in mine))

    # 重名幂等：再建同名不应新增
    again = await svc.create(ELDER, "女儿小芳", "女儿")
    _check("重名幂等不新增", again["id"] == c1["id"] and len(await svc.list(ELDER)) == 2)
    return db, store, svc, c1, c2


async def _test_voice_mock() -> None:
    db = _fresh_db("voice")
    store, svc = _service(db)
    await store.ensure_schema()
    c = await svc.create(ELDER, "女儿", "女儿")

    # 提交训练：mock 模式返回 training（模拟真实「克隆中」），而非瞬时就绪
    snap = await svc.train_voice(ELDER, c["id"], "S_mocktest1", b"\x00\x01\x02\x03", "wav")
    _check("提交后进入克隆中(training)", snap["voice_status"] == "training", f"实际 {snap['voice_status']}")
    _check("speaker_id 落库", snap["speaker_id"] == "S_mocktest1")

    # 模拟训练耗时已过：把 mock 起始时间回拨，轮询应转为 ready
    svc._voice._mock_train_started["S_mocktest1"] -= 999
    snap2 = await svc.refresh_voice_status(ELDER, c["id"])
    _check("训练完成后转为就绪(ready)", snap2["voice_status"] == "ready", f"实际 {snap2['voice_status']}")


async def _test_persona_template() -> None:
    db = _fresh_db("persona")
    store, svc = _service(db)
    await store.ensure_schema()
    c = await svc.create(ELDER, "老伴", "老伴")

    snap = await svc.distill_persona(ELDER, c["id"], CORPUS)
    _check("无方舟人格蒸馏模板降级就绪", snap["persona_status"] == "ready")
    _check("人格提示词非空且含角色名", "老伴" in snap["persona_prompt"] and len(snap["persona_prompt"]) > 10)
    # 原始语料绝不落库
    _check("原始语料不落库", CORPUS not in snap["persona_prompt"])


async def _test_chat_record_parser() -> None:
    txt = parse_chat_record("chat.txt", "女儿: 爸，记得吃饭\n爸爸: 好的".encode("utf-8"))
    _check("txt 聊天记录解析", "记得吃饭" in txt)
    js = parse_chat_record(
        "chat.json",
        '[{"sender":"女儿","text":"爸你慢慢来"},{"sender":"爸爸","text":"好"}]'.encode("utf-8"),
    )
    _check("json 聊天记录解析", "女儿: 爸你慢慢来" in js)
    csv_bytes = "sender,message\n女儿,爸你别着急\n爸爸,好\n".encode("utf-8")
    csv_text = parse_chat_record("chat.csv", csv_bytes)
    _check("csv 聊天记录解析", "女儿: 爸你别着急" in csv_text)
    rendered = _render_prompt(
        "女儿",
        "女儿",
        {
            "identity": "女儿",
            "sample_phrases": ["爸你别着急，记得按时吃饭"],
            "phrase_patterns": ["安抚式短句；提醒式短句"],
        },
    )
    _check("人格渲染不落 sample 原句", "爸你别着急" not in rendered and "句式模式" in rendered)


async def _test_activation_and_injection() -> None:
    db = _fresh_db("active")
    store, svc = _service(db)
    await store.ensure_schema()
    c1 = await svc.create(ELDER, "女儿", "女儿")
    c2 = await svc.create(ELDER, "老伴", "老伴")

    # 未启用任何角色：注入读数为 None（回落默认）
    _check("未启用时 speaker=None", await svc.active_speaker(ELDER) is None)
    _check("未启用时 persona=None", await svc.active_persona(ELDER) is None)

    # 给 c1 配齐声音 + 人格并启用
    await svc.train_voice(ELDER, c1["id"], "S_daughter1", b"\x00\x01", "wav")
    # 快进 mock 训练并刷新状态到 ready（否则 active_speaker 因未就绪返回 None）
    svc._voice._mock_train_started["S_daughter1"] -= 999
    await svc.refresh_voice_status(ELDER, c1["id"])
    await svc.distill_persona(ELDER, c1["id"], CORPUS)
    c1_ready = await svc.get(ELDER, c1["id"])
    _check("声音+说话方式就绪后可同步", c1_ready["sync_status"] == "ready", c1_ready["sync_status"])
    _check("同步给老人端成功", await svc.sync_to_elder(ELDER, c1["id"]))
    companions = await svc.companions_for_elder(ELDER)
    _check("老人端 companions 只暴露轻量角色", any(i["id"] == c1["id"] for i in companions["items"]))
    _check("老人端新角色提示存在", companions["notice"]["character_id"] == c1["id"])
    ok = await svc.activate(ELDER, c1["id"])
    _check("激活成功", ok)
    _check("启用后注入 speaker", await svc.active_speaker(ELDER) == "S_daughter1")
    _check("启用后注入 persona 非空", bool(await svc.active_persona(ELDER)))

    # 切换到 c2（互斥）：只有 c2 active
    await svc.activate(ELDER, c2["id"])
    roles = await svc.list(ELDER)
    actives = [r for r in roles if r["is_active"]]
    _check("激活互斥：至多一个 active", len(actives) == 1 and actives[0]["id"] == c2["id"])

    # c2 未配声音/人格 -> 注入读数回落 None（只在就绪时返回）
    _check("启用未就绪角色时 speaker=None", await svc.active_speaker(ELDER) is None)
    _check("启用未就绪角色时 persona=None", await svc.active_persona(ELDER) is None)

    # 取消启用
    await svc.deactivate(ELDER)
    _check("取消启用后无 active", not any(r["is_active"] for r in await svc.list(ELDER)))

    # 激活不存在的角色 -> False
    _check("激活不存在角色返回 False", not await svc.activate(ELDER, 999999))


async def main() -> None:
    await _test_crud_and_isolation()
    await _test_voice_mock()
    await _test_persona_template()
    await _test_chat_record_parser()
    await _test_activation_and_injection()
    print("\n角色再生（声音克隆 + 人格蒸馏）自测全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
