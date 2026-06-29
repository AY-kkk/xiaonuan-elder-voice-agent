(() => {
  "use strict";

  const KEY = "xiaonuan_splash_seen_v1";
  const params = new URLSearchParams(location.search);
  const force = params.get("splash") === "1";
  const reset = params.get("splash") === "reset";
  const durationMs = 5000;

  try {
    if (reset) localStorage.removeItem(KEY);
    if (!force && localStorage.getItem(KEY) === "1") return;
    localStorage.setItem(KEY, "1");
  } catch (_) {
    if (!force) return;
  }

  const style = document.createElement("style");
  style.textContent = `
    .xn-splash {
      position: fixed;
      inset: 0;
      z-index: 9999;
      background: #fff;
      display: grid;
      place-items: center;
      overflow: hidden;
      color: #1C1A17;
      font-family: var(--font-zh, "PingFang SC", -apple-system, system-ui, sans-serif);
      animation: xn-splash-fade-out .46s ease ${durationMs - 460}ms forwards;
    }
    .xn-splash[aria-hidden="true"] {
      display: none;
    }
    .xn-splash-stage {
      width: min(100vw, 390px);
      min-height: 360px;
      display: grid;
      place-items: center;
      transform: translateY(-2vh);
    }
    .xn-splash-lockup {
      display: grid;
      place-items: center;
      gap: 24px;
    }
    .xn-splash-mascot {
      --m-size: 168px;
      position: relative !important;
      top: auto !important;
      left: auto !important;
      width: var(--m-size) !important;
      height: var(--m-size) !important;
      opacity: 1 !important;
      filter: drop-shadow(0 18px 28px rgba(28, 26, 23, .16));
      animation:
        xn-mascot-slide-jump 2.15s cubic-bezier(.2, .9, .2, 1) both,
        xn-mascot-soft-bob 1.85s ease-in-out 2.15s infinite;
      transform-origin: 50% 92%;
    }
    .xn-splash-title {
      min-height: 44px;
      font-size: clamp(28px, 8vw, 36px);
      line-height: 1.18;
      font-weight: 900;
      letter-spacing: 0;
      text-align: center;
      opacity: 0;
      transform: translateY(14px);
      animation: xn-slogan-reveal .72s ease 1.62s forwards;
    }
    .xn-splash-skip {
      position: fixed;
      right: 16px;
      top: 16px;
      min-height: 36px;
      border: none;
      border-radius: 999px;
      padding: 8px 14px;
      background: rgba(28, 26, 23, .06);
      color: #5A5249;
      font: inherit;
      font-size: 14px;
      cursor: pointer;
    }
    @keyframes xn-mascot-slide-jump {
      0% { transform: translateX(-120vw) translateY(0) scale(.94); }
      18% { transform: translateX(4px) translateY(0) scale(1.02); }
      28% { transform: translateX(0) translateY(-28px) scale(1.04, .96); }
      39% { transform: translateX(0) translateY(0) scale(.98, 1.04); }
      50% { transform: translateX(0) translateY(-12px) scale(1.015, .985); }
      60%, 100% { transform: translateX(0) translateY(0) scale(1); }
    }
    @keyframes xn-mascot-soft-bob {
      0%, 100% { transform: translateY(0) scale(1); }
      50% { transform: translateY(-5px) scale(1.012); }
    }
    @keyframes xn-slogan-reveal {
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes xn-splash-fade-out {
      to { opacity: 0; visibility: hidden; }
    }
    @media (prefers-reduced-motion: reduce) {
      .xn-splash,
      .xn-splash-mascot,
      .xn-splash-title {
        animation-duration: .01ms !important;
        animation-delay: 0ms !important;
      }
    }
  `;

  const splash = document.createElement("div");
  splash.className = "xn-splash";
  splash.setAttribute("role", "img");
  splash.setAttribute("aria-label", "小暖开屏动画");
  splash.innerHTML = `
    <button class="xn-splash-skip" type="button" aria-label="跳过开屏动画">跳过</button>
    <div class="xn-splash-stage">
      <div class="xn-splash-lockup">
        <div class="mascot is-idle xn-splash-mascot" aria-hidden="true">
          <span class="body"></span><span class="hand"></span>
          <span class="eye l"></span><span class="eye r"></span><span class="mouth"></span>
        </div>
        <div class="xn-splash-title">小暖，陪你唠唠！</div>
      </div>
    </div>
  `;

  function closeSplash() {
    splash.style.animation = "xn-splash-fade-out .22s ease forwards";
    window.setTimeout(() => splash.remove(), 260);
  }

  document.head.appendChild(style);
  document.body.prepend(splash);
  splash.querySelector(".xn-splash-skip").addEventListener("click", closeSplash);
  window.setTimeout(() => splash.remove(), durationMs + 80);
})();
