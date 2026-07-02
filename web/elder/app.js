// 适老端主逻辑：建连后端 WS、采集上行 PCM、播放下行 TTS、处理打断与字幕。
// UI 为「半圆进度环」四态（待机/连接中/通话中/出错）+ 动态吉祥物两态联动。
import { PcmPlayer } from "./player.js";

const ELDER_ID = "elder-001"; // MVP 单家庭，固定 ID
const btn = document.getElementById("talk-btn");
const statusEl = document.getElementById("status");
const hintEl = document.getElementById("status-hint");
const textEl = document.getElementById("talk-text");
const timerEl = document.getElementById("talk-timer");
const subtitleEl = document.getElementById("subtitle");
const vadStateEl = document.getElementById("vad-state");
const familyNoticeEl = document.getElementById("family-notice");
const noticeTextEl = document.getElementById("notice-text");
const noticeAcceptBtn = document.getElementById("notice-accept");
const noticeLaterBtn = document.getElementById("notice-later");
const greetingFromEl = document.getElementById("greeting-from");
const greetingTitleEl = document.getElementById("greeting-title");
const greetingTextEl = document.getElementById("greeting-text");
const playGreetingBtn = document.getElementById("play-greeting");
const quietModeBtn = document.getElementById("quiet-mode");
const quietStateEl = document.getElementById("quiet-state");
const emergencyCallEl = document.getElementById("emergency-call");
const emergencyTextEl = document.getElementById("emergency-text");
const fallbackCardEl = document.getElementById("fallback-card");
const fallbackTextEl = document.getElementById("fallback-text");
const fallbackPlayBtn = document.getElementById("fallback-play");
const medMainEl = document.getElementById("med-main");
const medSubEl = document.getElementById("med-sub");
const medConfirmBtn = document.getElementById("med-confirm");

let ws = null;
let audioCtx = null;
let micStream = null;
let workletNode = null;
let player = null;
let live = false;
let timerId = null, seconds = 0;
let todayCare = null;
let quietMode = localStorage.getItem("xiaonuan_quiet_mode") === "1";
let quietReminderTimer = null;
let quietReminderIndex = Number(localStorage.getItem("xiaonuan_quiet_reminder_index") || "0");
let currentMedicationId = null;
let emergencyContacts = [];
let serviceWorkerRegistration = null;

// 本地 VAD 即时打断：Agent 出声时连续多帧高能量即判定老人插话，立刻本地停播。
const VAD_BASE_RMS_THRESHOLD = 0.04;
const VAD_TRIGGER_FRAMES = 5;
const VAD_CALIBRATION_FRAMES = 50; // 约 1 秒，用于估计当前房间/设备噪声底
const VAD_COOLDOWN_MS = 900;
const VAD_ECHO_GRACE_MS = 180;
let vadVoiceFrames = 0;
let bargedLocally = false;
let vadNoiseFloor = 0.01;
let vadCalibrationFrames = 0;
let lastInterruptAt = 0;
let agentAudioSince = 0;
// 音频门闩（precise stop chain）：打断后关闸丢弃在途旧轮残尾，下一帧文本才重新开闸。
let acceptingAudio = true;

// 四态文案
const COPY = {
  idle:       { title: "轻触和家人说说话", hint: "我随时在，想聊就点下面的大圆圈", btn: "点击通话" },
  connecting: { title: "正在为你接通…",     hint: "马上就好，请稍等一下",         btn: "连接中…" },
  talking:    { title: "正在通话中",         hint: "想说什么就慢慢说，我在听",     btn: "" },
  error:      { title: "没接通，再试一次",   hint: "网络好像不太稳，点圆圈重拨",   btn: "重新拨打" },
};

// 设定 UI 状态：通话按钮 data-state + 文案 + 吉祥物两态 + 计时器
function setUiState(state) {
  btn.dataset.state = state;
  const c = COPY[state];
  statusEl.textContent = c.title;
  hintEl.textContent = c.hint;
  if (state === "idle" && quietMode) {
    hintEl.textContent = "小暖会安静陪着，你想说话时再点大圆圈";
  }
  textEl.textContent = c.btn;
  // 吉祥物：仅通话中放大到中央，其余回到右上角 idle（CSS 控制过渡）
  const mascot = document.querySelector(".mascot");
  if (state === "talking") {
    hideFallback();
    document.body.dataset.mascot = "talking";
    mascot.classList.remove("is-idle"); mascot.classList.add("is-talking");
  } else {
    document.body.dataset.mascot = "idle";
    mascot.classList.remove("is-talking"); mascot.classList.add("is-idle");
  }
  // 计时器
  clearInterval(timerId);
  if (state === "talking") {
    seconds = 0; timerEl.textContent = "00:00";
    timerId = setInterval(() => {
      seconds++;
      const m = String(Math.floor(seconds / 60)).padStart(2, "0");
      const s = String(seconds % 60).padStart(2, "0");
      timerEl.textContent = `${m}:${s}`;
    }, 1000);
  }
}

function showText(role, text) {
  const who = role === "user" ? "我" : "TA";
  const cls = role === "user" ? "me" : "ta";
  subtitleEl.innerHTML = `<span class="${cls}">${who}：</span>${escapeHtml(text)}`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function apiBase() {
  return (window.APP_CONFIG && window.APP_CONFIG.API_BASE || "").trim();
}

function apiUrl(path) {
  return withFamilyToken(`${apiBase()}${path}`);
}

function familyToken() {
  return ((window.APP_CONFIG && window.APP_CONFIG.FAMILY_TOKEN) || "").trim();
}

function withFamilyToken(url) {
  const token = familyToken();
  let sessionToken = "";
  try { sessionToken = localStorage.getItem("xiaonuan_session_token") || ""; } catch (_) {}
  if (!token && !sessionToken) return url;
  const join = url.includes("?") ? "&" : "?";
  if (token) return `${url}${join}family_token=${encodeURIComponent(token)}`;
  return `${url}${join}session_token=${encodeURIComponent(sessionToken)}`;
}

function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

async function logAction(actionType, detail = "", targetType = "", targetId = null) {
  try {
    await fetch(apiUrl(`/api/elder/${ELDER_ID}/actions`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_type: actionType,
        target_type: targetType,
        target_id: targetId,
        detail,
      }),
    });
  } catch (_) {
    // 行为日志失败不能打断老人端主流程。
  }
}

function wsUrl() {
  const base = apiBase();
  if (base) {
    const u = new URL(base);
    const proto = u.protocol === "https:" ? "wss" : "ws";
    return withFamilyToken(`${proto}://${u.host}/ws/elder/${ELDER_ID}`);
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return withFamilyToken(`${proto}://${location.host}/ws/elder/${ELDER_ID}`);
}

// 启动前健康预检：若后端引擎为 seeduplex 但语音凭证未就绪，提前给出友好提示，
// 避免用户点了通话后卡在「正在接通…」（凭证缺失时建连必失败）。
// 返回 true 表示可继续建连；false 表示已提示、不应继续。
async function precheckReady() {
  try {
    const r = await fetch(`${apiBase()}/healthz`);
    if (!r.ok) return true; // 健康检查不可用时不阻断，交由后续建连兜底
    const h = await r.json();
    if (h.engine === "seeduplex" && h.volc_ready === false) {
      setUiState("error");
      hintEl.textContent = "语音服务还没配置好，请联系家人帮忙设置";
      logAction("call_precheck_failed", "voice_credentials_missing");
      return false;
    }
  } catch (_) {
    // 预检失败不阻断，让真正的建连流程去暴露问题
  }
  return true;
}

async function loadCompanionNotice() {
  if (!familyNoticeEl) return;
  try {
    const data = await fetch(apiUrl(`/api/elder/${ELDER_ID}/companions`)).then((x) => x.json());
    if (!data.notice) return;
    familyNoticeEl.dataset.characterId = data.notice.character_id;
    noticeTextEl.textContent = data.notice.text;
    familyNoticeEl.classList.add("show");
  } catch (_) {
    // 陪伴提示失败不影响老人一键通话。
  }
}

async function loadTodayCare() {
  try {
    todayCare = await fetch(apiUrl(`/api/elder/${ELDER_ID}/today`)).then((x) => x.json());
    renderTodayCare(todayCare);
  } catch (_) {
    todayCare = {
      greeting: {
        from: "家人",
        title: "家人给你留了一句话",
        text: "今天别着急，慢慢来。想聊天就点小暖，我们都惦记着你。",
      },
      medication: {
        text: "今天先照顾好自己，按平时习惯吃饭、喝水、休息。",
        sub: "有不舒服就点上面和小暖说",
      },
      emergency: { enabled: false, text: "家人还没设置电话。", phone: "" },
      fallback: { text: "今天别着急，慢慢来。想聊天就点小暖。" },
    };
    renderTodayCare(todayCare);
  }
}

function renderTodayCare(data) {
  const greeting = data.greeting || {};
  greetingFromEl.textContent = greeting.from ? `${greeting.from}问候` : "家人问候";
  greetingTitleEl.textContent = greeting.title || "家人给你留了一句话";
  greetingTextEl.textContent = greeting.text || "今天别着急，慢慢来。";
  const medication = data.medication || {};
  medMainEl.textContent = medication.text || "今天先照顾好自己";
  medSubEl.textContent = medication.sub || "有不舒服就点上面和小暖说";
  currentMedicationId = medication.confirmable ? medication.id : null;
  const takenKey = currentMedicationId ? `xiaonuan_med_taken_${ELDER_ID}_${currentMedicationId}` : "";
  const alreadyTaken = takenKey && localStorage.getItem(takenKey) === new Date().toISOString().slice(0, 10);
  if (currentMedicationId && medConfirmBtn) {
    medConfirmBtn.hidden = false;
    medConfirmBtn.textContent = alreadyTaken ? "今天已记录" : "我已吃药";
    medConfirmBtn.classList.toggle("done", Boolean(alreadyTaken));
    medConfirmBtn.disabled = Boolean(alreadyTaken);
  } else if (medConfirmBtn) {
    medConfirmBtn.hidden = true;
  }
  const emergency = data.emergency || {};
  emergencyContacts = Array.isArray(emergency.contacts) ? emergency.contacts : [];
  if (emergency.enabled && emergency.phone) {
    emergencyCallEl.href = `tel:${emergency.phone}`;
    emergencyTextEl.textContent = emergencyContacts.length > 1 ? `${emergencyContacts.length} 位家人` : "现在拨打";
    emergencyCallEl.removeAttribute("aria-disabled");
  } else {
    emergencyCallEl.href = "#";
    emergencyTextEl.textContent = "请家人设置";
    emergencyCallEl.setAttribute("aria-disabled", "true");
  }
  fallbackTextEl.textContent = (data.fallback && data.fallback.text) || greetingTextEl.textContent;
  renderQuietMode();
  scheduleQuietCompanion();
}

function renderQuietMode() {
  quietModeBtn.classList.toggle("on", quietMode);
  quietStateEl.textContent = quietMode ? "已打开" : "轻轻陪着";
  if (quietMode) {
    hintEl.textContent = "小暖会安静陪着，你想说话时再点大圆圈";
  }
}

function speak(text) {
  const content = (text || "").trim();
  if (!content || !window.speechSynthesis) {
    toast("这个浏览器暂时不能朗读");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(content);
  utterance.lang = "zh-CN";
  utterance.rate = 0.92;
  utterance.pitch = 1.0;
  window.speechSynthesis.speak(utterance);
}

async function registerPwa() {
  if (!("serviceWorker" in navigator)) return;
  try {
    serviceWorkerRegistration = await navigator.serviceWorker.register("./sw.js");
  } catch (_) {
    serviceWorkerRegistration = null;
  }
}

async function ensureNotificationPermission() {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const permission = await Notification.requestPermission();
  return permission === "granted";
}

async function showReminderNotification(text) {
  if (!(await ensureNotificationPermission())) return false;
  const title = "小暖提醒";
  const options = { body: text, tag: "xiaonuan-care-reminder" };
  if (serviceWorkerRegistration) {
    await serviceWorkerRegistration.showNotification(title, options);
  } else {
    new Notification(title, options);
  }
  return true;
}

async function confirmMedicationTaken() {
  if (!currentMedicationId) return;
  try {
    const response = await fetch(apiUrl(`/api/elder/${ELDER_ID}/medications/${currentMedicationId}/taken`), {
      method: "POST",
    });
    if (!response.ok) throw new Error("confirm_failed");
    localStorage.setItem(
      `xiaonuan_med_taken_${ELDER_ID}_${currentMedicationId}`,
      new Date().toISOString().slice(0, 10),
    );
    medConfirmBtn.textContent = "今天已记录";
    medConfirmBtn.classList.add("done");
    medConfirmBtn.disabled = true;
    toast("已告诉家人");
  } catch (_) {
    toast("暂时没记上，请等会儿再点");
  }
}

function confirmEmergencyCall(event) {
  if (emergencyCallEl.getAttribute("aria-disabled") === "true") {
    event.preventDefault();
    hintEl.textContent = "请让家人在子女端添加紧急联系人电话";
    toast("还没有电话");
    return;
  }
  const first = emergencyContacts[0] || { name: "家人", phone: emergencyCallEl.href.replace(/^tel:/, "") };
  const message = `要现在拨打${first.name}${first.phone ? `（${first.phone}）` : ""}吗？`;
  if (!window.confirm(message)) {
    event.preventDefault();
    logAction("emergency_call_cancelled", first.name || "", "contact", first.id || null);
    return;
  }
  logAction("emergency_call_clicked", `${first.name} ${first.phone || ""}`.trim(), "contact", first.id || null);
}

function showFallback() {
  if (!fallbackCardEl) return;
  fallbackCardEl.classList.add("show");
}

function hideFallback() {
  if (!fallbackCardEl) return;
  fallbackCardEl.classList.remove("show");
}

function scheduleQuietCompanion() {
  clearTimeout(quietReminderTimer);
  quietReminderTimer = null;
  if (!quietMode || !todayCare || live) return;
  const policy = todayCare.quiet_companion || {};
  const firstDelay = Math.max(30, Number(policy.first_prompt_after_seconds || 90)) * 1000;
  quietReminderTimer = setTimeout(runQuietReminder, firstDelay);
}

function runQuietReminder() {
  clearTimeout(quietReminderTimer);
  quietReminderTimer = null;
  if (!quietMode || live) {
    scheduleQuietCompanion();
    return;
  }
  const reminders = (
    todayCare &&
    todayCare.quiet_companion &&
    Array.isArray(todayCare.quiet_companion.reminders)
  ) ? todayCare.quiet_companion.reminders : [];
  const fallback = fallbackTextEl.textContent || greetingTextEl.textContent;
  const item = reminders.length ? reminders[quietReminderIndex % reminders.length] : { text: fallback };
  quietReminderIndex += 1;
  localStorage.setItem("xiaonuan_quiet_reminder_index", String(quietReminderIndex));
  const text = item.text || fallback;
  if (document.hidden) {
    showReminderNotification(text);
  } else {
    showText("assistant", text);
    speak(text);
  }
  const interval = Math.max(
    300,
    Number(todayCare.quiet_companion && todayCare.quiet_companion.reminder_interval_seconds || 1800)
  ) * 1000;
  quietReminderTimer = setTimeout(runQuietReminder, interval);
}

async function activateNoticeCompanion() {
  const characterId = familyNoticeEl && familyNoticeEl.dataset.characterId;
  if (!characterId) return;
  try {
    await fetch(apiUrl(`/api/elder/${ELDER_ID}/companions/${characterId}/activate`), { method: "POST" });
    familyNoticeEl.classList.remove("show");
    hintEl.textContent = "好啦，今天就让 TA 陪你说说话";
  } catch (_) {
    hintEl.textContent = "暂时没换成，等家人再帮你看看";
  }
}

async function dismissNoticeCompanion() {
  const characterId = familyNoticeEl && familyNoticeEl.dataset.characterId;
  familyNoticeEl.classList.remove("show");
  if (!characterId) return;
  try {
    await fetch(apiUrl(`/api/elder/${ELDER_ID}/companions/${characterId}/notice_seen`), { method: "POST" });
  } catch (_) {}
}

async function start() {
  clearTimeout(quietReminderTimer);
  quietReminderTimer = null;
  setUiState("connecting");
  btn.disabled = true;

  // 凭证预检：缺语音凭证时直接友好提示，不进入卡死的连接流程
  if (!(await precheckReady())) {
    btn.disabled = false;
    return;
  }

  try {
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
      calibrateVad(rms);
      localVad(rms);
    };

    await openSocket();
    live = true;
    btn.disabled = false;
  } catch (err) {
    // 任一环节失败（麦克风被拒/无设备/Worklet 加载失败/建连失败/超时）：
    // 彻底清理已建资源，回到可重试态，给适老化友好提示。
    failStart(err);
  }
}

// 建连 WebSocket，以 Promise 等待 open/error/超时，便于 start() 统一捕获失败。
function openSocket() {
  return new Promise((resolve, reject) => {
    let settled = false;
    ws = new WebSocket(wsUrl());
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { settled = true; setUiState("talking"); resolve(); };
    ws.onmessage = onMessage;
    ws.onclose = () => {
      if (!settled) { settled = true; reject(new Error("connect_closed")); return; }
      if (live) {
        setUiState("error");
        hintEl.textContent = "通话断开了，点大圆圈可以重新接通";
        showFallback();
        logAction("call_disconnected", "websocket_closed");
      }
      cleanupAfterStop();
    };
    ws.onerror = () => {
      if (!settled) { settled = true; reject(new Error("connect_error")); }
      else setUiState("error");
    };
    // 建连超时兜底：8s 未 open 视为失败，避免无限期卡在「接通中」
    setTimeout(() => { if (!settled) { settled = true; reject(new Error("connect_timeout")); } }, 8000);
  });
}

// 启动失败统一收口：清理资源 + 适老化友好提示 + 恢复可重试。
function failStart(err) {
  const msg = String((err && err.name) || (err && err.message) || err);
  cleanupAfterStop();
  if (ws) { try { ws.close(); } catch (_) {} ws = null; }
  setUiState("error");
  if (msg.includes("NotAllowed") || msg.includes("Permission")) {
    hintEl.textContent = "请在浏览器弹窗里点允许麦克风，然后再点大圆圈";
  } else if (msg.includes("NotFound") || msg.includes("Devices")) {
    hintEl.textContent = "没有找到麦克风，请检查设备后再试";
  } else if (msg.includes("connect")) {
    hintEl.textContent = "没连上，请检查网络后点圆圈重试";
  } else {
    hintEl.textContent = "出了点小问题，请点圆圈再试一次";
  }
  logAction("call_failed", msg);
  showFallback();
  btn.disabled = false;
}

function onMessage(e) {
  if (e.data instanceof ArrayBuffer) {
    if (acceptingAudio && player) {
      player.enqueue(e.data); // player 已清理则丢弃
      markAgentAudio();
    }
    return;
  }
  let msg;
  try { msg = JSON.parse(e.data); } catch (_) { return; }
  if (msg.type === "barge_in") {
    interrupt();
  } else if (msg.type === "text") {
    acceptingAudio = true;
    bargedLocally = false;
    if (btn.dataset.state !== "talking") setUiState("talking");
    showText(msg.role, msg.text);
  } else if (msg.type === "status") {
    if (msg.status === "connected") setUiState("talking");
    else if (msg.status === "error") setUiState("error");
    else if (msg.status === "ended") { setUiState("idle"); }
  }
}

// 统一打断：停播 + 关音频门闩丢弃旧轮在途残尾。
function interrupt() {
  if (player) player.clear();
  bargedLocally = true;
  acceptingAudio = false;
  vadVoiceFrames = 0;
  lastInterruptAt = Date.now();
  updateVadState("已为插话停播");
}

function localVad(rms) {
  if (!player || !player.isPlaying || bargedLocally) { vadVoiceFrames = 0; return; }
  const now = Date.now();
  if (now - lastInterruptAt < VAD_COOLDOWN_MS || now - agentAudioSince < VAD_ECHO_GRACE_MS) {
    vadVoiceFrames = 0;
    return;
  }
  if (rms >= currentVadThreshold()) {
    vadVoiceFrames += 1;
    if (vadVoiceFrames >= VAD_TRIGGER_FRAMES) interrupt();
  } else {
    vadVoiceFrames = 0;
  }
}

function calibrateVad(rms) {
  if (!Number.isFinite(rms)) return;
  if (player && player.isPlaying) return;
  if (vadCalibrationFrames < VAD_CALIBRATION_FRAMES) {
    vadNoiseFloor = vadNoiseFloor * 0.9 + rms * 0.1;
    vadCalibrationFrames += 1;
    if (vadCalibrationFrames === VAD_CALIBRATION_FRAMES) updateVadState("回声保护已开启");
    return;
  }
  // 空闲期缓慢跟随环境噪声，避免电视声/风扇声导致阈值长期不匹配。
  vadNoiseFloor = vadNoiseFloor * 0.98 + Math.min(rms, 0.08) * 0.02;
}

function currentVadThreshold() {
  return Math.max(VAD_BASE_RMS_THRESHOLD, vadNoiseFloor * 4.5);
}

function markAgentAudio() {
  if (!agentAudioSince) updateVadState("回声保护已开启");
  agentAudioSince = Date.now();
}

function updateVadState(text) {
  if (vadStateEl) vadStateEl.textContent = text;
}

function stop() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "hangup" }));
    ws.close();
  }
  setUiState("idle");
  cleanupAfterStop();
  scheduleQuietCompanion();
}

function cleanupAfterStop() {
  live = false;
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
  if (player) { player.clear(); player = null; }
  vadVoiceFrames = 0;
  bargedLocally = false;
  acceptingAudio = true;
  agentAudioSince = 0;
  updateVadState("回声保护待开启");
}

// 通话按钮：通话中→挂断；空闲→发起通话（失败已由 start() 内部收口）。
btn.addEventListener("click", () => {
  if (live) stop();
  else start();
});

if (noticeAcceptBtn) noticeAcceptBtn.addEventListener("click", activateNoticeCompanion);
if (noticeLaterBtn) noticeLaterBtn.addEventListener("click", dismissNoticeCompanion);
if (playGreetingBtn) {
  playGreetingBtn.addEventListener("click", () => {
    speak(greetingTextEl.textContent);
    logAction("greeting_played", greetingTextEl.textContent);
  });
}
if (fallbackPlayBtn) {
  fallbackPlayBtn.addEventListener("click", () => {
    speak(fallbackTextEl.textContent);
    logAction("fallback_played", fallbackTextEl.textContent);
  });
}
if (quietModeBtn) {
  quietModeBtn.addEventListener("click", () => {
    quietMode = !quietMode;
    localStorage.setItem("xiaonuan_quiet_mode", quietMode ? "1" : "0");
    renderQuietMode();
    scheduleQuietCompanion();
    if (quietMode) ensureNotificationPermission();
    toast(quietMode ? "安心陪伴已打开" : "安心陪伴已关闭");
  });
}
if (emergencyCallEl) {
  emergencyCallEl.addEventListener("click", confirmEmergencyCall);
}
if (medConfirmBtn) medConfirmBtn.addEventListener("click", confirmMedicationTaken);
loadTodayCare();
loadCompanionNotice();
registerPwa();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) scheduleQuietCompanion();
});
