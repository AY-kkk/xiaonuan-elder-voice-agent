// 麦克风采集 Worklet：把浏览器原生采样率的 mono 音频下采样到 16kHz，
// 累积成 20ms（320 采样）整帧后以 Int16 PCM 抛回主线程。
// 每帧附带 RMS 能量，供主线程做本地 VAD 即时打断（barge-in 提速到 ≤300ms）。
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / 16000; // 输入采样率 / 目标采样率
    this._acc = [];
    this._pos = 0;
    this._frame = 320; // 20ms @16k
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    // 线性下采样
    for (let i = 0; i < ch.length; i++) {
      this._pos += 1;
      if (this._pos >= this._ratio) {
        this._pos -= this._ratio;
        this._acc.push(ch[i]);
      }
    }
    while (this._acc.length >= this._frame) {
      const slice = this._acc.splice(0, this._frame);
      const pcm = new Int16Array(this._frame);
      let sumSq = 0;
      for (let i = 0; i < this._frame; i++) {
        const s = Math.max(-1, Math.min(1, slice[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        sumSq += s * s;
      }
      const rms = Math.sqrt(sumSq / this._frame);
      this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
