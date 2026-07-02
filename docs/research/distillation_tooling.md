# 小暖蒸馏与语音评测工具选型

## 当前结论

小暖现阶段不应把模型训练框架接进在线服务。当前优先级是：

1. 用 VoiceBench 建立语音 Agent 回归评测。
2. 用匿名轨迹导出沉淀后续训练数据。
3. 在数据、评测和算力目标明确后，再选择 EasyDistill、Agent Distillation 或 KDFlow。

原因是当前仓库的核心链路是实时语音陪伴：老人端 WebSocket、后端语音引擎、记忆蒸馏、信号生成。线上服务需要稳定、低依赖、隐私边界清晰；训练框架通常依赖 CUDA、多 GPU、SGLang、FSDP2、TRL 等重组件，不应进入 `backend/requirements.txt`。

## 已接入

### VoiceBench

- 仓库：https://github.com/MatthewCYM/VoiceBench
- 本仓库位置：`third_party/voicebench`
- 用途：语音助手回归评测，覆盖开放问答、多轮、指令跟随、安全、wild voice 等任务。
- 集成方式：通过 `tools/evals/voicebench_adapter/` 将 wav manifest 发送到小暖 `/ws/elder/{elder_id}`，输出 JSONL。
- 不做的事：不下载 VoiceBench 数据集到主仓库，不把 VoiceBench 依赖加入后端运行依赖。

## 已新增内部能力

### 匿名轨迹导出

- 位置：`backend/distillation/trajectory_exporter.py`
- 默认状态：关闭。
- 开启方式：

```text
DISTILLATION_EXPORT_ENABLED=1
DISTILLATION_EXPORT_DIR=artifacts/distillation_exports
```

导出内容只包含脱敏后的 user / assistant 文本轮次和最小元信息。不导出原始音频、真实 `elder_id`、家庭 token、数据库主键或子女端不可见的家庭标识。

该能力用于为后续 EasyDistill / AgentKD / Agent Distillation 准备训练样本，但它本身不是训练框架。

## 后续候选

### EasyDistill / AgentKD

- 仓库：https://github.com/modelscope/easydistill
- 适合场景：黑盒/白盒知识蒸馏、数据合成、SFT（监督微调）、ranking optimization、RL（强化学习）训练。
- AgentKD 价值：能从 persona seeds 生成虚拟 tool-use 任务、工具 schema、教师轨迹和 rubrics。
- 当前不接入原因：小暖还没有产品化工具调用轨迹，也没有匿名真实轨迹基线。现在接入会增加训练栈复杂度，但不能提升老人端通话质量。
- 升级条件：已有至少一批授权匿名轨迹、明确学生模型目标、明确工具调用任务集合。

### Agent Distillation

- 仓库：https://github.com/Nardien/agent-distillation
- 适合场景：把大 LLM Agent 的 retrieval / code tool 行为蒸馏到小模型，基于 smolagents 和 TRL。
- 可迁移点：日志轨迹保存、训练 JSONL 组织、benchmark 流程。
- 当前不接入原因：项目偏检索/代码工具 Agent；小暖目前核心是语音陪伴、记忆和信号，不是复杂工具执行。
- 升级条件：小暖引入可审计工具调用，如日程、用药提醒、家庭通知、知识库检索，并有稳定轨迹格式。

### KDFlow

- 仓库：https://github.com/songmzhang/KDFlow
- 适合场景：off-policy、on-policy、cross-tokenizer、multi-teacher、自蒸馏和多模态 VLM 蒸馏。
- 技术价值：教师推理和学生训练解耦，支持 SGLang、FSDP2、hidden-state transfer、LoRA 和多教师路由。
- 当前不接入原因：依赖 CUDA、多 GPU、SGLang/FSDP2，属于训练基础设施，不属于小暖在线服务。
- 升级条件：明确要训练学生 LLM/VLM，已有 VoiceBench 基线和匿名训练集，并具备独立训练环境。

## 方法参考

### DM-Codec

- 仓库：https://github.com/mubtasimahasan/DM-Codec
- 方向：speech tokenization，将 acoustic、semantic、contextual 表示蒸馏到 speech tokenizer。
- 对小暖的意义：如果未来从托管 Seeduplex 转向自研 Speech LLM，可参考其语音 token 表征设计。
- 当前不接入原因：当前语音主链路依赖 Seeduplex，不训练自有 tokenizer。

### DistillAV

- 方向：从 speech foundation model 蒸馏 audio-visual representation。
- 对小暖的意义：如果未来加入视频、唇形或人脸场景，可参考其音视频鲁棒表征蒸馏。
- 当前不接入原因：当前产品没有视频输入，强行接入会扩大隐私面。

### LLaVA-KD

- 仓库：https://github.com/Fantasyele/LLaVA-KD
- 方向：视觉语言多模态蒸馏，包含 Multimodal Distillation、Relation Distillation 和三阶段训练。
- 对小暖的意义：其“跨模态对齐 + 关系蒸馏”思想可迁移到 audio-text 或 audio-visual-text。
- 当前不接入原因：框架面向视觉语言模型，不直接解决当前语音陪伴链路。

### Ultravox / DiVA

- Ultravox 方向：实时语音多模态 LLM，强调 audio adapter / projector 与文本 LLM 对齐。
- DiVA 方向：用 transcript 上 text-only LLM 的响应做 self-supervision / context distillation，减少语音 instruction data 依赖。
- 对小暖的意义：未来自研 Speech LLM 时，可借鉴 speech-to-LLM logits 或 context distillation。
- 当前不接入原因：当前没有训练自有 Speech LLM 的计划，且 Seeduplex 已提供端到端实时语音能力。

## 升级路径

进入训练框架前必须满足四个条件：

1. 有 VoiceBench 或自定义老人陪伴评测基线。
2. 有授权、匿名、可删除的轨迹数据。
3. 有明确学生模型目标，例如降低成本、降低延迟、增强端侧可用性。
4. 有独立训练环境，不污染在线后端依赖。

满足条件后建议顺序：

1. 用匿名轨迹做 SFT 数据格式验证。
2. 用 EasyDistill 或 Agent Distillation 跑小规模 agent 行为蒸馏。
3. 若需要 on-policy、多教师或 cross-tokenizer，再引入 KDFlow。
4. 如果问题转向语音表征，再研究 DM-Codec、Ultravox、DiVA。
