import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";


const backgroundSource = await readFile(new URL("../background.js", import.meta.url), "utf8");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));


const createHarness = (initial = {}) => {
  const store = { ...initial };
  const requests = [];
  let messageListener;
  const chrome = {
    storage: {
      local: {
        async get(keys) {
          return Object.fromEntries(keys.flatMap((key) => key in store ? [[key, store[key]]] : []));
        },
        async set(changes) {
          Object.assign(store, structuredClone(changes));
        }
      }
    },
    alarms: {
      async create() {},
      onAlarm: { addListener() {} }
    },
    runtime: {
      getManifest: () => ({ homepage_url: "https://scores.example.org" }),
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: {
        addListener(listener) { messageListener = listener; }
      }
    },
    action: {
      async setBadgeText() {},
      async setBadgeBackgroundColor() {},
      async setTitle() {}
    }
  };
  const fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, status: 202, headers: new Headers() };
  };
  vm.runInNewContext(backgroundSource, {
    chrome,
    crypto: webcrypto,
    fetch,
    URL,
    TextEncoder,
    Headers,
    structuredClone,
    console
  }, { filename: "background.js" });

  const send = (message) => new Promise((resolve) => {
    const keepOpen = messageListener(message, {}, resolve);
    assert.equal(keepOpen, true);
  });
  return { store, requests, send };
};


test("background queues only after explicit consent and never serializes participant token", async () => {
  const harness = createHarness();
  await tick();
  const token = `tsp_${"a".repeat(32)}`;
  const saved = await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: token,
      consent: true,
      enabled: true
    }
  });
  assert.equal(saved.ok, true);

  const telemetry = {
    kind: "telemetry",
    eventType: "run_status",
    observedAt: "2026-09-18T09:00:00Z",
    context: {
      extension_version: "0.1.0",
      page_path: "/benchmark/running/88",
      page_visibility: "visible",
      device: { platform: "Windows", browser: "Chrome" }
    },
    payload: { run_id: 88, current_score: 12, max_score: 100 }
  };
  const accepted = await harness.send(telemetry);
  assert.equal(accepted.result.accepted, true);
  await tick();
  await tick();

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(harness.requests[0].options.body.includes(token), false);
  const uploaded = JSON.parse(harness.requests[0].options.body);
  assert.equal(uploaded.context.device.platform, "Windows");
  assert.equal(uploaded.payload.current_score, 12);
  assert.deepEqual(harness.store.queue || [], []);
});


test("background rejects forbidden keys before queueing or networking", async () => {
  const harness = createHarness();
  await tick();
  await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: `tsp_${"b".repeat(32)}`,
      consent: true,
      enabled: true
    }
  });

  const rejected = await harness.send({
    kind: "telemetry",
    eventType: "run_status",
    observedAt: "2026-09-18T09:00:00Z",
    context: {
      extension_version: "0.1.0",
      page_path: "/benchmark/running/88",
      page_visibility: "visible",
      device: {}
    },
    payload: {
      run_id: 88,
      submitted_flag: "flag{must-never-upload}"
    }
  });
  assert.equal(rejected.result.accepted, false);
  assert.equal(rejected.result.reason, "sensitive_field");
  assert.equal(harness.requests.length, 0);
  assert.deepEqual(harness.store.queue || [], []);
});


test("background rejects unknown fields even when their names do not look sensitive", async () => {
  const harness = createHarness();
  await tick();
  await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: `tsp_${"d".repeat(32)}`,
      consent: true,
      enabled: true
    }
  });
  const rejected = await harness.send({
    kind: "telemetry",
    eventType: "run_status",
    observedAt: "2026-09-18T09:00:00Z",
    context: {
      extension_version: "0.1.0",
      page_path: "/benchmark/running/88",
      page_visibility: "visible",
      device: {}
    },
    payload: { run_id: 88, unknown_private_blob: "do-not-upload" }
  });
  assert.equal(rejected.result.reason, "unexpected_field");
  assert.equal(harness.requests.length, 0);
});


test("withdrawing consent disables collection", async () => {
  const harness = createHarness();
  await tick();
  await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: `tsp_${"c".repeat(32)}`,
      consent: false,
      enabled: false
    }
  });
  const result = await harness.send({
    kind: "telemetry",
    eventType: "heartbeat",
    observedAt: "2026-09-18T09:00:00Z",
    context: {
      extension_version: "0.1.0",
      page_path: "/benchmark/running/99",
      page_visibility: "visible",
      device: {}
    },
    payload: { run_id: 99, route: "/benchmark/running/99" }
  });
  assert.equal(result.result.reason, "not_configured");
  assert.equal(harness.requests.length, 0);
});


test("background accepts organiser-issued tokens and rejects malformed ones", async () => {
  const harness = createHarness();
  await tick();
  const saved = await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: "player-demo-01",
      consent: true,
      enabled: true
    }
  });
  assert.equal(saved.ok, true);
  assert.equal(harness.store.participantToken, "player-demo-01");

  const legacy = await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: `tsp_${"e".repeat(32)}`,
      consent: true,
      enabled: true
    }
  });
  assert.equal(legacy.ok, true);

  const tooShort = await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: "abc",
      consent: true,
      enabled: true
    }
  });
  assert.equal(tooShort.ok, false);
  assert.equal(tooShort.error, "参赛者令牌格式无效");

  const badChars = await harness.send({
    kind: "saveOptions",
    options: {
      serverUrl: "https://scores.example.org",
      participantToken: "令牌 无效",
      consent: true,
      enabled: true
    }
  });
  assert.equal(badChars.ok, false);
  assert.equal(badChars.error, "参赛者令牌格式无效");
});

test("changing organiser starts with a new identity and requires consent", async () => {
  const harness = createHarness({
    serverUrl: "https://previous.example.org",
    participantToken: "old-credential",
    installationId: "previous-installation",
    consent: true,
    queue: [{ event: { event_id: "previous-event" } }]
  });
  await tick();
  const { result } = await harness.send({ kind: "getStatus" });
  assert.equal(result.serverUrl, "https://scores.example.org");
  assert.equal(result.configured, false);
  assert.equal(result.consent, false);
  assert.equal(result.queueSize, 0);
  assert.notEqual(harness.store.installationId, "previous-installation");
  assert.equal(harness.requests.length, 0);
});
