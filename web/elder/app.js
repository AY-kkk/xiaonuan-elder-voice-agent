// 适老端主逻辑：建连后端 WS、采集上行 PCM、播放下行 TTS、处理打断与字幕。
import { PcmPlayer } from "./player.js";

const ELDER_ID = "elder-001"; // MVP 单家庭，固定 ID
const statusEl = document.getElementById("status");
const btn = document.getElementById("talk-btn");
const subtitleEl = document.getElementById("subtitle");

let ws = null;
let audioCtx = null;
let micStream = null;
let workletNode = null;
let player = null;
let live = false;

// 本地 VAD 即时打断：Agent 出声时，连续多帧高能量即判定老人插话，立刻本地停播，
// 不等服务端往返（达成 ≤300ms）。阈值偏保守，配合浏览器 AEC 降低自播误触发。
const VAD_RMS_THRESHOLD = 0.04;
const VAD_TRIGGER_FRAMES = 4; // 连续 4 帧 ≈ 80ms 才算开口，过滤瞬时噪声
let vadVoiceFrames = 0;
let bargedLocally = false; // 本轮已本地打断，避免重复 clear
// 音频门闩（precise stop chain）：打断后关闸丢弃在途旧轮残尾，
// 直到下一帧文本（新一轮回复开始）才重新开闸。开闸信号用可靠的文本帧而非乱序音频帧。
let acceptingAudio = true;

function setStatus(text) { statusEl.textContent = text; }
function showText(role, text) {
  const who = role === "user" ? "我" : "TA";
  const cls = role === "user" ? "me" : "ta";
  subtitleEl.innerHTML = `<span class="${cls}">${who}：</span>${text}`;
}

function wsUrl() {
  // API_BASE 为空=同源；非空=分离部署，从配置地址推导 ws/wss 与 host。
  const base = (window.APP_CONFIG && window.APP_CONFIG.API_BASE || "").trim();
  if (base) {
    const u = new URL(base);
    const proto = u.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${u.host}/ws/elder/${ELDER_ID}`;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/elder/${ELDER_ID}`;
}

async function start() {
  setStatus("正在连接…");
  btn.disabled = true;

  player = new PcmPlayer(24000);
  await player.resume();

  // 麦克风采集 + AEC（回声消除，防止 TTS 自播被回采误触发打断）
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  await audioCtx.audioWorklet.addModule("./capture-worklet.js");
  const srcNode = audioCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioCtx, "capture-processor");
  srcNode.connect(workletNode);
  workletNode.port.onmessage = (e) => {
    const { pcm, rms } = e.data;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm);
    localVad(rms);
  };

  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
  ws.onopen = () => setStatus("接通啦，您说话吧");
  ws.onmessage = onMessage;
  ws.onclose = () => { setStatus("已挂断"); cleanupAfterStop(); };
  ws.onerror = () => setStatus("连接出错了，请再试一次");

  live = true;
  btn.disabled = false;
  btn.classList.add("live");
  btn.innerHTML = "挂断";
}

function onMessage(e) {
  if (e.data instanceof ArrayBuffer) {
    if (acceptingAudio) player.enqueue(e.data); // 关闸期间丢弃旧轮残尾
    return;
  }
  let msg;
  try { msg = JSON.parse(e.data); } catch (_) { return; }
  if (msg.type === "barge_in") {
    interrupt(); // 服务端打断信号兜底：本地未触发时由此停播
  } else if (msg.type === "text") {
    // 文本推进 = 新一轮回复开始：重新开闸并允许再次本地打断。
    acceptingAudio = true;
    bargedLocally = false;
    showText(msg.role, msg.text);
  } else if (msg.type === "status") {
    if (msg.status === "connected") setStatus("接通啦，您说话吧");
    else if (msg.status === "error") setStatus("出了点小问题，请再试一次");
    else if (msg.status === "ended") setStatus("已挂断");
  }
}

// 统一打断：停播 + 关音频门闩丢弃旧轮在途残尾。本地 VAD 与服务端 barge_in 共用。
function interrupt() {
  player.clear();
  bargedLocally = true;
  acceptingAudio = false;
  vadVoiceFrames = 0;
}

// 本地 VAD：仅在 Agent 出声时生效；连续高能量帧达阈值即立刻停播。
function localVad(rms) {
  if (!player || !player.isPlaying || bargedLocally) {
    vadVoiceFrames = 0;
    return;
  }
  if (rms >= VAD_RMS_THRESHOLD) {
    vadVoiceFrames += 1;
    if (vadVoiceFrames >= VAD_TRIGGER_FRAMES) {
      interrupt();
    }
  } else {
    vadVoiceFrames = 0;
  }
}

function stop() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "hangup" }));
    ws.close();
  }
  cleanupAfterStop();
}

function cleanupAfterStop() {
  live = false;
  btn.classList.remove("live");
  btn.innerHTML = "开始<br/>说话";
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
  if (player) { player.clear(); }
}

btn.addEventListener("click", () => {
  if (live) stop();
  else start().catch((err) => { setStatus("无法使用麦克风：" + err.message); btn.disabled = false; });
});
