(() => {
  "use strict";

  const ELDER_ID = "elder-001";

  function apiBase() {
    return ((window.APP_CONFIG && window.APP_CONFIG.API_BASE) || "").trim();
  }

  function parentApi(path) {
    const cleanPath = String(path || "").replace(/^\/+/, "");
    return `${apiBase()}/api/parent/${cleanPath}`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function toast(message) {
    const node = document.getElementById("toast");
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
    window.setTimeout(() => node.classList.remove("show"), 2000);
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : "请求失败";
      throw new Error(detail);
    }
    return payload || {};
  }

  function formatTime(timestamp) {
    return new Date(Number(timestamp || 0) * 1000).toLocaleString("zh-CN", {
      hour12: false,
    });
  }

  function levelText(level) {
    return { normal: "良好", attention: "需关注", urgent: "紧急" }[level] || level || "未知";
  }

  function statusText(status) {
    return { active: "已启用", pending: "待确认", archived: "已归档" }[status] || status || "未知";
  }

  function yuan(cents) {
    return `¥${(Number(cents || 0) / 100).toFixed(2)}`;
  }

  function daysLeft(timestamp) {
    if (!timestamp) return "长期有效";
    const leftDays = Math.ceil((Number(timestamp) * 1000 - Date.now()) / 86400000);
    return leftDays > 0 ? `${leftDays} 天后过期` : "已过期";
  }

  function roleStateText(role) {
    if (role.human_status) return role.human_status;
    const voice = {
      none: "声音未准备",
      training: "声音正在准备",
      ready: "声音已准备好",
      failed: "声音准备失败",
    }[role.voice_status] || "声音未准备";
    const persona = role.persona_status === "ready" ? "说话方式已生成" : "说话方式未生成";
    const sync = {
      draft: "还未同步",
      ready: "可以同步给老人端",
      synced: "老人端已可选择",
      active: "正在作为通话对象",
      failed: "需要重新准备",
    }[role.sync_status] || "还未同步";
    return `${voice} · ${persona} · ${sync}`;
  }

  window.ParentShared = {
    ELDER_ID,
    parentApi,
    escapeHtml,
    toast,
    fetchJson,
    formatTime,
    levelText,
    statusText,
    yuan,
    daysLeft,
    roleStateText,
  };
})();
