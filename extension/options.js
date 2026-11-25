"use strict";

const elements = {
  serverUrl: document.querySelector("#serverUrl"),
  participantToken: document.querySelector("#participantToken"),
  tokenHint: document.querySelector("#tokenHint"),
  consent: document.querySelector("#consent"),
  enabled: document.querySelector("#enabled"),
  clearToken: document.querySelector("#clearToken"),
  save: document.querySelector("#save"),
  test: document.querySelector("#test"),
  dashboard: document.querySelector("#dashboard"),
  message: document.querySelector("#message")
};

let current = null;

const send = (kind, extra = {}) => chrome.runtime.sendMessage({ kind, ...extra }).then((response) => {
  if (!response?.ok) throw new Error(response?.error || "操作失败");
  return response.result;
});

const showMessage = (text, isError = false) => {
  elements.message.textContent = text;
  elements.message.classList.toggle("error", isError);
};

const TOKEN_RULE = "请使用主办方单独发给你的参赛令牌。";

const render = (state) => {
  current = state;
  elements.serverUrl.value = state.serverUrl;
  elements.participantToken.value = "";
  elements.participantToken.placeholder = state.configured ? "已保存；留空则不更改" : "主办方签发的令牌";
  elements.tokenHint.textContent = state.participant
    ? `已验证：${state.participant.display_name} / ${state.participant.code}`
    : (state.configured ? `已保存令牌，建议执行连接测试。${TOKEN_RULE}` : TOKEN_RULE);
  elements.consent.checked = state.consent;
  elements.enabled.checked = state.enabled;
  elements.clearToken.disabled = !state.configured;
};

const formOptions = () => ({
  serverUrl: elements.serverUrl.value,
  participantToken: elements.participantToken.value,
  consent: elements.consent.checked,
  enabled: elements.enabled.checked
});

const save = async () => {
  const options = formOptions();
  if (!options.consent) {
    options.enabled = false;
    elements.enabled.checked = false;
  }
  const state = await send("saveOptions", { options });
  render(state);
  return state;
};

elements.save.addEventListener("click", async () => {
  elements.save.disabled = true;
  showMessage("正在保存…");
  try {
    await save();
    showMessage("设置已保存。");
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    elements.save.disabled = false;
  }
});

elements.test.addEventListener("click", async () => {
  elements.test.disabled = true;
  showMessage("正在保存并验证连接…");
  try {
    await save();
    const result = await send("testConnection");
    const state = await send("getOptions");
    render(state);
    showMessage(`连接成功：${result.participant.display_name} / ${result.participant.code}`);
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    elements.test.disabled = false;
  }
});

elements.clearToken.addEventListener("click", async () => {
  elements.clearToken.disabled = true;
  try {
    await send("clearToken");
    render(await send("getOptions"));
    showMessage("本机参赛者令牌已清除。");
  } catch (error) {
    showMessage(error.message, true);
  }
});

elements.dashboard.addEventListener("click", () => {
  if (current?.dashboardUrl) chrome.tabs.create({ url: current.dashboardUrl });
});

send("getOptions").then(render).catch((error) => showMessage(error.message, true));
