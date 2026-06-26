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

## 🚀 快速开始（即填即用）

### 方式一：一键启动（推荐，零凭证即可跑）

```bash
./run.sh
```

脚本会自动：建虚拟环境 → 装依赖 → （首次）从 `.env.example` 复制 `.env` → 用 **fake 引擎**零凭证启动整条链路。启动后打开：

- 老人端：http://127.0.0.1:8000/elder/
- 子女端：http://127.0.0.1:8000/parent/

> fake 模式用脚本化对话模拟语音链路，无需任何 API 凭证，用于体验/联调全流程。

### 方式二：接真实火山语音

1. **填 API 凭证** —— 编辑项目根目录 `.env`，只需填两行（在火山语音控制台获取）：

   ```ini
   VOLC_APP_ID=你的AppID
   VOLC_ACCESS_TOKEN=你的AccessToken
   ```

   > 👉 **这就是唯一必填的 API 配置位置**。其余变量保持默认即可。
   > 选填：`ARK_API_KEY` 填了能让记忆/信号摘要更自然，留空自动降级为规则，不影响通话。

2. **启动**：

   ```bash
   ./run.sh --real
   ```

### 手动启动（不想用脚本时）

```bash
cp .env.example .env                      # 首次：复制配置模板
pip install -r backend/requirements.txt   # 装依赖
VOICE_ENGINE=fake python -m backend.server  # fake 模式；接真实语音去掉前缀并填好 .env
```

健康检查 `GET /healthz` 返回引擎与两套凭证的分项就绪状态（`engine` / `volc_ready` / `ark_enabled`）。

## 前后端分离部署（选填）

默认后端用 `StaticFiles` 同源托管两个前端，无需任何额外配置。若要把前端单独部署到别的域名：

1. 后端 `.env` 填 `CORS_ALLOW_ORIGINS=https://你的前端域名`（逗号分隔多个）。
2. 前端 [`web/elder/config.js`](web/elder/config.js) 与 [`web/parent/config.js`](web/parent/config.js) 把 `API_BASE` 填成后端地址（不带末尾斜杠）。

留空即同源，二者互不影响。

## 测试

```bash
python -m backend.scripts.test_e2e                # 端到端（fake 引擎跑通全链路）
python -m backend.scripts.test_privacy            # 隐私回归（阻断项）
python -m backend.scripts.test_session_isolation  # 多会话隔离（防串线阻断项）
python -m backend.scripts.test_protocol_contract  # 控制帧契约（与 docs/protocol.md 一致）
python -m backend.scripts.test_signals
```

客户端契约单测 `ControlFrameTest` 随 `native-build` CI 在编译时运行。

## 交付安全网（R1–R5）

详见 [交付风险解决方案_R1-R5.md](交付风险解决方案_R1-R5.md)：协议真源、契约测试、凭证 fail-fast、隐私护栏、CI 三端编译门禁。
