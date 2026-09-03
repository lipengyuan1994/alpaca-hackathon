import assert from "node:assert/strict";
import test from "node:test";

import worker from "./index.js";

const env = { GITHUB_TOKEN: "test-only-token" };

test("scheduled dispatch respects Eastern weekday and time boundaries", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return new Response(null, { status: 204, headers: { "x-github-request-id": "test-request" } });
  });
  t.mock.method(console, "log", () => {});

  const cases = [
    ["2026-09-03T08:59:00-04:00", false],
    ["2026-09-03T09:00:00-04:00", true],
    ["2026-09-03T14:30:00-04:00", true],
    ["2026-09-03T17:00:00-04:00", true],
    ["2026-09-03T17:01:00-04:00", false],
    ["2026-09-05T12:00:00-04:00", false],
    ["2026-12-03T09:00:00-05:00", true],
  ];
  for (const [timestamp, expectedDispatch] of cases) {
    const before = calls.length;
    await worker.scheduled({ scheduledTime: Date.parse(timestamp) }, env);
    assert.equal(calls.length - before, expectedDispatch ? 1 : 0, timestamp);
  }
  for (const { url, options } of calls) {
    assert.equal(url, "https://api.github.com/repos/lipengyuan1994/alpaca-hackathon/actions/workflows/pages.yml/dispatches");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.Authorization, "Bearer test-only-token");
    assert.deepEqual(JSON.parse(options.body), { ref: "main" });
  }
});

test("health endpoint does not dispatch or reveal the secret", async (t) => {
  let fetchCalls = 0;
  t.mock.method(globalThis, "fetch", async () => { fetchCalls += 1; });
  const response = await worker.fetch(new Request("https://worker.example/healthz"), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const body = await response.text();
  assert.equal(JSON.parse(body).ok, true);
  assert.equal(body.includes(env.GITHUB_TOKEN), false);
  assert.equal(fetchCalls, 0);
});

test("manual endpoint rejects missing or incorrect authorization", async (t) => {
  let fetchCalls = 0;
  t.mock.method(globalThis, "fetch", async () => { fetchCalls += 1; });
  for (const authorization of ["", "Bearer wrong-token"]) {
    const request = new Request("https://worker.example/dispatch", {
      method: "POST",
      headers: { authorization },
    });
    const response = await worker.fetch(request, env);
    assert.equal(response.status, 401);
  }
  assert.equal(fetchCalls, 0);
});

test("authenticated manual endpoint uses the same dispatch path", async (t) => {
  t.mock.method(Date, "now", () => Date.parse("2026-09-03T14:30:00-04:00"));
  t.mock.method(console, "log", () => {});
  t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 204 }));
  const response = await worker.fetch(new Request("https://worker.example/dispatch", {
    method: "POST",
    headers: { authorization: "Bearer test-only-token" },
  }), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).dispatched, true);
});

test("scheduled GitHub failure rejects instead of reporting success", async (t) => {
  t.mock.method(console, "error", () => {});
  t.mock.method(globalThis, "fetch", async () => new Response('{"message":"Forbidden"}', { status: 403 }));
  await assert.rejects(
    worker.scheduled({ scheduledTime: Date.parse("2026-09-03T14:30:00-04:00") }, env),
    /GitHub workflow dispatch failed with status 403/,
  );
});

test("missing secret fails closed during the refresh window", async () => {
  await assert.rejects(
    worker.scheduled({ scheduledTime: Date.parse("2026-09-03T14:30:00-04:00") }, {}),
    /GITHUB_TOKEN is not configured/,
  );
});
