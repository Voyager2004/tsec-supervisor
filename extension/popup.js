"use strict";

const elements = {
  enabled: document.querySelector("#enabled"),
  setupNotice: document.querySelector("#setupNotice"),
  setupTitle: document.querySelector("#setupTitle"),
  setupDetail: document.querySelector("#setupDetail"),
  setupGoOptions: document.querySelector("#setupGoOptions"),
  statusDot: document.querySelector("#statusDot"),
  participant: document.querySelector("#participant"),
  queueSize: document.querySelector("#queueSize"),
  lastUpload: document.querySelector("#lastUpload"),
  lastError: document.querySelector("#lastError"),
  flush: document.querySelector("#flush"),
  dashboard: document.querySelector("#dashboard"),
  options: document.querySelector("#options")
};

let status = null;

const message = (kind, extra = {}) => chrome.runtime.sendMessage({ kind, ...extra }).then((response) => {
  if (!response?.ok) throw new Error(response?.error || "操作失败");
  return response.result;
});

const formatTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("zh-CN", { hour12: false });
};

const TOKEN_RULE = "请使用主办方单独发给你的参赛令牌。";

const renderSetupNotice = (next) => {
  const pending = [];
  if (!next.configured) pending.push("token");
  if (!next.consent) pending.push("consent");
  elements.setupNotice.hidden = pending.length === 0;
  if (pending.length === 0) return;
  if (!next.configured) {
    elements.setupTitle.textContent = "尚未配置参赛者令牌";
    elements.setupDetail.textContent = `${TOKEN_RULE}请打开设置页填写令牌并勾选同意，成绩才会开始上报。`;
    elements.setupGoOptions.textContent = "去配置令牌";
  } else {
    elements.setupTitle.textContent = "还需同意数据采集";
    elements.setupDetail.textContent = "令牌已保存，但尚未在设置页勾选“我已阅读并同意上述数据采集与赛事监督用途”。";
    elements.setupGoOptions.textContent = "去完成同意";
  }
};

const render = (next) => {
  status = next;
  const ready = next.enabled && next.consent && next.configured;
  elements.enabled.checked = next.enabled;
  renderSetupNotice(next);
  elements.statusDot.className = `status-dot ${ready ? "ready" : (next.enabled ? "" : "off")}`;
  elements.participant.textContent = next.participant
    ? `${next.participant.display_name} / ${next.participant.code}`
    : (next.configured ? "已配置（待验证）" : "未配置令牌");
  elements.queueSize.textContent = String(next.queueSize || 0);
  elements.lastUpload.textContent = formatTime(next.lastUploadedAt);
  elements.lastError.hidden = !next.lastError;
  elements.lastError.textContent = next.lastError || "";
  elements.flush.disabled = !ready || next.queueSize === 0;
};

const refresh = async () => render(await message("getStatus"));

elements.enabled.addEventListener("change", async () => {
  elements.enabled.disabled = true;
  try {
    await message("setEnabled", { enabled: elements.enabled.checked });
    await refresh();
  } catch (error) {
    elements.lastError.hidden = false;
    elements.lastError.textContent = error.message;
  } finally {
    elements.enabled.disabled = false;
  }
});

elements.flush.addEventListener("click", async () => {
  elements.flush.disabled = true;
  try {
    render(await message("flush"));
  } catch (error) {
    elements.lastError.hidden = false;
    elements.lastError.textContent = error.message;
  }
});

elements.dashboard.addEventListener("click", () => {
  if (status?.dashboardUrl) chrome.tabs.create({ url: status.dashboardUrl });
});
elements.options.addEventListener("click", () => chrome.runtime.openOptionsPage());
elements.setupGoOptions.addEventListener("click", () => chrome.runtime.openOptionsPage());

refresh().catch((error) => {
  elements.lastError.hidden = false;
  elements.lastError.textContent = error.message;
});
