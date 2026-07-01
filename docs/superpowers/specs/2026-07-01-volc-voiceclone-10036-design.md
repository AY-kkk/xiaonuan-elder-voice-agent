# 火山 10036 完整复刻接入设计

## 背景

项目当前已有两条声音相关链路：

- 旧链路：`backend/character/voice_clone.py` 负责调用火山声音复刻训练接口，得到 `speaker_id` 后写入角色。
- 新链路：`backend/voice` 提供 Provider 抽象，当前默认使用 `MockVoiceCloneProvider` 完成样本、档案、试听流程。

用户选择“兼容旧链路”方案：不重构旧训练链路，优先把火山 10036 对应的豆包语音合成能力接到试听层，用于验证“已训练复刻音色是否能合成试听音频”。

## 目标

实现一个最小可验证闭环：

1. 子女端仍可通过旧接口上传授权录音并提交火山声音复刻训练。
2. 训练完成后得到的 `speaker_id` 继续写入 `characters.speaker_id`。
3. 新增火山试听合成能力：使用该 `speaker_id` 调用 `seed-icl-2.0` 单向流式语音合成接口，生成试听音频。
4. 缺少火山 10036 凭证时，自动回退到现有 mock 试听，不影响本地演示和测试。

## 非目标

- 不在本轮重做 `backend/character/voice_clone.py` 的训练接口。
- 不把实时通话链路切换到 10036；老人端实时通话仍走现有 Seeduplex WebSocket。
- 不把原始授权录音暴露到子女端、老人端或日志。

## 架构设计

新增 `VolcTtsPreviewProvider`，只实现 `VoiceCloneProvider.preview()` 的真实火山试听能力；`clone()` 和 `status()` 仍交给兼容逻辑或 mock，不替代旧训练链路。

配置层新增独立的火山 TTS 配置：

- `VOLC_TTS_API_KEY`：新版控制台 API Key，优先用于 10036 相关接口。
- `VOLC_TTS_RESOURCE_ID`：默认 `seed-icl-2.0`。
- `VOLC_TTS_MODEL`：默认 `seed-tts-2.0-standard`，可改为 `seed-tts-2.0-expressive`。
- `VOLC_TTS_ENDPOINT`：默认 `wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream`。

服务装配：

- `server.build_container()` 根据 `VOLC_TTS_API_KEY` 判断是否启用真实试听 Provider。
- 未配置时继续使用 `MockVoiceCloneProvider`。
- `VoiceService.preview()` 优先使用当前角色的 `speaker_id` 或 `voice_profiles.provider_voice_id` 合成试听。

## 数据流

```text
子女端上传授权录音
  -> 旧 voice_clone 训练接口
  -> 火山声音复刻训练
  -> speaker_id 写入 characters
  -> 子女端点击试听
  -> VoiceService.preview()
  -> VolcTtsPreviewProvider.preview(speaker_id, text)
  -> seed-icl-2.0 单向流式 TTS
  -> 返回 audio/mpeg 或 audio/wav
```

## 错误处理

- 缺 `VOLC_TTS_API_KEY`：回退 mock 试听。
- 火山返回鉴权失败、音色不存在、资源未开通：返回 400 级业务错误，前端展示“试听失败，请检查音色或凭证”。
- WebSocket 超时或无音频：返回可读错误，不写入数据库。
- 不记录真实 API Key，不把原始授权音频写入日志。

## 测试

最小测试范围：

- 配置缺失时，`VoiceService.preview()` 仍返回 mock WAV。
- 配置存在时，Provider 构造请求头包含 `X-Api-Key`、`X-Api-Resource-Id`、`X-Api-Request-Id`。
- Provider 能解析火山单向流式返回的音频帧并拼接为试听音频。
- `python -m backend.scripts.test_character` 和 `python -m backend.scripts.test_parent_api` 不回归。

## 验收标准

- 本地无凭证可继续完整跑通 mock 声音流程。
- 配置 `VOLC_TTS_API_KEY` 后，使用已就绪的 `speaker_id` 能生成真实试听音频。
- 不影响老人端实时通话、角色同步、人格蒸馏和隐私边界。
