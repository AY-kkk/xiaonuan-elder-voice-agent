# X-OmniClaw 借鉴边界分析：能借什么、不能借什么、差距在哪

> 目的：回答"参考 X-OmniClaw 代码仓库完善我的项目"这个诉求。
> 结论先行：**不搬它的代码，借鉴它两条经过验证的运行时纪律**——因为它和本项目不是同一类产品。
>
> 依据仓库：[Master-Frank/X-OmniClaw](https://github.com/Master-Frank/X-OmniClaw)（经核实是 [OPPO-Mente-Lab/X-OmniClaw](https://github.com/OPPO-Mente-Lab/X-OmniClaw) 的 fork，内容一致）
> 技术报告：arXiv 2605.05765

---

## 一、第一性认知：X-OmniClaw 到底是什么

它是 **手机 GUI 自动化 Agent（mobile GUI automation agent）**，不是语音对话 Agent。

它的核心循环是 **Observation → Reasoning → Execution**：

| 阶段 | 它做的事 |
|------|---------|
| Observation | 截图 + 无障碍 XML 树 + 录屏帧，拼成"屏幕观察栈" |
| Reasoning | LLM/VLM 看懂当前页面，决定下一步点哪里 |
| Execution | 通过 Android 原子动作**模拟点击/滑动/输入，去操作淘宝、美团、剪映等别的 App** |

它打包的 10 个技能全是操控外部 App：`taobao-search`、`gallery-qa`、`capcut-theme-video`、`scheduled-automation`……它的"语音"只是 push-to-talk 触发自动化的入口（STT 用 SiliconFlow SenseVoice）。

**这正是本项目 PRD 明确列为 Out of Scope 的"替老人操作手机"。** 所以它的源码主体（device scheduler、accessibility 点击、截图、行为克隆）对"语音陪伴 App"是负资产：无落点、徒增攻击面。

### 一个关键澄清：它不是 KMP

X-OmniClaw 是**纯 Android 单平台**（原生 Kotlin APK，目录 `app/src/main/...`）。本项目是 **CMP+KMP 双端**。即便有想借的逻辑，也不能文件级 copy——只能把模式翻译进本项目的 `expect/actual` 骨架。

---

## 二、它真正值得借鉴的，是两条运行时纪律（出处：仓库 changelog）

读它的更新日志，有两条和本项目顾虑精确同频，而且**反过来印证本项目架构方向是对的**：

| X-OmniClaw 的做法（原文） | 印证/启发本项目什么 |
|---|---|
| `Multi-session parallelism: per-session agent loops, isolated runtime across sessions, precise stop chains` | "A 用户拿到 B 的回答"的正解是**每会话独立运行时**，不是云端一人一沙盒。本项目后端已这么做。 |
| 技术报告比喻：手机是"车辆"，云端大模型只是"燃料" | "把编排放端侧、推理放云端"——本项目已经是这个架构。它明确**反对**云手机/云沙盒路线（点名 RedFinger、阿里无影、腾讯云手机），理由与"每人 2G 沙盒太贵"一字不差。 |

**核心结论：本项目不需要"按它完善"，因为已经在用它主张的架构。** 它能提供的不是代码，是方向背书 + 一个具体加固点：**precise stop chain（精确停止链）**。

---

## 三、对照真实代码的差距诊断

### 差距 1（真实 bug）：打断停止链不精确，会残留串轮

借鉴点：X-OmniClaw 的 `precise stop chains`。

当前 [CallController.kt](../app/composeApp/src/commonMain/kotlin/com/elder/companion/session/CallController.kt) 打断时只做了 `player.clear()`（停本地播放），但**没有处理"已在 WebSocket 管道在途的下行音频帧"**。

问题时序：

```
1. 老人开口打断 → 本地 VAD 命中 → player.clear() 清空播放队列, bargedThisTurn=true
2. 但此刻服务端还有半句 TTS 音频在网络在途（尚未到达客户端）
3. 这些在途帧到达 → handleIncoming 走 Audio 分支：
       player.enqueue(incoming.pcm)   // 继续播旧回复的残尾
       bargedThisTurn = false         // 还把打断标志重置了
4. 结果：老人听到被打断的旧话又冒出来一截，且打断态被错误清零
```

根因：**客户端无法区分一帧下行音频属于"被打断的旧轮"还是"新一轮回复"**。

X-OmniClaw 的解法思想：给每轮一个标识，停止链按标识丢弃滞后产物。翻译到本项目 = **加一个"轮次门闩"**：打断后短暂丢弃在途音频，直到服务端发出新一轮的明确起点（新 turn 的首个 text/status 或一个新增的 `turn` 信令）才重新放行。

> 注意：这一步可能需要在 [docs/protocol.md](protocol.md) 增补"轮次边界"语义，属于协议变更，需同步三端契约测试。

### 差距 2（已做对，缺测试钉死）：多会话隔离

借鉴点：X-OmniClaw 的 `isolated runtime across sessions`。

本项目后端 [session/manager.py](../backend/session/manager.py) 已是 per-session 隔离：
- 每条 WS 连接 new 一个 `ConversationSession`
- `_transcript` 是**实例字段**（非全局）
- 记忆/信号按 `elder_id` 作用域（`WHERE elder_id=?`）

**这块代码不用改。** 差的只是一个并发回归测试：开 N 个不同 `elder_id` 会话，断言彼此 transcript/记忆/信号互不污染——把"不会串线"从"我相信"变成"CI 每次回归都验证"。这直接回应最初的核心顾虑。

### 差距 3（增强项）：端侧运行时鲁棒性

借鉴点：X-OmniClaw 强调端侧闭环"failures converge and execution continues"（失败收敛、持续运行）。

当前 [WsClient.kt](../app/composeApp/src/commonMain/kotlin/com/elder/companion/net/WsClient.kt) 断线即流结束 → [CallController](../app/composeApp/src/commonMain/kotlin/com/elder/companion/session/CallController.kt) 直接进 ERROR/IDLE，**无重连**。老人网络一抖动通话就断、要重新点。可借鉴"失败收敛"思想加：
- [CallState.kt](../app/composeApp/src/commonMain/kotlin/com/elder/companion/session/CallState.kt) 增 `RECONNECTING` 态
- WsClient 加有限次退避重连
- 重连成功后无缝续接，UI 给"正在重新连接…"温和提示

---

## 四、可落地清单（都在 PRD 范围内、借思想不搬代码）

> 状态：三项已全部落地（2026-06）。

| 项 | 类型 | 涉及文件 | 协议变更 | 状态 |
|----|------|---------|---------|------|
| 修打断停止链竞态（音频门闩） | Bug 修复 | CallController.kt / web/elder/app.js | 否（复用 text 帧作开闸点） | ✅ 已修 |
| 多会话隔离回归测试 | 测试加固 | backend/scripts/test_session_isolation.py（纯离线、已纳入 CI） | 否 | ✅ 已加 |
| 断线重连 + RECONNECTING 态 | 鲁棒性增强 | CallController.kt / CallState.kt / CallScreen.kt / App.kt | 否 | ✅ 已加 |

实现要点修正（相对初版分析）：
- **打断停止链不需要改协议**。原以为要加轮次信令，实际只需把"新轮开闸点"从乱序的音频帧改为可靠的 `text(assistant)` 字幕帧 + 一个 `acceptingAudio` 门闩，纯客户端修复。
- **重连未硬塞单测**。重连是依赖真实 WsClient/AudioCapture 的 IO 编排逻辑，在 commonTest 离线 mock 会引入额外依赖且 expect/actual 类无法实例化，违背"依赖审慎"。改由 CI 编译 + 真机联调验证。

### 明确不做（X-OmniClaw 有但本项目 Out of Scope）
- ❌ 屏幕截图 / 无障碍树 / GUI 点击自动化
- ❌ 摄像头视觉理解 / OCR
- ❌ 行为克隆 / 技能录制回放
- ❌ 跨 App 操控、相册记忆蒸馏

---

## 五、下一步

本文档只做分析。请基于第四节清单确认要落地哪几项，再进入实现。
建议顺序：先做"多会话隔离回归测试"（纯后端、零风险、直接回应核心顾虑）→ 再评估"打断停止链"（涉及协议，要先想清楚轮次语义）→ 最后"断线重连"（纯增强）。
