"use strict";

const DEFAULT_SERVER_URL = chrome.runtime.getManifest().homepage_url.replace(/\/$/, "");
const DEFAULTS = {
  serverUrl: DEFAULT_SERVER_URL,
  participantToken: "",
  consent: false,
  enabled: true,
  installationId: "",
  queue: [],
  droppedEvents: 0,
  lastUploadedAt: null,
  lastError: null,
  participant: null
};
const ALLOWED_EVENTS = new Set([
  "active_run",
  "run_created",
  "run_status",
  "run_result",
  "score_timeline",
  "account_hint",
  "heartbeat"
]);
const FORBIDDEN_KEYS = new Set([
  "authorization",
  "access_token",
  "refresh_token",
  "benchmark_token",
  "token",
  "cookie",
  "cookies",
  "password",
  "api_key",
  "apikey",
  "vpn_config",
  "benchmark_base_url",
  "base_url",
  "container_addr",
  "sandbox_logs",
  "logs",
  "flag",
  "env_config",
  "app_dist",
  "upload_url"
]);
const SAFE_FLAG_KEYS = new Set([
  "flag_correct",
  "flag_total",
  "final_flags",
  "flag_index",
  "correct_flag_count",
  "total_flag_count"
]);
const RUN_FIELDS = new Set([
  "status",
  "set_id",
  "set_name",
  "run_source",
  "evaluation_type",
  "username",
  "org",
  "agent_tag",
  "model_name",
  "harness_name",
  "harness_version",
  "created_at",
  "started_at",
  "ended_at",
  "run_id",
  "current_score",
  "max_score",
  "final_score",
  "score_percent",
  "score_origin",
  "accuracy",
  "flag_correct",
  "flag_total",
  "final_flags",
  "challenge_solved",
  "challenge_total",
  "duration_seconds",
  "elapsed_seconds",
  "score_elapsed_seconds",
  "pending_remain_seconds",
  "base_model",
  "radar_data",
  "score_events",
  "run_events",
  "matrix"
]);
const SCORE_EVENT_FIELDS = new Set([
  "id",
  "source_id",
  "challenge_id",
  "correct_flag_count",
  "total_flag_count",
  "challenge_code",
  "challenge_name",
  "event_time",
  "score_delta"
]);
const RUN_EVENT_FIELDS = new Set([
  "id",
  "source_id",
  "challenge_id",
  "flag_index",
  "operation_type",
  "challenge_code",
  "challenge_name",
  "event_time",
  "awarded"
]);
const MATRIX_FIELDS = new Set([
  "key",
  "title",
  "score",
  "challenge_total",
  "challenge_solved",
  "flag_total",
  "flag_correct",
  "challenges"
]);
const MATRIX_CHALLENGE_FIELDS = new Set([
  "name",
  "difficulty",
  "state",
  "correct_flag_count",
  "total_flag_count",
  "total_score",
  "level"
]);
const TIMELINE_FIELDS = new Set([
  "challenge_id",
  "timestamp",
  "challenge_code",
  "challenge_name",
  "challenge_score",
  "cumulative_score"
]);
const MAX_QUEUE_SIZE = 500;
const MAX_EVENT_BYTES = 220_000;
let flushing = false;
let mutationChain = Promise.resolve();

const withStorageLock = (callback) => {
  const operation = mutationChain.then(callback, callback);
  mutationChain = operation.catch(() => {});
  return operation;
};

const storageGet = async () => {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  if (stored.serverUrl && stored.serverUrl !== DEFAULT_SERVER_URL) {
    // A different organiser's build must start with a fresh identity and queue.
    return { ...DEFAULTS };
  }
  return { ...DEFAULTS, ...stored };
};

const ensureInitialized = () => withStorageLock(async () => {
  const current = await storageGet();
  const { serverUrl } = await chrome.storage.local.get(["serverUrl"]);
  const changes = serverUrl !== DEFAULT_SERVER_URL ? { ...current, serverUrl: DEFAULT_SERVER_URL } : {};
  if (!current.installationId) changes.installationId = `inst-${crypto.randomUUID()}`;
  if (!Array.isArray(current.queue)) changes.queue = [];
  if (Object.keys(changes).length) await chrome.storage.local.set(changes);
  await chrome.alarms.create("flush-telemetry", { periodInMinutes: 1 });
  await updateBadge();
});

const containsForbiddenKey = (value) => {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!value || typeof value !== "object") return false;
  for (const [key, child] of Object.entries(value)) {
    const normalized = key.trim().toLowerCase();
    if (FORBIDDEN_KEYS.has(normalized)) return true;
    if (normalized.includes("flag") && !SAFE_FLAG_KEYS.has(normalized)) return true;
    if (["token", "cookie", "password", "secret", "credential"].some((part) => normalized.includes(part))) {
      return true;
    }
    if (containsForbiddenKey(child)) return true;
  }
  return false;
};

const plainObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const onlyKeys = (value, allowed) => plainObject(value)
  && Object.keys(value).every((key) => allowed.has(key));
const objectArray = (value, allowed) => Array.isArray(value)
  && value.every((item) => onlyKeys(item, allowed));

const runShapeAllowed = (payload) => {
  if (!onlyKeys(payload, RUN_FIELDS)) return false;
  if (payload.base_model !== undefined
    && (!Array.isArray(payload.base_model) || !payload.base_model.every((item) => typeof item === "string"))) {
    return false;
  }
  if (payload.radar_data !== undefined) {
    if (!plainObject(payload.radar_data)) return false;
    if (!Object.values(payload.radar_data).every((value) => Number.isFinite(Number(value)))) return false;
  }
  if (payload.score_events !== undefined && !objectArray(payload.score_events, SCORE_EVENT_FIELDS)) {
    return false;
  }
  if (payload.run_events !== undefined && !objectArray(payload.run_events, RUN_EVENT_FIELDS)) {
    return false;
  }
  if (payload.matrix !== undefined) {
    if (!objectArray(payload.matrix, MATRIX_FIELDS)) return false;
    if (!payload.matrix.every((item) => item.challenges === undefined
      || objectArray(item.challenges, MATRIX_CHALLENGE_FIELDS))) {
      return false;
    }
  }
  return true;
};

const payloadShapeAllowed = (eventType, payload) => {
  if (["run_created", "run_status", "run_result"].includes(eventType)) {
    return runShapeAllowed(payload);
  }
  if (eventType === "active_run") {
    return onlyKeys(payload, new Set(["active_run"]))
      && (payload.active_run === null || runShapeAllowed(payload.active_run));
  }
  if (eventType === "score_timeline") {
    return onlyKeys(payload, new Set(["run_id", "items"]))
      && objectArray(payload.items, TIMELINE_FIELDS);
  }
  if (eventType === "account_hint") {
    return onlyKeys(payload, new Set(["username", "org", "agent_tag", "run_id"]));
  }
  if (eventType === "heartbeat") {
    return onlyKeys(payload, new Set(["run_id", "route", "current_score"]));
  }
  return false;
};

const normalizedServerUrl = (raw) => {
  let url;
  try {
    url = new URL(String(raw || ""));
  } catch {
    throw new Error("服务器地址无效");
  }
  const local = ["localhost", "127.0.0.1"].includes(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error("服务器必须使用 HTTPS（本机调试除外）");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("服务器地址不得包含凭据、查询参数或锚点");
  }
  url.pathname = url.pathname.replace(/\/+$/, "");
  return url.toString().replace(/\/$/, "");
};

const publicStatus = (state) => ({
  enabled: state.enabled,
  consent: state.consent,
  configured: Boolean(state.participantToken),
  serverUrl: state.serverUrl,
  dashboardUrl: state.serverUrl,
  queueSize: Array.isArray(state.queue) ? state.queue.length : 0,
  droppedEvents: state.droppedEvents || 0,
  lastUploadedAt: state.lastUploadedAt,
  lastError: state.lastError,
  participant: state.participant
});

const updateBadge = async (providedState) => {
  const state = providedState || await storageGet();
  const count = Array.isArray(state.queue) ? state.queue.length : 0;
  const ready = state.enabled && state.consent && state.participantToken;
  await chrome.action.setBadgeText({ text: count ? String(Math.min(count, 999)) : (ready ? "•" : "!") });
  await chrome.action.setBadgeBackgroundColor({ color: ready ? "#28d7a1" : "#d9822b" });
  await chrome.action.setTitle({
    title: ready
      ? `TsecBench 赛事监督：${count} 条待上传`
      : "TsecBench 赛事监督：需要配置"
  });
};

const enqueue = (message) => withStorageLock(async () => {
  if (!ALLOWED_EVENTS.has(message.eventType)) return { accepted: false, reason: "event_type" };
  if (!message.payload || typeof message.payload !== "object" || Array.isArray(message.payload)) {
    return { accepted: false, reason: "payload" };
  }
  if (containsForbiddenKey(message.payload)) return { accepted: false, reason: "sensitive_field" };
  if (!payloadShapeAllowed(message.eventType, message.payload)) {
    return { accepted: false, reason: "unexpected_field" };
  }

  const state = await storageGet();
  if (!state.enabled || !state.consent || !state.participantToken) {
    return { accepted: false, reason: "not_configured" };
  }
  const event = {
    schema_version: 1,
    event_id: `evt-${crypto.randomUUID()}`,
    event_type: message.eventType,
    observed_at: message.observedAt || new Date().toISOString(),
    context: {
      installation_id: state.installationId,
      extension_version: String(message.context?.extension_version || "0.0.0").slice(0, 32),
      page_path: String(message.context?.page_path || "/").slice(0, 500),
      page_visibility: ["visible", "hidden", "prerender"].includes(message.context?.page_visibility)
        ? message.context.page_visibility
        : "unknown",
      device: message.context?.device && typeof message.context.device === "object"
        ? message.context.device
        : {}
    },
    payload: message.payload
  };
  if (new TextEncoder().encode(JSON.stringify(event)).length > MAX_EVENT_BYTES) {
    return { accepted: false, reason: "too_large" };
  }

  const queue = Array.isArray(state.queue) ? state.queue : [];
  let droppedEvents = state.droppedEvents || 0;
  while (queue.length >= MAX_QUEUE_SIZE) {
    queue.shift();
    droppedEvents += 1;
  }
  queue.push({ event, attemptCount: 0, nextAttemptAt: 0 });
  await chrome.storage.local.set({ queue, droppedEvents });
  await updateBadge({ ...state, queue, droppedEvents });
  return { accepted: true };
});

const safeError = (error) => {
  const message = error instanceof Error ? error.message : String(error || "未知错误");
  return message.replace(/tsp_[A-Za-z0-9_-]+/g, "[已隐藏令牌]").slice(0, 180);
};

const request = async (state, path, options = {}) => {
  const serverUrl = normalizedServerUrl(state.serverUrl);
  return fetch(`${serverUrl}${path}`, {
    ...options,
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${state.participantToken}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {})
    }
  });
};

const flushQueue = async () => {
  if (flushing) return;
  flushing = true;
  try {
    let processed = 0;
    while (processed < 30) {
      const outcome = await withStorageLock(async () => {
        const state = await storageGet();
        if (!state.enabled || !state.consent || !state.participantToken || !state.queue.length) {
          return "stop";
        }
        const queued = state.queue[0];
        if ((queued.nextAttemptAt || 0) > Date.now()) return "stop";

        let response;
        try {
          response = await request(state, "/api/v1/ingest", {
            method: "POST",
            body: JSON.stringify(queued.event)
          });
        } catch (error) {
          queued.attemptCount = (queued.attemptCount || 0) + 1;
          queued.nextAttemptAt = Date.now() + Math.min(
            300_000,
            1_000 * (2 ** queued.attemptCount)
          );
          await chrome.storage.local.set({ queue: state.queue, lastError: safeError(error) });
          return "stop";
        }

        if (response.ok) {
          state.queue.shift();
          const lastUploadedAt = new Date().toISOString();
          await chrome.storage.local.set({ queue: state.queue, lastUploadedAt, lastError: null });
          return "continue";
        }

        if ([400, 413, 422].includes(response.status)) {
          state.queue.shift();
          await chrome.storage.local.set({
            queue: state.queue,
            droppedEvents: (state.droppedEvents || 0) + 1,
            lastError: `服务器拒绝了一条无效事件（${response.status}）`
          });
          return "continue";
        }

        queued.attemptCount = (queued.attemptCount || 0) + 1;
        const retryAfter = Number(response.headers.get("retry-after") || 0) * 1000;
        queued.nextAttemptAt = Date.now() + Math.max(
          retryAfter,
          Math.min(300_000, 1_000 * (2 ** queued.attemptCount))
        );
        await chrome.storage.local.set({
          queue: state.queue,
          lastError: response.status === 401 || response.status === 403
            ? "参赛者令牌无效或已停用"
            : `上传失败（HTTP ${response.status}）`
        });
        return "stop";
      });
      if (outcome !== "continue") break;
      processed += 1;
    }
  } finally {
    flushing = false;
    await updateBadge();
  }
};

const testConnection = async () => {
  const state = await storageGet();
  if (!state.participantToken) throw new Error("请先填写参赛者令牌");
  const response = await request(state, "/api/v1/participant/session");
  if (!response.ok) {
    throw new Error(response.status === 401 || response.status === 403
      ? "参赛者令牌无效或已停用"
      : `服务器连接失败（HTTP ${response.status}）`);
  }
  const participant = await response.json();
  await chrome.storage.local.set({ participant, lastError: null });
  return participant;
};

const saveOptions = (options) => withStorageLock(async () => {
  const state = await storageGet();
  const serverUrl = DEFAULT_SERVER_URL;
  const participantToken = typeof options.participantToken === "string" && options.participantToken.trim()
    ? options.participantToken.trim()
    : state.participantToken;
  if (participantToken && !/^[A-Za-z0-9_-]{6,80}$/.test(participantToken)) {
    throw new Error("参赛者令牌格式无效");
  }
  const changes = {
    serverUrl,
    participantToken,
    consent: Boolean(options.consent),
    enabled: Boolean(options.enabled),
    ...(participantToken !== state.participantToken
      ? { participant: null, queue: [], lastUploadedAt: null }
      : {}),
    lastError: null
  };
  await chrome.storage.local.set(changes);
  await updateBadge({ ...state, ...changes });
  return publicStatus({ ...state, ...changes });
});

chrome.runtime.onInstalled.addListener(() => { ensureInitialized().catch(() => {}); });
chrome.runtime.onStartup.addListener(() => { ensureInitialized().catch(() => {}); });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "flush-telemetry") flushQueue().catch(() => {});
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    switch (message?.kind) {
      case "telemetry": {
        const result = await enqueue(message);
        if (result.accepted) flushQueue().catch(() => {});
        return result;
      }
      case "getStatus":
      case "getOptions":
        return publicStatus(await storageGet());
      case "saveOptions":
        return await saveOptions(message.options || {});
      case "setEnabled": {
        const state = await storageGet();
        await chrome.storage.local.set({ enabled: Boolean(message.enabled) });
        await updateBadge({ ...state, enabled: Boolean(message.enabled) });
        return { ok: true };
      }
      case "clearToken":
        await withStorageLock(() => chrome.storage.local.set({ participantToken: "", participant: null, queue: [], lastError: null, lastUploadedAt: null }));
        await updateBadge();
        return { ok: true };
      case "testConnection":
        return { ok: true, participant: await testConnection() };
      case "flush":
        await flushQueue();
        return publicStatus(await storageGet());
      default:
        return { ok: false, error: "unsupported_message" };
    }
  })().then(
    (result) => sendResponse({ ok: true, result }),
    (error) => sendResponse({ ok: false, error: safeError(error) })
  );
  return true;
});

ensureInitialized().catch(() => {});
