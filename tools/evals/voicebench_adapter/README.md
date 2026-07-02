# 小暖 VoiceBench Adapter

该适配器把本地 wav 样本发送到小暖老人端 WebSocket，并输出 VoiceBench 风格 JSONL。

## 输入 manifest

每行一个 JSON：

```json
{"id": "sample-001", "audio_path": "/absolute/path/to/sample.wav", "subset": "smoke"}
```

音频要求：

- wav
- 16kHz
- mono
- 16-bit PCM

## 运行

```bash
.venv/bin/python -m tools.evals.voicebench_adapter.run_manifest \
  --base-url http://127.0.0.1:8000 \
  --elder-id eval-elder \
  --manifest path/to/manifest.jsonl \
  --output artifacts/evals/voicebench/xiaonuan-results.jsonl
```

输出示例：

```json
{"id":"sample-001","subset":"smoke","model":"xiaonuan","response":"小暖回复","metadata":{"audio_bytes":11520,"barge_in":1}}
```

如后端开启 `AUTH_REQUIRED=1`，传入：

```bash
--family-token "$FAMILY_API_TOKEN"
```
