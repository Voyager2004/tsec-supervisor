import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";


const hookSource = await readFile(new URL("../page-hook.js", import.meta.url), "utf8");


class FakeXMLHttpRequest {
  open() {}
  send() {}
  addEventListener() {}
  getResponseHeader() { return null; }
}


const installHook = (responseBody) => {
  const posted = [];
  const nativeFetch = async () => new Response(JSON.stringify(responseBody), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
  const context = {
    URL,
    Request,
    Response,
    XMLHttpRequest: FakeXMLHttpRequest,
    location: {
      href: "https://tsecbench.zc.tencent.com/benchmark/running/123",
      origin: "https://tsecbench.zc.tencent.com"
    },
    fetch: nativeFetch,
    postMessage: (message, origin) => posted.push({ message, origin })
  };
  context.window = context;
  vm.runInNewContext(hookSource, context, { filename: "page-hook.js" });
  return { context, posted };
};


const waitForObserver = () => new Promise((resolve) => setTimeout(resolve, 0));


test("status observation retains scoring fields and strips all unlisted secrets", async () => {
  const raw = {
    run_id: 123,
    status: "running",
    username: "alice",
    current_score: 25,
    max_score: 100,
    benchmark_token: "secret-benchmark-token",
    vpn_config: "secret-vpn-config",
    container_addr: "10.0.0.9",
    flag: "flag{must_not_leave}",
    unknown_private_blob: "also-must-not-leave",
    score_events: [{
      id: 9,
      challenge_code: "web-1",
      score_delta: 25,
      submitted_flag: "flag{nested-secret}"
    }],
    run_events: [{
      id: 10,
      operation_type: "submit",
      extra: {
        awarded: 25,
        flag_index: 1,
        submitted_flag: "flag{deep-secret}",
        container_addr: "10.0.0.10"
      }
    }]
  };
  const { context, posted } = installHook(raw);

  const pageResponse = await context.fetch(
    "https://tsecbench.zc.tencent.com/api/v1/runs/123/status"
  );
  assert.deepEqual(await pageResponse.json(), raw, "the benchmark page still receives its full response");
  await waitForObserver();

  assert.equal(posted.length, 1);
  assert.equal(posted[0].origin, "https://tsecbench.zc.tencent.com");
  assert.equal(posted[0].message.eventType, "run_status");
  assert.equal(posted[0].message.payload.current_score, 25);
  assert.equal(posted[0].message.payload.score_events[0].score_delta, 25);
  assert.equal(posted[0].message.payload.run_events[0].awarded, 25);

  const serialized = JSON.stringify(posted[0].message);
  for (const forbidden of [
    "secret-benchmark-token",
    "secret-vpn-config",
    "10.0.0.9",
    "10.0.0.10",
    "flag{must_not_leave}",
    "flag{nested-secret}",
    "flag{deep-secret}",
    "unknown_private_blob"
  ]) {
    assert.equal(serialized.includes(forbidden), false, `leaked: ${forbidden}`);
  }
});


test("non-whitelisted endpoints are never observed", async () => {
  const { context, posted } = installHook({ token: "profile-secret" });
  await context.fetch("https://tsecbench.zc.tencent.com/api/v1/my/agents/options");
  await context.fetch("https://example.com/api/v1/runs/123/status");
  await waitForObserver();
  assert.equal(posted.length, 0);
});


test("profile observation keeps only competition account hints", async () => {
  const { context, posted } = installHook({
    data: {
      user: {
        username: "alice",
        org: "red-team",
        agent_tag: "agent-a",
        email: "private@example.com",
        phone: "private-phone-value"
      }
    }
  });
  await context.fetch("https://tsecbench.zc.tencent.com/api/v1/my/profile");
  await waitForObserver();
  assert.deepEqual(
    JSON.parse(JSON.stringify(posted[0].message.payload)),
    { username: "alice", org: "red-team", agent_tag: "agent-a" }
  );
});
