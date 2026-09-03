import { env, exports } from "cloudflare:workers";
import { runDurableObjectAlarm, runInDurableObject } from "cloudflare:test";
import { afterEach, expect, it, vi } from "vitest";
import { Scheduler, nextRefreshSlot } from "./scheduler.js";

afterEach(() => vi.restoreAllMocks());

function fakeStorage() {
  const values = new Map();
  return {
    alarmAt: null,
    async get(key) { return structuredClone(values.get(key)); },
    async put(key, value) { values.set(key, structuredClone(value)); },
    async getAlarm() { return this.alarmAt; },
    async setAlarm(at) { this.alarmAt = at; },
    async deleteAlarm() { this.alarmAt = null; },
    async transaction(fn) { return fn(this); },
  };
}

it("calculates half-hour weekday slots across weekends and DST", () => {
  for (const [from, to] of [
    ["2026-09-03T15:59:00-04:00", "2026-09-03T16:00:00-04:00"],
    ["2026-09-03T16:30:00-04:00", "2026-09-03T17:00:00-04:00"],
    ["2026-09-04T17:00:00-04:00", "2026-09-07T09:00:00-04:00"],
    ["2026-10-30T17:00:00-04:00", "2026-11-02T09:00:00-05:00"],
    ["2026-03-06T17:00:00-05:00", "2026-03-09T09:00:00-04:00"],
  ]) expect(nextRefreshSlot(Date.parse(from))).toBe(Date.parse(to));
});

it("starts idempotently, fires once, rearms, and suppresses duplicate delivery", async () => {
  let now = Date.parse("2026-09-03T15:55:00-04:00");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, {
    status: 204, headers: { "x-github-request-id": "alarm-test-id" },
  }));
  const storage = fakeStorage();
  const scheduler = new Scheduler(storage, { GITHUB_TOKEN: "test" });
  const started = await scheduler.start();
  expect(started.nextRunAt).toBe(now + 10000);
  expect((await scheduler.start()).nextRunAt).toBe(started.nextRunAt);
  expect(fetch).not.toHaveBeenCalled();
  now += 10000;
  await scheduler.alarm();
  const status = await scheduler.status();
  expect(status.lastOutcome).toBe("dispatched");
  expect(status.lastGithubRequestId).toBe("alarm-test-id");
  expect(status.alarmAt).toBe(Date.parse("2026-09-03T16:00:00-04:00"));
  await scheduler.alarm();
  expect(fetch).toHaveBeenCalledTimes(1);
  await scheduler.stop();
  expect((await scheduler.status()).alarmAt).toBeNull();
});

it("bounded failure retries cannot strand the next normal refresh", async () => {
  let now = Date.parse("2026-09-03T14:00:00-04:00");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(null, { status: 403 }));
  const scheduler = new Scheduler(fakeStorage(), { GITHUB_TOKEN: "test" });
  await scheduler.start();
  for (let attempt = 1; attempt <= 4; attempt++) {
    now = (await scheduler.status()).nextRunAt;
    await scheduler.alarm();
    expect((await scheduler.status()).lastOutcome).toBe("failed");
  }
  expect((await scheduler.status()).nextRunAt).toBe(Date.parse("2026-09-03T14:30:00-04:00"));
});

it("a delayed weekend alarm skips GitHub and moves to Monday", async () => {
  let now = Date.parse("2026-09-04T16:59:00-04:00");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Must not fetch"));
  const scheduler = new Scheduler(fakeStorage(), {});
  await scheduler.start();
  now = Date.parse("2026-09-05T12:00:00-04:00");
  await scheduler.alarm();
  expect(fetch).not.toHaveBeenCalled();
  expect((await scheduler.status()).nextRunAt).toBe(Date.parse("2026-09-07T09:00:00-04:00"));
});

it("stopping during a dispatch never re-enables the timer", async () => {
  let now = Date.parse("2026-09-03T14:00:00-04:00");
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const scheduler = new Scheduler(fakeStorage(), { GITHUB_TOKEN: "test" });
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
    await scheduler.stop();
    return new Response(null, { status: 204 });
  });
  await scheduler.start();
  now += 10000;
  await scheduler.alarm();
  expect((await scheduler.status()).enabled).toBe(false);
  expect((await scheduler.status()).alarmAt).toBeNull();
});

it("the actual Durable Object persists, runs an alarm, and rearms", async () => {
  const stub = env.PAPER_REFRESH_SCHEDULER.getByName("integration-" + crypto.randomUUID());
  await stub.start();
  await runInDurableObject(stub, async (instance, state) => {
    const saved = await state.storage.get("scheduler");
    await state.storage.put("scheduler", { ...saved, nextRunAt: Date.now() - 1000 });
    await state.storage.setAlarm(Date.now() + 86400000);
    // Replace only the dispatcher engine environment to ensure no real GitHub call.
    instance.scheduler.env = {};
  });
  expect(await runDurableObjectAlarm(stub)).toBe(true);
  const result = await stub.status();
  expect(result.lastAlarmAt).toBeGreaterThan(0);
  expect(["failed", "skipped"]).toContain(result.lastOutcome);
  expect(result.alarmAt).toBeGreaterThan(result.lastAlarmAt);
  await stub.stop();
});

it("scheduler control endpoints require authentication", async () => {
  for (const [path, method] of [["status", "GET"], ["start", "POST"], ["stop", "POST"]]) {
    const response = await exports.default.fetch(`https://worker.test/scheduler/${path}`, { method });
    expect(response.status).toBe(401);
  }
  const health = await exports.default.fetch("https://worker.test/healthz");
  expect((await health.json()).scheduler).toBe("durable-object-alarm");
});
