# 小暖 VoiceBench 评测与蒸馏轨迹导出设计

## 背景

当前小暖的在线链路是实时语音陪伴产品：老人端通过 WebSocket 发送 PCM 音频，后端桥接语音引擎，并在会话结束后异步做记忆蒸馏和信号生成。现有“蒸馏”主要指把会话文本提炼成结构化记忆、信号和人格提示词，不是模型训练框架。

外部工具的接入目标不是立即训练模型，而是先补齐两个前提：

1. 语音 Agent 的可复现回归评测。
2. 后续训练可用、默认关闭、可匿名化的轨迹导出。

## 决策

采用“评测优先，训练隔离”的方案。

- 接入 VoiceBench 作为语音 Agent 回归评测工具，代码隔离在 `third_party/voicebench`。
- 新增小暖专用评测适配器，放在 `tools/evals/voicebench_adapter/`。
- 新增匿名轨迹导出模块，放在 `backend/distillation/`。
- EasyDistill / AgentKD、Agent Distillation、KDFlow 暂不进入线上依赖；只在研究文档中记录使用边界。
- DM-Codec、DistillAV、LLaVA-KD、Ultravox、DiVA 只作为后续自研 Speech LLM 的方法参考。

## 为什么不直接引入训练框架

当前仓库没有离线训练入口、训练数据目录、多 GPU 配置或学生模型部署路径。直接把 KDFlow、EasyDistill 或 Agent Distillation 放进后端依赖，会增加 CUDA、SGLang、FSDP2、TRL 等重依赖，但不能立刻提升老人端通话质量。

最短路径是先建立“数据与评测闭环”：能跑语音样本、能产出结果、能导出干净轨迹，再决定是否进入 KDFlow 或 EasyDistill 的训练阶段。

## 外部工具取舍

| 工具 | 结论 | 原因 |
|---|---|---|
| VoiceBench | 立即接入 | 覆盖开放问答、多轮、指令跟随、安全、wild voice，适合作为语音助手验收集 |
| VocalBench | 可选后续接入 | 更关注 vocal conversational abilities，可作为 VoiceBench 的补充 |
| EasyDistill / AgentKD | 先准备数据，不接训练框架 | AgentKD 适合虚拟工具轨迹与 rubrics，但小暖还缺真实匿名轨迹 |
| Agent Distillation | 先准备数据，不接训练框架 | 适合 tool-use agent 轨迹蒸馏，但当前小暖工具调用链路尚未产品化 |
| KDFlow | 后续训练阶段接入 | 适合 off-policy、on-policy、cross-tokenizer、multi-teacher、多模态 KD，但依赖重 |
| DM-Codec | 方法参考 | 解决 speech tokenization，不适合当前 Seeduplex 托管语音主链路 |
| DistillAV | 方法参考 | 适合语音+视频/唇形场景，当前产品没有视频输入 |
| LLaVA-KD | 方法参考 | 视觉语言蒸馏框架，可迁移思想，不直接服务当前语音链路 |
| Ultravox / DiVA | 方法参考 | 对 speech-to-LLM logits 对齐有启发，适合未来自研 Speech LLM |

## 架构

### 1. VoiceBench 隔离接入

下载方式优先使用 git submodule，将外部源码固定在：

```text
third_party/voicebench
```

主仓库不提交 VoiceBench 数据集和评测输出，只提交子模块指针和适配器代码。评测产物写入：

```text
artifacts/evals/voicebench/
```

`artifacts/` 必须加入 `.gitignore`。

### 2. 小暖评测适配器

新增目录：

```text
tools/evals/voicebench_adapter/
```

职责：

- 读取 VoiceBench 或本地 JSONL manifest。
- 对 `.wav` 音频做格式校验：16kHz、mono、signed 16-bit PCM。
- 将音频切成 20ms PCM 帧，通过 `/ws/elder/{elder_id}` 发送。
- 收集 `assistant` 文本回复，输出 VoiceBench 风格 JSONL。
- 支持 fake 引擎 smoke test，也支持真实 Seeduplex 链路评测。

适配器不直接依赖 VoiceBench 的 Python 包结构，优先通过文件协议连接，降低外部仓库变化带来的破坏。

### 3. 匿名轨迹导出

新增模块：

```text
backend/distillation/trajectory_exporter.py
```

导出默认关闭，仅在环境变量显式开启时生效：

```text
DISTILLATION_EXPORT_ENABLED=1
DISTILLATION_EXPORT_DIR=artifacts/distillation_exports
```

会话结束后，`server.py` 的 `_on_session_end` 在提交记忆蒸馏与信号生成后台任务的同时，提交轨迹导出任务。导出失败只记录日志，不影响通话、蒸馏和信号生成。

### 4. 导出格式

每行一个 JSON 对象：

```json
{
  "schema_version": "xiaonuan-agent-trajectory-v1",
  "sample_id": "sha256-prefix",
  "source": "xiaonuan_ws_session",
  "elder_ref": "anon-sha256-prefix",
  "turns": [
    {"role": "user", "text": "脱敏后的老人文本"},
    {"role": "assistant", "text": "脱敏后的小暖回复"}
  ],
  "metadata": {
    "engine": "fake|seeduplex",
    "privacy": "redacted",
    "created_at": 1760000000.0
  }
}
```

不导出：

- 原始音频。
- 家庭 token。
- 真实 `elder_id`。
- 数据库主键。
- 子女端不可见的家庭标识。

## 隐私与安全

轨迹导出是高风险能力，必须满足：

- 默认关闭。
- 只写入 `.gitignore` 覆盖的本地产物目录。
- 不改变子女端 API 可见数据。
- 不复用 `signals` 表或 `life_memories` 表承载训练数据。
- 对手机号、身份证号、长数字串、明显地址关键词做基础脱敏。

基础脱敏不是合规终局，只是开发阶段的最低护栏。真实训练前仍需人工审查、授权记录和数据删除机制。

## 测试

新增或调整的验证：

1. `backend/scripts/test_trajectory_export.py`
   - 验证默认关闭时不写文件。
   - 验证开启后生成 JSONL。
   - 验证空 transcript 不写文件。
   - 验证手机号、长数字串等被脱敏。
2. `backend/scripts/test_voicebench_adapter.py`
   - 使用 fake 引擎启动临时服务。
   - 发送一段本地静音 wav 或 PCM manifest。
   - 验证输出 JSONL 包含 assistant 回复。
3. 继续运行既有回归：
   - `python -m backend.scripts.test_privacy`
   - `python -m backend.scripts.test_e2e`
   - `python -m backend.scripts.test_session_isolation`

## 实施顺序

1. 下载 VoiceBench 到 `third_party/voicebench`，固定 commit。
2. 更新 `.gitignore`，排除 `artifacts/`、`eval_outputs/`、`distillation_exports/`。
3. 新增 `backend/distillation/trajectory_exporter.py` 与测试。
4. 在 `server.py` 的会话结束钩子中接入可选导出。
5. 新增 `tools/evals/voicebench_adapter/`。
6. 新增研究文档 `docs/research/distillation_tooling.md`。
7. 运行回归测试。

## 非目标

- 不训练学生模型。
- 不下载 VoiceBench 数据集到仓库。
- 不把 KDFlow、EasyDistill、Agent Distillation 加入 `backend/requirements.txt`。
- 不改变老人端 WebSocket 协议。
- 不改变子女端可见数据范围。
