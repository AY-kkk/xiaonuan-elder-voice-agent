// 24kHz PCM 流式播放器：维护一个时间游标，把后端下发的 Int16 PCM
// 依次排进 AudioContext 时间轴，保证连续平滑播放；支持 barge-in 立即清空。
export class PcmPlayer {
  constructor(sampleRate = 24000) {
    this._ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate });
    this._sampleRate = sampleRate;
    this._cursor = 0;
    this._sources = new Set();
  }

  async resume() {
    if (this._ctx.state === "suspended") await this._ctx.resume();
  }

  enqueue(int16Buffer) {
    const int16 = new Int16Array(int16Buffer);
    if (int16.length === 0) return;
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 0x8000;

    const buf = this._ctx.createBuffer(1, f32.length, this._sampleRate);
    buf.copyToChannel(f32, 0);
    const src = this._ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this._ctx.destination);

    const now = this._ctx.currentTime;
    if (this._cursor < now) this._cursor = now;
    src.start(this._cursor);
    this._cursor += buf.duration;

    this._sources.add(src);
    src.onended = () => this._sources.delete(src);
  }

  // barge-in：立即停播并清空已排队音频
  clear() {
    for (const src of this._sources) {
      try { src.stop(); } catch (_) {}
    }
    this._sources.clear();
    this._cursor = this._ctx.currentTime;
  }

  get isPlaying() {
    return this._sources.size > 0;
  }
}
