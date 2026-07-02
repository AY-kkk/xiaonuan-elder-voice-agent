"""后端入口：FastAPI 应用 + 适老端 WS 网关 + 子女端 HTTP API + 静态前端。

适老端 WS：/ws/elder/{elder_id}
  - 二进制消息 = 上行 PCM 音频
  - 文本消息(JSON) = 控制信令（hangup 等）
记忆与信号模块在 L3/L4 接入；此处通过依赖注入挂载回调，缺省为空实现。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .auth import FamilyAuthMiddleware
from .account import AccountStore
from .config import AppConfig, load_config
from .engine.factory import create_engine
from .jobs import BackgroundJobService
from .memory import MemoryService, MemoryStore
from .signals import SignalService
from .usage import UsageStore
from .character import CharacterService, CharacterStore
from .care import CareStore, ElderCareService
from .distillation import TrajectoryExporter
from .voice import VoiceService, VoiceStore
from .api import auth as auth_api
from .api import parent as parent_api
from .api import elder as elder_api
from .api import character as character_api
from .session.manager import ConversationSession
from .session.store import SessionEventStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gateway")


@dataclass
class AppContainer:
    cfg: AppConfig
    account_store: AccountStore
    usage_store: UsageStore
    memory_store: MemoryStore
    care_store: CareStore
    memory: MemoryService
    care: ElderCareService
    signals: SignalService
    character_store: CharacterStore
    character: CharacterService
    voice_store: VoiceStore
    voice: VoiceService
    session_events: SessionEventStore
    jobs: BackgroundJobService
    trajectory_exporter: TrajectoryExporter


def build_container(config: AppConfig | None = None) -> AppContainer:
    """构建服务容器。

    目前仍保留 module-level app 兼容 `backend.server:app`，但所有服务实例从这里
    集中创建，测试或后续拆分部署时可以替换 config 后复用同一装配路径。
    """
    c = config or load_config()
    account_store = AccountStore(c.db_path)
    usage_store = UsageStore(c.db_path)
    memory_store = MemoryStore(c.db_path)
    care_store = CareStore(c.db_path)
    memory = MemoryService(memory_store, c.ark, usage_store=usage_store)
    care = ElderCareService(memory_store, care_store)
    signals = SignalService(c.db_path, c.ark, usage_store=usage_store)
    character_store = CharacterStore(c.db_path)
    character = CharacterService(character_store, c.volc, c.ark, usage_store=usage_store)
    voice_store = VoiceStore(c.db_path)
    voice = VoiceService(voice_store, character_store)
    session_events = SessionEventStore(c.db_path)
    jobs = BackgroundJobService(c.db_path)
    trajectory_exporter = TrajectoryExporter()
    return AppContainer(
        cfg=c,
        account_store=account_store,
        usage_store=usage_store,
        memory_store=memory_store,
        care_store=care_store,
        memory=memory,
        care=care,
        signals=signals,
        character_store=character_store,
        character=character,
        voice_store=voice_store,
        voice=voice,
        session_events=session_events,
        jobs=jobs,
        trajectory_exporter=trajectory_exporter,
    )


cfg = load_config()
app = FastAPI(title="老年陪伴语音 Agent 后端")
_container = build_container(cfg)
_account_store = _container.account_store

# 前后端分离部署时放行跨域（CORS_ALLOW_ORIGINS 配置；留空=同源托管，不挂中间件最安全）
if cfg.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(
    FamilyAuthMiddleware,
    required=cfg.auth_required,
    token=cfg.family_api_token,
    account_store=_account_store,
)

_usage_store = _container.usage_store
_memory_store = _container.memory_store
_care_store = _container.care_store
_memory = _container.memory
_care = _container.care
_signals = _container.signals
_character_store = _container.character_store
_character = _container.character
_voice_store = _container.voice_store
_voice = _container.voice
_session_events = _container.session_events
_jobs = _container.jobs
_trajectory_exporter = _container.trajectory_exporter
auth_api.bind(_account_store)
parent_api.bind(_memory_store, _signals, _usage_store, cfg.ark_price_per_mtoken, _character, _voice, _care)
elder_api.bind(_memory_store, _character, _care)
character_api.bind(_character)
app.include_router(auth_api.router)
app.include_router(parent_api.router)
app.include_router(elder_api.router)
app.include_router(character_api.router)


def _probe_volc_ready() -> bool:
    """探测火山语音凭证是否就绪：读 property 触发惰性校验，缺失会抛 RuntimeError。"""
    try:
        return bool(cfg.volc.app_id and cfg.volc.access_token)
    except RuntimeError:
        return False


@app.on_event("startup")
async def _init_db() -> None:
    await _account_store.ensure_schema()
    await _memory_store.ensure_schema()
    await _care_store.ensure_schema()
    await _signals.ensure_schema()
    await _usage_store.ensure_schema()
    await _character_store.ensure_schema()
    await _voice_store.ensure_schema()
    await _session_events.ensure_schema()
    await _jobs.ensure_schema()


@app.on_event("startup")
async def _check_credentials() -> None:
    """凭证 fail-fast：启动时尽早暴露 seeduplex 缺凭证问题，但不崩进程，
    以便 /healthz 仍可访问、供运维定位（volc_ready=false）。"""
    if cfg.engine == "seeduplex" and not _probe_volc_ready():
        logger.error(
            "引擎=seeduplex 但缺少火山语音凭证（VOLC_APP_ID / VOLC_ACCESS_TOKEN），"
            "语音链路将无法建连。请在根目录 .env 配置后重启；/healthz 将标记 volc_ready=false。"
        )


def _on_session_end(elder_id: str, session_id: str):
    """会话结束钩子：异步触发蒸馏 + 信号生成，立即返回不阻塞（PRD 8.2 解耦）。"""

    async def _handler(transcript: list) -> None:
        _jobs.submit(elder_id, "memory_distill", lambda: _memory.distill(elder_id, transcript))
        _jobs.submit(elder_id, "signal_generate", lambda: _signals.generate(elder_id, transcript))
        _jobs.submit(
            elder_id,
            "character_persona_refine",
            lambda: _character.refine_active_persona_from_dialogue(elder_id, transcript),
        )
        if _trajectory_exporter.enabled:
            _jobs.submit(
                elder_id,
                "trajectory_export",
                lambda: _trajectory_exporter.export(
                    elder_id=elder_id,
                    session_id=session_id,
                    transcript=transcript,
                    engine=cfg.engine,
                ),
            )

    return _handler


def _build_context_with_persona(elder_id: str):
    """组合上下文提供器：基础记忆人设 + 当前启用角色的人格提示词。

    人格段落拼在基础人设之后注入 system_role —— 这是「第二蒸馏」的注入点，
    无需重训即让对话角色按目标人物的口吻/性格再生。无启用角色则只用基础人设。
    """

    async def _provider() -> tuple:
        base_prompt, dialog_context = await _memory.build_context(elder_id)
        persona = await _character.active_persona(elder_id)
        if persona:
            base_prompt = f"{base_prompt}\n\n{persona}"
        return base_prompt, dialog_context

    return _provider


def _speaker_provider_for(elder_id: str):
    """会话发声音色提供器：返回当前启用角色的克隆音色（未启用/未就绪则 None）。"""

    async def _provider():
        return await _character.active_speaker(elder_id)

    return _provider


@app.websocket("/ws/elder/{elder_id}")
async def elder_ws(ws: WebSocket, elder_id: str) -> None:
    if cfg.auth_required:
        family_token = ws.query_params.get("family_token")
        session_token = ws.query_params.get("session_token")
        allowed = family_token == cfg.family_api_token or await _account_store.allowed(
            session_token or "", elder_id=elder_id, required_role="elder"
        )
        if not allowed:
            await ws.close(code=1008)
            return
    await ws.accept()
    session_id = uuid.uuid4().hex
    audio_frames = 0
    audio_bytes = 0
    await _session_events.log(elder_id, session_id, "ws_accepted")
    logger.info("适老端接入 elder_id=%s session_id=%s", elder_id, session_id)

    async def client_send(msg: bytes | str) -> None:
        if isinstance(msg, bytes):
            await ws.send_bytes(msg)
        else:
            await ws.send_text(msg)

    session = ConversationSession(
        elder_id=elder_id,
        engine=create_engine(cfg),
        client_send=client_send,
        context_provider=_build_context_with_persona(elder_id),
        on_turn_text=None,
        on_session_end=_on_session_end(elder_id, session_id),
        speaker_provider=_speaker_provider_for(elder_id),
    )

    try:
        await session.start()
        await _session_events.log(elder_id, session_id, "session_started")
    except Exception as exc:  # 建连失败：友好提示后关闭
        logger.exception("会话启动失败")
        await _session_events.log(elder_id, session_id, "session_start_failed", detail=str(exc))
        await client_send('{"type":"status","status":"error","detail":"connect_failed"}')
        await ws.close()
        return

    recv_task = asyncio.create_task(session.run_until_end())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                audio_frames += 1
                audio_bytes += len(data)
                await session.push_audio(data)
            elif (text := message.get("text")) is not None:
                await session.handle_client_text(text)
    except WebSocketDisconnect:
        logger.info("适老端断开 elder_id=%s", elder_id)
        await _session_events.log(
            elder_id,
            session_id,
            "websocket_disconnected",
            audio_frames=audio_frames,
            audio_bytes=audio_bytes,
        )
    finally:
        await session.stop()
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("接收任务已随 WebSocket 断开结束", exc_info=True)
        await _session_events.log(
            elder_id,
            session_id,
            "session_closed",
            audio_frames=audio_frames,
            audio_bytes=audio_bytes,
        )


@app.get("/healthz")
async def healthz() -> dict:
    """健康检查：除存活外，给出引擎与两套凭证的分项就绪状态，便于运维定位。

    volc_ready：语音链路凭证（VOLC_*）是否就绪，仅 seeduplex 引擎下有意义。
    ark_enabled：方舟文本链路是否启用（缺 key 时降级为规则，不影响存活）。
    """
    volc_ready = _probe_volc_ready()
    degraded = cfg.engine == "seeduplex" and not volc_ready
    background_job_failures_24h = await _jobs.recent_failures()
    if background_job_failures_24h:
        degraded = True
    return {
        "status": "degraded" if degraded else "ok",
        "engine": cfg.engine,
        "volc_ready": volc_ready,
        "ark_enabled": cfg.ark.enabled,
        "model": cfg.volc.model_version,
        "speaker": cfg.volc.speaker,
        "auth_required": cfg.auth_required,
        "session_events_enabled": True,
        "background_job_failures_24h": background_job_failures_24h,
        "last_background_job_failure": await _jobs.last_failure(),
    }


_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# 单一根挂载（html=True）：/ 提供入口选择页 index.html，/shared/* 提供共享样式，
# /elder/ /parent/ /admin/ 自动按子目录托管。必须放在所有 API/WS 路由之后，
# 这样显式路由优先，静态挂载只兜底未匹配路径。
if _WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn

    uvicorn.run("backend.server:app", host=cfg.host, port=cfg.port, reload=False)


if __name__ == "__main__":
    main()
