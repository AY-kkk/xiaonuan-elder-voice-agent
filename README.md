# 小暖：老年人语音对话 agent 小助手

面向独居老人的语音陪伴 Agent。老人端通过自然语音对话获得情感陪伴与日常关照，
子女端在隐私硬边界下查看温和的「状态信号」（绝不接触原始对话内容）。

## 架构总览

| 层 | 技术 | 职责 |
|----|------|------|
| 客户端 | Compose Multiplatform + Kotlin Multiplatform（Android / iOS 同源） | 音频采集/播放、本地 VAD 抢跑打断、WebSocket 通话 |
| 网关/后端 | FastAPI + WebSocket | 双向音频转发、会话生命周期、控制信令 |
| 语音引擎 | 火山 Seeduplex 端到端实时语音（`VOLC_*` 鉴权） | 全双工「听 + 说」发声链路 |
| 文本链路 | 火山方舟 LLM（`ARK_API_KEY` 鉴权，与语音不通用） | L3 记忆蒸馏、L4 信号摘要润色 |
| 记忆/信号 | SQLite 分层记忆（层级 A 重点事项 / B 生活记忆）+ 规则信号引擎 | 跨会话记忆、子女端状态信号 |

> 两套鉴权体系互相独立、不可混用；缺方舟 key 时自动降级为规则提炼，不阻塞通话。

## 隐私硬边界（PRD 护栏指标 = 0）

子女端 `signals` 表只存等级/心情/话题标签/次数/结论摘要，**绝不写入任何原始对话句子**。
由 [`backend/scripts/test_privacy.py`](backend/scripts/test_privacy.py) 在 CI 中回归把关。

## 目录结构

```
app/        CMP+KMP 客户端（commonMain 共享核心 + Android/iOS actual）
backend/    FastAPI 后端（engine 语音引擎 / memory 记忆 / signals 信号 / session 会话）
docs/       protocol.md —— 客户端↔后端控制帧协议单一真源
.github/    CI 门禁（native-build 三端编译 + backend-test 离线测试）
```

## 本地运行（后端）

```bash
cp .env.example .env   # 填入 VOLC_* 与可选 ARK_API_KEY
pip install -r backend/requirements.txt
python -m backend.server
```

健康检查 `GET /healthz` 返回引擎与两套凭证的分项就绪状态（`engine` / `volc_ready` / `ark_enabled`）。

## 测试

```bash
python -m backend.scripts.test_privacy            # 隐私回归（阻断项）
python -m backend.scripts.test_protocol_contract  # 控制帧契约（与 docs/protocol.md 一致）
python -m backend.scripts.test_signals
```

客户端契约单测 `ControlFrameTest` 随 `native-build` CI 在编译时运行。

## 交付安全网（R1–R5）

详见 [交付风险解决方案_R1-R5.md](交付风险解决方案_R1-R5.md)：协议真源、契约测试、凭证 fail-fast、隐私护栏、CI 三端编译门禁。
