(() => {
  "use strict";

  const MARKER = "__tsecSupervisorHookV1";
  if (window[MARKER]) return;
  Object.defineProperty(window, MARKER, { value: true });

  const SOURCE = "tsec-supervisor-page";
  const API_PREFIX = "/api/v1";
  const TARGET_ORIGIN = "https://tsecbench.zc.tencent.com";
  const MAX_RESPONSE_BYTES = 1_000_000;

  const text = (value, limit = 160) => {
    if (value === null || value === undefined) return undefined;
    const output = String(value).replace(/\0/g, "").trim();
    return output ? output.slice(0, limit) : undefined;
  };

  const number = (value) => {
    if (value === null || value === undefined || typeof value === "boolean") return undefined;
    const output = Number(value);
    return Number.isFinite(output) ? output : undefined;
  };

  const integer = (value) => {
    const output = number(value);
    return output === undefined ? undefined : Math.trunc(output);
  };

  const assignText = (target, source, fields) => {
    for (const field of fields) {
      const value = text(source?.[field], field === "abnormal_reason" ? 500 : 160);
      if (value !== undefined) target[field] = value;
    }
  };

  const assignNumbers = (target, source, fields) => {
    for (const field of fields) {
      const value = number(source?.[field]);
      if (value !== undefined) target[field] = value;
    }
  };

  const sanitizeScoreEvents = (value) => {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 500).flatMap((source) => {
      if (!source || typeof source !== "object" || Array.isArray(source)) return [];
      const event = {};
      for (const field of ["id", "source_id", "challenge_id", "correct_flag_count", "total_flag_count"]) {
        const value = integer(source[field]);
        if (value !== undefined) event[field] = value;
      }
      assignText(event, source, ["challenge_code", "challenge_name", "event_time"]);
      const scoreDelta = number(source.score_delta);
      if (scoreDelta !== undefined) event.score_delta = scoreDelta;
      return [event];
    });
  };

  const sanitizeRunEvents = (value) => {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 500).flatMap((source) => {
      if (!source || typeof source !== "object" || Array.isArray(source)) return [];
      const event = {};
      for (const field of ["id", "source_id", "challenge_id"]) {
        const value = integer(source[field]);
        if (value !== undefined) event[field] = value;
      }
      assignText(event, source, ["operation_type", "challenge_code", "challenge_name", "event_time"]);
      const awarded = number(source.awarded ?? source.extra?.awarded);
      const flagIndex = integer(source.flag_index ?? source.extra?.flag_index);
      if (awarded !== undefined) event.awarded = awarded;
      if (flagIndex !== undefined) event.flag_index = flagIndex;
      return [event];
    });
  };

  const sanitizeMatrix = (value) => {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 16).flatMap((source) => {
      if (!source || typeof source !== "object" || Array.isArray(source)) return [];
      const dimension = {};
      assignText(dimension, source, ["key", "title"]);
      assignNumbers(dimension, source, [
        "score",
        "challenge_total",
        "challenge_solved",
        "flag_total",
        "flag_correct"
      ]);
      dimension.challenges = Array.isArray(source.challenges)
        ? source.challenges.slice(0, 150).flatMap((challenge) => {
            if (!challenge || typeof challenge !== "object" || Array.isArray(challenge)) return [];
            const clean = {};
            assignText(clean, challenge, ["name", "difficulty", "state"]);
            assignNumbers(clean, challenge, [
              "correct_flag_count",
              "total_flag_count",
              "total_score",
              "level"
            ]);
            return [clean];
          })
        : [];
      return [dimension];
    });
  };

  const sanitizeRun = (source, routeRunId) => {
    if (!source || typeof source !== "object" || Array.isArray(source)) return {};
    const run = {};
    assignText(run, source, [
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
      "ended_at"
    ]);
    assignNumbers(run, source, [
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
      "pending_remain_seconds"
    ]);
    if (run.run_id === undefined && routeRunId) run.run_id = routeRunId;

    if (Array.isArray(source.base_model)) {
      run.base_model = source.base_model.slice(0, 16).map((item) => text(item, 80)).filter(Boolean);
    }
    if (source.radar_data && typeof source.radar_data === "object" && !Array.isArray(source.radar_data)) {
      run.radar_data = {};
      for (const [key, value] of Object.entries(source.radar_data).slice(0, 16)) {
        const cleanKey = text(key, 80);
        const cleanValue = number(value);
        if (cleanKey && cleanValue !== undefined) run.radar_data[cleanKey] = cleanValue;
      }
    }
    run.score_events = sanitizeScoreEvents(source.score_events);
    run.run_events = sanitizeRunEvents(source.run_events);
    run.matrix = sanitizeMatrix(source.matrix);
    return run;
  };

  const sanitizeTimeline = (source, routeRunId) => {
    const container = source && typeof source === "object" ? source : {};
    const rawItems = Array.isArray(container)
      ? container
      : container.items ?? container.timeline ?? container.score_timeline;
    const payload = { run_id: integer(container.run_id) ?? routeRunId, items: [] };
    if (!Array.isArray(rawItems)) return payload;
    payload.items = rawItems.slice(0, 500).flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const clean = {};
      const challengeId = integer(item.challenge_id);
      if (challengeId !== undefined) clean.challenge_id = challengeId;
      assignText(clean, item, ["timestamp", "challenge_code", "challenge_name"]);
      assignNumbers(clean, item, ["challenge_score", "cumulative_score"]);
      return [clean];
    });
    return payload;
  };

  const unwrap = (raw) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return raw;
    if (raw.data && typeof raw.data === "object") return raw.data;
    if (raw.result && typeof raw.result === "object") return raw.result;
    return raw;
  };

  const classify = (input, method = "GET") => {
    let url;
    try {
      url = new URL(input, location.href);
    } catch {
      return null;
    }
    if (url.origin !== TARGET_ORIGIN || !url.pathname.startsWith(API_PREFIX)) return null;
    const normalizedMethod = String(method || "GET").toUpperCase();
    if (url.pathname === `${API_PREFIX}/runs/active` && normalizedMethod === "GET") {
      return { eventType: "active_run" };
    }
    if (url.pathname === `${API_PREFIX}/runs` && normalizedMethod === "POST") {
      return { eventType: "run_created" };
    }
    if (url.pathname === `${API_PREFIX}/my/profile` && normalizedMethod === "GET") {
      return { eventType: "account_hint" };
    }
    const match = url.pathname.match(/^\/api\/v1\/runs\/(\d+)\/(status|result|score-timeline)$/);
    if (!match || normalizedMethod !== "GET") return null;
    const eventType = {
      status: "run_status",
      result: "run_result",
      "score-timeline": "score_timeline"
    }[match[2]];
    return { eventType, runId: Number(match[1]) };
  };

  const buildPayload = (descriptor, raw) => {
    const source = unwrap(raw);
    if (descriptor.eventType === "account_hint") {
      const profile = source?.profile ?? source?.user ?? source;
      const hint = {};
      assignText(hint, profile, ["username", "org", "agent_tag"]);
      return hint;
    }
    if (descriptor.eventType === "active_run") {
      const active = source?.active_run ?? source?.run ?? source;
      return { active_run: active ? sanitizeRun(active) : null };
    }
    if (descriptor.eventType === "score_timeline") {
      return sanitizeTimeline(source, descriptor.runId);
    }
    return sanitizeRun(source, descriptor.runId);
  };

  const emit = (descriptor, raw) => {
    try {
      const payload = buildPayload(descriptor, raw);
      window.postMessage(
        {
          source: SOURCE,
          schemaVersion: 1,
          eventType: descriptor.eventType,
          payload
        },
        location.origin
      );
    } catch {
      // Observation must never interfere with the benchmark page.
    }
  };

  const readResponse = async (response, descriptor) => {
    if (!response?.ok) return;
    const contentType = response.headers?.get("content-type") || "";
    if (!contentType.toLowerCase().includes("application/json")) return;
    const length = Number(response.headers?.get("content-length") || 0);
    if (length > MAX_RESPONSE_BYTES) return;
    try {
      emit(descriptor, await response.clone().json());
    } catch {
      // Malformed/streamed responses are simply ignored.
    }
  };

  const nativeFetch = window.fetch;
  window.fetch = function supervisedFetch(input, init) {
    const requestUrl = input instanceof Request ? input.url : input;
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    const descriptor = classify(requestUrl, method);
    const promise = nativeFetch.apply(this, arguments);
    if (descriptor) promise.then((response) => readResponse(response, descriptor)).catch(() => {});
    return promise;
  };

  const nativeOpen = XMLHttpRequest.prototype.open;
  const nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function supervisedOpen(method, url) {
    this.__tsecSupervisorDescriptor = classify(url, method);
    return nativeOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function supervisedSend() {
    const descriptor = this.__tsecSupervisorDescriptor;
    if (descriptor) {
      this.addEventListener(
        "load",
        () => {
          if (this.status < 200 || this.status >= 300) return;
          const contentType = this.getResponseHeader("content-type") || "";
          if (!contentType.toLowerCase().includes("application/json")) return;
          const raw = typeof this.response === "object" && this.response !== null
            ? this.response
            : this.responseText;
          if (typeof raw === "string" && raw.length > MAX_RESPONSE_BYTES) return;
          try {
            emit(descriptor, typeof raw === "string" ? JSON.parse(raw) : raw);
          } catch {
            // Ignore malformed responses.
          }
        },
        { once: true }
      );
    }
    return nativeSend.apply(this, arguments);
  };
})();
