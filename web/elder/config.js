// 前端运行时配置（即填即用）。
//
// API_BASE 留空 = 与当前页面同源（默认，无需改动；后端用 StaticFiles 托管前端时即此情形）。
// 前后端分离部署时，填后端地址即可，例如：
//   window.APP_CONFIG = { API_BASE: "https://api.example.com" };
// 注意：不带末尾斜杠。ws/wss 会据此自动推导。
window.APP_CONFIG = {
  API_BASE: "",
};
