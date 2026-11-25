"use strict";

const state = {
  config: null,
  scoreboard: [],
  scoreSeries: [],
  challengeStats: null,
  adminToken: sessionStorage.getItem("tsecSupervisorAdminToken") || "",
  adminState: null,
  lastRefresh: null,
  batchIndex: 0,
  chartBatchIndex: 0,
  chartMode: "trend",
};

const BATCH_SIZE = 10;
const BATCH_INTERVAL_MS = 8000;
const CHART_BATCH_INTERVAL_MS = 10 * 60 * 1000;
const CHART_MODE_INTERVAL_MS = 12 * 1000;
let chartModeTimer = null;

function totalBatches() {
  return Math.max(1, Math.ceil(state.scoreboard.length / BATCH_SIZE));
}

function batchAt(index) {
  const total = totalBatches();
  if (index >= total) index = 0;
  const start = index * BATCH_SIZE;
  return state.scoreboard.slice(start, start + BATCH_SIZE);
}

function currentBatch() {
  state.batchIndex = Math.min(state.batchIndex, totalBatches() - 1);
  return batchAt(state.batchIndex);
}

function currentChartBatch() {
  state.chartBatchIndex = Math.min(state.chartBatchIndex, totalBatches() - 1);
  return batchAt(state.chartBatchIndex);
}

const byId = (id) => document.getElementById(id);

function formatNumber(value, digits = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = Math.floor(value % 60);
  return [hours, minutes, secs].map((item) => String(item).padStart(2, "0")).join(":");
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function statusClass(value) {
  const status = String(value || "observed").toLowerCase();
  if (["finished", "timeout"].includes(status)) return "finished";
  if (["running"].includes(status)) return "running";
  if (["abnormal", "failed", "wait_expired"].includes(status)) return "abnormal";
  return "pending";
}

function statusLabel(value) {
  const labels = {
    provisioning: "部署中",
    pending: "待接入",
    running: "进行中",
    finished: "已完成",
    timeout: "已结束",
    abnormal: "异常",
    failed: "失败",
    wait_expired: "接入超时",
    observed: "观察中",
  };
  return labels[value] || value || "观察中";
}

function verificationLabel(value) {
  return {
    observed: "观察中",
    pending: "待核验",
    verified: "已核验",
    risk: "需复核",
  }[value] || "观察中";
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

function appendPrimarySecondary(cell, primary, secondary) {
  cell.append(createElement("strong", "primary-text", primary || "—"));
  cell.append(createElement("small", "secondary-text", secondary || "—"));
}

function rawScore(item) {
  return Number(item.display_score ?? item.final_score ?? item.current_score ?? 0) || 0;
}

const PODIUM_RANKS = [
  { medal: "🥇", className: "gold" },
  { medal: "🥈", className: "silver" },
  { medal: "🥉", className: "bronze" },
];

function renderPodium() {
  const list = byId("podium-list");
  list.replaceChildren();
  const top = state.scoreboard.slice(0, 3);
  if (!top.length) {
    const empty = createElement("li", "podium-empty", "等待选手上传成绩");
    list.append(empty);
    return;
  }
  top.forEach((item, index) => {
    const rank = PODIUM_RANKS[index] || { medal: "", className: "" };
    const row = createElement("li", `podium-row ${rank.className}`);
    const name = createElement("span", "podium-name", item.display_name);
    const solved = createElement(
      "span",
      "podium-solved",
      `${formatNumber(item.solved)} 题`,
    );
    const score = createElement("strong", "podium-score", formatNumber(rawScore(item)));
    row.append(
      createElement("span", "podium-medal", rank.medal),
      name,
      solved,
      score,
    );
    list.append(row);
  });
}

function renderChallengeMetrics() {
  const stats = state.challengeStats || {};
  const setCount = (stats.sets || []).length;
  const active = state.scoreboard.filter((item) => (
    ["running", "pending", "provisioning"].includes(item.status)
  )).length;
  byId("metric-setname").textContent = stats.primary_set_name || "等待数据";
  byId("metric-setstatus").textContent = setCount === 0
    ? "等待题集观测"
    : (setCount > 1
      ? `⚠ 检测到 ${setCount} 个题集，请核查`
      : `全员同一题集 · ${active} 人正在跑分`);
  byId("metric-challenges").textContent = formatNumber(stats.challenges_solved);
  byId("metric-solves").textContent = formatNumber(stats.solve_count);
}

function renderScoreboard() {
  const body = byId("scoreboard-body");
  const empty = byId("scoreboard-empty");
  body.replaceChildren();
  empty.hidden = state.scoreboard.length > 0;

  for (const item of currentBatch()) {
    const row = document.createElement("tr");
    const rankCell = createElement("td");
    rankCell.append(createElement("span", `rank ${item.rank <= 3 ? "top" : ""}`, item.rank));

    const playerCell = createElement("td");
    appendPrimarySecondary(playerCell, item.display_name, item.tsec_username || item.participant_code);
    const startCell = createElement("td");
    appendPrimarySecondary(startCell, formatTime(item.started_at), `RUN ${item.run_id || "—"}`);

    const statusCell = createElement("td");
    statusCell.append(
      createElement("span", `badge ${statusClass(item.status)}`, statusLabel(item.status)),
    );
    const progressCell = createElement("td", "numeric");
    const solved = formatNumber(item.solved);
    const progress = item.challenge_total ? `${solved} / ${item.challenge_total}` : solved;
    progressCell.append(createElement("strong", "score", progress));
    const rawCell = createElement(
      "td",
      "numeric",
      formatNumber(rawScore(item)),
    );
    const timeCell = createElement("td", "numeric", formatDuration(item.elapsed_seconds));
    const verifyCell = createElement("td");
    const verifyClass = item.risk_count > 0 ? "risk" : item.verification_status;
    verifyCell.append(
      createElement("span", `badge ${verifyClass}`, verificationLabel(verifyClass)),
    );

    row.append(
      rankCell,
      playerCell,
      startCell,
      statusCell,
      progressCell,
      rawCell,
      timeCell,
      verifyCell,
    );
    body.append(row);
  }

  renderPodium();
  renderChallengeMetrics();
}

function svgElement(tag, attributes = {}, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

function chartTimeLabel(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(timestamp));
}

function niceCeil(value) {
  if (!(value > 0)) return 10;
  const exponent = Math.floor(Math.log10(value));
  const scale = 10 ** exponent;
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (step * scale >= value) return step * scale;
  }
  return value;
}

function seriesColor(index, total) {
  const hue = Math.round((index * 360) / Math.max(1, total));
  return `hsl(${hue}, 72%, 62%)`;
}

function renderScoreChart() {
  const svg = byId("score-chart");
  const legend = byId("score-chart-legend");
  const empty = byId("score-chart-empty");
  svg.replaceChildren();
  legend.replaceChildren();

  const batch = currentChartBatch();
  const order = new Map(batch.map((item, index) => [item.participant_code, index]));
  const series = state.scoreSeries
    .filter((item) => order.has(item.participant_code)
      && Array.isArray(item.points) && item.points.length)
    .sort((a, b) => order.get(a.participant_code) - order.get(b.participant_code));
  empty.hidden = series.length > 0;
  svg.hidden = series.length === 0;
  if (!series.length) return;

  const plot = { left: 58, right: 1172, top: 22, bottom: 316 };
  const timestamps = series.flatMap((item) => (
    item.points.map((point) => new Date(point.timestamp).getTime())
  ));
  const scores = series.flatMap((item) => item.points.map((point) => Number(point.score) || 0));
  const minTime = Math.min(...timestamps);
  const rawMaxTime = Math.max(...timestamps);
  const maxTime = rawMaxTime === minTime ? minTime + 60_000 : rawMaxTime;
  const fullScore = Math.max(
    ...state.scoreboard.map((item) => Number(item.max_score) || 0),
    0,
  );
  const maxScore = fullScore > 0 ? fullScore : niceCeil(Math.max(...scores, 1));
  const x = (time) => plot.left + ((time - minTime) / (maxTime - minTime)) * (plot.right - plot.left);
  const y = (score) => plot.bottom - (score / maxScore) * (plot.bottom - plot.top);

  for (let index = 0; index <= 5; index += 1) {
    const score = (maxScore / 5) * index;
    const position = y(score);
    svg.append(svgElement("line", {
      x1: plot.left,
      x2: plot.right,
      y1: position,
      y2: position,
      class: "chart-grid",
    }));
    svg.append(svgElement("text", {
      x: plot.left - 12,
      y: position + 4,
      "text-anchor": "end",
      class: "chart-axis-label",
    }, formatNumber(score, score % 1 ? 1 : 0)));
  }

  for (let index = 0; index <= 4; index += 1) {
    const timestamp = minTime + ((maxTime - minTime) / 4) * index;
    const position = x(timestamp);
    svg.append(svgElement("line", {
      x1: position,
      x2: position,
      y1: plot.top,
      y2: plot.bottom,
      class: "chart-grid",
    }));
    svg.append(svgElement("text", {
      x: position,
      y: plot.bottom + 24,
      "text-anchor": index === 0 ? "start" : (index === 4 ? "end" : "middle"),
      class: "chart-axis-label",
    }, chartTimeLabel(timestamp)));
  }

  series.forEach((item, index) => {
    const color = seriesColor(index, series.length);
    const legendItem = createElement("span", "chart-legend-item");
    const swatch = createElement("i", "chart-swatch");
    swatch.style.background = color;
    legendItem.append(swatch, document.createTextNode(item.display_name));
    legend.append(legendItem);

    const coordinates = item.points.map((point) => ({
      x: x(new Date(point.timestamp).getTime()),
      y: y(Number(point.score) || 0),
      score: Number(point.score) || 0,
      timestamp: point.timestamp,
    }));
    const pathData = coordinates.map((point, pointIndex) => (
      `${pointIndex === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`
    )).join(" ");
    svg.append(svgElement("path", { d: pathData, stroke: color, class: "chart-line" }));
    for (const point of coordinates) {
      const circle = svgElement("circle", {
        cx: point.x,
        cy: point.y,
        r: 4,
        fill: color,
        class: "chart-point",
      });
      circle.append(svgElement(
        "title",
        {},
        `${item.display_name} · ${formatNumber(point.score)} · ${chartTimeLabel(point.timestamp)}`,
      ));
      svg.append(circle);
    }
  });

  svg.append(svgElement("text", { x: 13, y: 18, class: "chart-axis-label" }, "累计分"));
}

function renderScoreDistribution() {
  const svg = byId("score-distribution-chart");
  const empty = byId("score-distribution-empty");
  svg.replaceChildren();

  const scores = state.scoreboard
    .map((item) => rawScore(item))
    .filter((score) => Number.isFinite(score));
  empty.hidden = scores.length > 0;
  svg.hidden = scores.length === 0;
  if (!scores.length) return;

  const plot = { left: 58, right: 1172, top: 22, bottom: 306 };
  const fullScore = Math.max(
    ...state.scoreboard.map((item) => Number(item.max_score) || 0),
    ...scores,
    1,
  );
  const binCount = 10;
  const bins = Array.from({ length: binCount }, () => 0);
  for (const score of scores) {
    const normalized = Math.max(0, Math.min(score, fullScore));
    const index = Math.min(binCount - 1, Math.floor((normalized / fullScore) * binCount));
    bins[index] += 1;
  }
  const maxCount = niceCeil(Math.max(...bins, 1));
  const plotWidth = plot.right - plot.left;
  const slotWidth = plotWidth / binCount;
  const barGap = Math.min(12, slotWidth * 0.16);
  const y = (count) => plot.bottom - (count / maxCount) * (plot.bottom - plot.top);

  for (let index = 0; index <= 4; index += 1) {
    const count = (maxCount / 4) * index;
    const position = y(count);
    svg.append(svgElement("line", {
      x1: plot.left,
      x2: plot.right,
      y1: position,
      y2: position,
      class: "chart-grid",
    }));
    svg.append(svgElement("text", {
      x: plot.left - 12,
      y: position + 4,
      "text-anchor": "end",
      class: "chart-axis-label",
    }, formatNumber(count, count % 1 ? 1 : 0)));
  }

  for (let index = 0; index <= 5; index += 1) {
    const score = (fullScore / 5) * index;
    const position = plot.left + (plotWidth / 5) * index;
    svg.append(svgElement("line", {
      x1: position,
      x2: position,
      y1: plot.top,
      y2: plot.bottom,
      class: "chart-grid",
    }));
    svg.append(svgElement("text", {
      x: position,
      y: plot.bottom + 24,
      "text-anchor": index === 0 ? "start" : (index === 5 ? "end" : "middle"),
      class: "chart-axis-label",
    }, formatNumber(score)));
  }

  bins.forEach((count, index) => {
    const left = plot.left + slotWidth * index + barGap / 2;
    const top = y(count);
    const lower = (fullScore / binCount) * index;
    const upper = (fullScore / binCount) * (index + 1);
    const rect = svgElement("rect", {
      x: left,
      y: top,
      width: Math.max(1, slotWidth - barGap),
      height: Math.max(0, plot.bottom - top),
      rx: 2,
      class: "histogram-bar",
    });
    rect.append(svgElement(
      "title",
      {},
      `${formatNumber(lower)}–${formatNumber(upper)} 分 · ${formatNumber(count)} 人`,
    ));
    svg.append(rect);
    if (count > 0) {
      svg.append(svgElement("text", {
        x: left + (slotWidth - barGap) / 2,
        y: Math.max(plot.top + 12, top - 7),
        "text-anchor": "middle",
        class: "histogram-count",
      }, formatNumber(count)));
    }
  });

  svg.append(svgElement("text", { x: 13, y: 18, class: "chart-axis-label" }, "选手数"));
  svg.append(svgElement("text", {
    x: (plot.left + plot.right) / 2,
    y: 354,
    "text-anchor": "middle",
    class: "chart-axis-label",
  }, `原始累计分（满分 ${formatNumber(fullScore)}）`));
}

function applyChartMode() {
  const isTrend = state.chartMode === "trend";
  byId("trend-chart-view").hidden = !isTrend;
  byId("distribution-chart-view").hidden = isTrend;
  byId("score-chart-legend").hidden = !isTrend;
  byId("score-chart-eyebrow").textContent = isTrend ? "SCORE VELOCITY" : "SCORE DISTRIBUTION";
  byId("score-chart-title").textContent = isTrend ? "得分趋势" : "得分分布";
  for (const mode of ["trend", "distribution"]) {
    const button = byId(`chart-mode-${mode}`);
    const active = state.chartMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}

function restartChartModeRotation() {
  if (chartModeTimer) clearInterval(chartModeTimer);
  chartModeTimer = setInterval(() => {
    state.chartMode = state.chartMode === "trend" ? "distribution" : "trend";
    applyChartMode();
  }, CHART_MODE_INTERVAL_MS);
}

function selectChartMode(mode) {
  state.chartMode = mode;
  applyChartMode();
  restartChartModeRotation();
}

async function loadPublic() {
  try {
    const [configResponse, boardResponse, seriesResponse, statsResponse] = await Promise.all([
      fetch("/api/v1/public/config", { cache: "no-store" }),
      fetch("/api/v1/public/scoreboard", { cache: "no-store" }),
      fetch("/api/v1/public/score-series", { cache: "no-store" }),
      fetch("/api/v1/public/challenge-stats", { cache: "no-store" }),
    ]);
    if (!configResponse.ok || !boardResponse.ok || !seriesResponse.ok || !statsResponse.ok) {
      throw new Error("同步失败");
    }
    state.config = await configResponse.json();
    const board = await boardResponse.json();
    const scoreSeries = await seriesResponse.json();
    state.challengeStats = await statsResponse.json();
    state.scoreboard = Array.isArray(board.items) ? board.items : [];
    state.scoreSeries = Array.isArray(scoreSeries.series) ? scoreSeries.series : [];
    state.lastRefresh = new Date();
    const config = state.config;
    byId("event-name").textContent = config.event_name;
    byId("scoring-notice").textContent = [
      config.scoring_start && `开始计分 ${config.scoring_start}`,
      config.scoring_end && `截止 ${config.scoring_end}`,
      config.scoring_set_name && `计分题集 ${config.scoring_set_name}`,
    ].filter(Boolean).join(" · ") || "所有时间和题集均参与计分";
    byId("refresh-label").textContent = `同步于 ${formatTime(state.lastRefresh)}`;
    document.title = `${state.config.event_name} · 实时赛况`;
    renderScoreboard();
    renderScoreChart();
    renderScoreDistribution();
    applyChartMode();
    renderCountdown();
  } catch (error) {
    byId("refresh-label").textContent = "同步暂时中断，正在重试";
  }
}

function renderClock() {
  byId("clock").textContent = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function renderCountdown() {
  const element = byId("countdown");
  const label = byId("countdown-label");
  const end = state.config && state.config.scoring_end
    ? new Date(state.config.scoring_end)
    : null;
  if (!end || Number.isNaN(end.getTime())) {
    element.textContent = "--:--:--";
    label.textContent = "未设置截止时间";
    return;
  }
  const remain = end.getTime() - Date.now();
  if (remain <= 0) {
    element.textContent = "已结束";
    label.textContent = "比赛已结束";
    return;
  }
  label.textContent = `截止 ${state.config.scoring_end}`;
  element.textContent = formatDuration(remain / 1000);
}

async function adminFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.adminToken}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function renderParticipants(items) {
  const list = byId("participant-list");
  list.replaceChildren();
  if (!items.length) {
    list.append(createElement("div", "empty-state", "尚未创建选手"));
    return;
  }
  for (const item of items) {
    const row = createElement("div", "participant-item");
    const info = createElement("div");
    info.append(createElement("strong", "", item.display_name));
    info.append(
      createElement(
        "small",
        "",
        `${item.code} · 绑定账号: ${item.tsec_username || "待首次观测"} · ${item.active ? "启用" : "停用"}`,
      ),
    );
    const actions = createElement("div", "participant-actions");
    const rotate = createElement("button", "mini-button", "换 Token");
    rotate.type = "button";
    rotate.addEventListener("click", async () => {
      if (!confirm(`确认轮换 ${item.display_name} 的 Token？旧 Token 将立即失效。`)) return;
      const result = await adminFetch(`/api/v1/admin/participants/${item.id}/rotate-token`, { method: "POST" });
      showNewToken(result.token);
    });
    const toggle = createElement("button", "mini-button", item.active ? "停用" : "启用");
    toggle.type = "button";
    toggle.addEventListener("click", async () => {
      await adminFetch(`/api/v1/admin/participants/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !Boolean(item.active) }),
      });
      await loadAdmin();
    });
    actions.append(rotate, toggle);
    row.append(info, actions);
    list.append(row);
  }
}

function compactDevice(device) {
  if (!device || typeof device !== "object") return "—";
  return [
    device.platform,
    device.browser,
    device.screen_bucket,
    device.timezone,
    device.hardware_concurrency ? `${device.hardware_concurrency} threads` : null,
  ].filter(Boolean).join(" · ") || "—";
}

function renderAdminRuns(items) {
  const body = byId("admin-runs-body");
  body.replaceChildren();
  for (const item of items) {
    const row = document.createElement("tr");
    const player = createElement("td");
    appendPrimarySecondary(player, item.display_name, item.participant_code);
    const runId = createElement("td", "", item.tsec_run_id);
    const account = createElement("td");
    appendPrimarySecondary(
      account,
      item.tsec_username || "待识别",
      `绑定: ${item.bound_tsec_username || "待首次观测"} · ${item.agent_tag || item.bound_agent_tag || "待识别 Agent"}`,
    );
    const score = createElement("td");
    appendPrimarySecondary(
      score,
      formatNumber(item.display_score, 2),
      `${formatNumber(item.current_score)} / ${formatNumber(item.max_score)} · ${item.set_name || "未知题集"} · ${item.scoring === false ? "不计分（时间/题集）" : "计分中"}`,
    );
    const ipCell = createElement("td");
    appendPrimarySecondary(ipCell, item.last_ip || "未保留原始 IP", item.ip_location || "未知归属地");
    const device = createElement("td", "device-text", compactDevice(item.device));
    const risk = createElement("td");
    const riskList = createElement("div", "risk-list");
    if (item.risk_flags?.length) {
      for (const flag of item.risk_flags) riskList.append(createElement("span", "risk-chip", flag));
    } else {
      riskList.append(createElement("span", "secondary-text", "无自动告警"));
    }
    risk.append(riskList);
    const verification = createElement("td");
    const select = createElement("select", "verification-select");
    for (const value of ["observed", "pending", "verified", "risk"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = verificationLabel(value);
      option.selected = value === item.verification_status;
      select.append(option);
    }
    select.addEventListener("change", async () => {
      await adminFetch(`/api/v1/admin/runs/${item.id}/verification`, {
        method: "PATCH",
        body: JSON.stringify({ verification_status: select.value }),
      });
      await Promise.all([loadAdmin(), loadPublic()]);
    });
    verification.append(select);
    const seen = createElement("td", "", formatTime(item.last_seen_at));
    row.append(player, runId, account, score, ipCell, device, risk, verification, seen);
    body.append(row);
  }
}

async function loadAdmin() {
  const result = await adminFetch("/api/v1/admin/state");
  state.adminState = result;
  byId("admin-count-participants").textContent = result.counts.participants;
  byId("admin-count-runs").textContent = result.counts.runs;
  byId("admin-count-events").textContent = result.counts.events;
  byId("admin-count-score-events").textContent = result.counts.score_events;
  const alert = byId("admin-set-alert");
  const runs = result.runs || [];
  const setNames = new Set(runs.map((run) => run.set_name || "未知题集"));
  if (setNames.size > 1) {
    alert.textContent = `⚠ 检测到 ${setNames.size} 个不同题集：${[...setNames].join("、")}，请核查选手是否使用同一靶场`;
    alert.hidden = false;
  } else if (setNames.size === 1) {
    alert.textContent = `当前题集：${[...setNames][0]}`;
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }
  renderParticipants(result.participants || []);
  renderAdminRuns(runs);
}

function showNewToken(token) {
  byId("new-token").textContent = token;
  byId("new-token-box").hidden = false;
}

byId("admin-open").addEventListener("click", async () => {
  if (state.adminToken) {
    try {
      await loadAdmin();
      byId("admin-console").hidden = false;
      byId("admin-console").scrollIntoView({ behavior: "smooth" });
      return;
    } catch {
      state.adminToken = "";
      sessionStorage.removeItem("tsecSupervisorAdminToken");
    }
  }
  byId("admin-login-dialog").showModal();
});

byId("admin-login-close").addEventListener("click", () => {
  byId("admin-login-error").textContent = "";
  byId("admin-login-dialog").close();
});

byId("admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.adminToken = byId("admin-token").value.trim();
  byId("admin-login-error").textContent = "";
  try {
    await loadAdmin();
    sessionStorage.setItem("tsecSupervisorAdminToken", state.adminToken);
    byId("admin-login-dialog").close();
    byId("admin-console").hidden = false;
    byId("admin-console").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    state.adminToken = "";
    byId("admin-login-error").textContent = error.message;
  }
});

byId("admin-close").addEventListener("click", () => {
  state.adminToken = "";
  state.adminState = null;
  sessionStorage.removeItem("tsecSupervisorAdminToken");
  byId("admin-console").hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
});

byId("admin-refresh").addEventListener("click", loadAdmin);

byId("participant-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const displayName = byId("participant-name").value.trim();
  const code = byId("participant-code").value.trim();
  const tokenInput = byId("participant-token-input").value.trim();
  const body = { display_name: displayName };
  if (code) body.code = code;
  if (tokenInput) body.token = tokenInput;
  const result = await adminFetch("/api/v1/admin/participants", {
    method: "POST",
    body: JSON.stringify(body),
  });
  showNewToken(result.token);
  event.target.reset();
  await loadAdmin();
});

byId("copy-new-token").addEventListener("click", async () => {
  await navigator.clipboard.writeText(byId("new-token").textContent);
  byId("copy-new-token").textContent = "已复制";
  setTimeout(() => { byId("copy-new-token").textContent = "复制 Token"; }, 1200);
});

let lastBulkCreated = [];

function renderBulkResult(result) {
  const box = byId("bulk-result");
  const summary = byId("bulk-summary");
  const errorsBox = byId("bulk-errors");
  const body = byId("bulk-tokens-body");
  body.replaceChildren();
  errorsBox.replaceChildren();
  lastBulkCreated = result.created || [];

  const created = result.created || [];
  const skipped = result.skipped || [];
  const errors = result.errors || [];
  summary.textContent = `新建 ${created.length} 人 · 跳过 ${skipped.length} · 格式错误 ${errors.length}`;
  for (const error of errors) {
    errorsBox.append(createElement("p", "form-error", `第 ${error.line} 行：${error.reason}`));
  }
  for (const skippedItem of skipped) {
    errorsBox.append(createElement(
      "p",
      "form-error",
      `${skippedItem.display_name}：${skippedItem.reason}`,
    ));
  }
  for (const item of created) {
    const row = document.createElement("tr");
    row.append(
      createElement("td", "", item.display_name),
      createElement("td", "", item.code),
      createElement("td", "token-cell", item.token),
    );
    body.append(row);
  }
  byId("copy-bulk-tokens").hidden = created.length === 0;
  box.hidden = false;
}

byId("bulk-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = byId("bulk-text").value.trim();
  if (!text) return;
  try {
    const result = await adminFetch("/api/v1/admin/participants/bulk", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    renderBulkResult(result);
    await loadAdmin();
  } catch (error) {
    renderBulkResult({ created: [], skipped: [], errors: [{ line: "—", reason: error.message }] });
  }
});

byId("copy-bulk-tokens").addEventListener("click", async () => {
  const tsv = lastBulkCreated
    .map((item) => `${item.display_name}\t${item.code}\t${item.token}`)
    .join("\n");
  await navigator.clipboard.writeText(tsv);
  byId("copy-bulk-tokens").textContent = "已复制";
  setTimeout(() => { byId("copy-bulk-tokens").textContent = "复制全部（TSV）"; }, 1200);
});

byId("chart-mode-trend").addEventListener("click", () => selectChartMode("trend"));
byId("chart-mode-distribution").addEventListener("click", () => selectChartMode("distribution"));

renderClock();
applyChartMode();
restartChartModeRotation();
setInterval(() => {
  renderClock();
  renderCountdown();
}, 1000);
setInterval(() => {
  if (totalBatches() <= 1) return;
  state.batchIndex = (state.batchIndex + 1) % totalBatches();
  renderScoreboard();
}, BATCH_INTERVAL_MS);
setInterval(() => {
  if (totalBatches() <= 1) return;
  state.chartBatchIndex = (state.chartBatchIndex + 1) % totalBatches();
  renderScoreChart();
}, CHART_BATCH_INTERVAL_MS);
loadPublic();
setInterval(loadPublic, 3000);
