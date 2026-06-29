(() => {
  "use strict";

  const tabs = [
    {
      id: "signals",
      label: "信号",
      href: "./index.html",
      icon: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    },
    {
      id: "facts",
      label: "事项",
      href: "./facts.html",
      icon: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    },
    {
      id: "characters",
      label: "角色",
      href: "./characters.html",
      icon: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    },
    {
      id: "wallet",
      label: "充值",
      href: "./wallet.html",
      icon: '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    },
    {
      id: "profile",
      label: "我的",
      href: "./profile.html",
      icon: '<circle cx="12" cy="8" r="4"/><path d="M6 21v-2a6 6 0 0 1 12 0v2"/>',
    },
  ];

  function mountParentNav() {
    const target = document.getElementById("parent-nav");
    if (!target) return;
    const activePage = document.body.dataset.parentPage || "signals";
    target.innerHTML = `
      <nav class="tabbar" aria-label="主导航">
        ${tabs.map((tab) => `
          <a class="tab ${tab.id === activePage ? "active" : ""}" href="${tab.href}">
            <svg viewBox="0 0 24 24" aria-hidden="true">${tab.icon}</svg>
            <span>${tab.label}</span>
          </a>
        `).join("")}
      </nav>`;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountParentNav);
  } else {
    mountParentNav();
  }
})();
