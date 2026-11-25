(() => {
  "use strict";

  const SOURCE = "tsec-supervisor-page";
  const ALLOWED_EVENTS = new Set([
    "active_run",
    "run_created",
    "run_status",
    "run_result",
    "score_timeline",
    "account_hint"
  ]);
  const VERSION = chrome.runtime.getManifest().version;

  const screenBucket = () => {
    const width = Math.max(window.screen?.width || 0, window.screen?.height || 0);
    if (width < 900) return "small";
    if (width < 1600) return "medium";
    if (width < 2560) return "large";
    return "xlarge";
  };

  const browserLabel = () => {
    const brands = navigator.userAgentData?.brands;
    if (Array.isArray(brands)) {
      return brands.map((brand) => `${brand.brand} ${brand.version}`).join(", ").slice(0, 96);
    }
    return "Chrome";
  };

  const chromeMajor = () => {
    const brand = navigator.userAgentData?.brands?.find((item) => /Chrom/i.test(item.brand));
    const fromBrand = Number.parseInt(brand?.version || "", 10);
    if (Number.isFinite(fromBrand)) return fromBrand;
    const match = navigator.userAgent.match(/(?:Chrome|Chromium)\/(\d+)/);
    return match ? Number(match[1]) : undefined;
  };

  const device = {
    platform: String(navigator.userAgentData?.platform || navigator.platform || "unknown").slice(0, 64),
    browser: browserLabel(),
    chrome_major: chromeMajor(),
    screen_bucket: screenBucket(),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone?.slice(0, 64),
    locale: String(navigator.language || "unknown").slice(0, 32),
    hardware_concurrency: navigator.hardwareConcurrency || undefined,
    device_memory_gb: navigator.deviceMemory || undefined
  };

  const pageVisibility = () => {
    const value = document.visibilityState;
    return ["visible", "hidden", "prerender"].includes(value) ? value : "unknown";
  };

  const context = () => ({
    extension_version: VERSION,
    page_path: location.pathname.slice(0, 500),
    page_visibility: pageVisibility(),
    device
  });

  const send = (eventType, payload) => {
    chrome.runtime.sendMessage({
      kind: "telemetry",
      eventType,
      payload,
      observedAt: new Date().toISOString(),
      context: context()
    }).catch(() => {
      // The background worker may be restarting; the next status poll/heartbeat retries naturally.
    });
  };

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const message = event.data;
    if (!message || message.source !== SOURCE || message.schemaVersion !== 1) return;
    if (!ALLOWED_EVENTS.has(message.eventType)) return;
    if (!message.payload || typeof message.payload !== "object" || Array.isArray(message.payload)) return;
    send(message.eventType, message.payload);
  });

  const runIdFromPath = () => {
    const match = location.pathname.match(/\/(?:benchmark\/running|agent)\/(\d+)/);
    return match ? Number(match[1]) : undefined;
  };

  const heartbeat = () => {
    const runId = runIdFromPath();
    if (!runId) return;
    send("heartbeat", {
      run_id: runId,
      route: location.pathname
    });
  };

  heartbeat();
  setInterval(heartbeat, 30_000);
  document.addEventListener("visibilitychange", heartbeat);
})();
