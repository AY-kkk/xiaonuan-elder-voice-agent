# 适老端 ↔ 后端 控制帧契约 v1

> **单一真源**：本文档是适老端客户端与后端之间通信协议的唯一权威定义。
> 任何协议变更 **必须先改本文档并评审**，再同步三端实现（后端 `session/manager.py`、
> Web 端 `web/elder/app.js`、KMP 端 `ControlFrame.kt`）与契约测试常量。文档是裁判。

## 1. 连接

| 项 | 值 |
|----|----|
| 端点 | `/ws/elder/{elder_id}` |
| 协议 | WebSocket |
| 鉴权 | 由后端网关持有外部凭证，客户端永不接触（隐私/安全硬边界） |

## 2. 帧类型总览

WebSocket 帧分两类：**二进制帧 = 音频 PCM**，**文本帧 = 控制信令(JSON)**。

| 方向 | 帧形态 | 内容 | 格式 |
|------|--------|------|------|
| 上行 | 二进制 | 麦克风 PCM | 16kHz / mono / 16bit / 小端，建议 20ms/帧 |
| 上行 | 文本 | 控制信令 | 见 §3 |
| 下行 | 二进制 | TTS PCM | 24kHz / mono / 16bit / 小端 |
| 下行 | 文本 | 控制信令/字幕 | 见 §4 |

> ⚠️ 上行 16k、下行 24k 采样率不同，播放器需按 24k 配置。

## 3. 上行控制帧（客户端 → 后端）

| type | 字段 | 说明 |
|------|------|------|
| `hangup` | 无 | 主动挂断，后端收到后结束会话 |

## 4. 下行控制帧（后端 → 客户端）

| type | 字段 | 说明 |
|------|------|------|
| `barge_in` | 无 | 服务端检测到用户插话，客户端应立即停播（本地 VAD 为抢跑，此为兜底） |
| `text` | `role`, `text` | 字幕。`role` ∈ {`user`,`assistant`} |
| `status` | `status`, `detail` | 会话状态。`status` ∈ {`connected`,`ended`,`error`}，`detail` 为补充信息（如错误码 `connect_failed`） |

## 5. 帧示例

```json
// 上行：挂断
{"type":"hangup"}

// 下行：打断
{"type":"barge_in"}

// 下行：字幕
{"type":"text","role":"assistant","text":"今天天气不错呢"}

// 下行：状态
{"type":"status","status":"connected","detail":""}
{"type":"status","status":"error","detail":"connect_failed"}
```

## 6. 一致性保障

- 后端契约测试：`backend/scripts/test_protocol_contract.py` 静态扫描 `manager.py`，
  断言出现的 type 均在本文档声明集合内。
- 客户端契约测试：`app/composeApp/src/commonTest/.../ControlFrameTest.kt` 断言
  `ControlFrame.parse` 覆盖全部下行 type。
- 任一处新增/修改 type 而未同步本文档与测试常量，对应测试将失败。
