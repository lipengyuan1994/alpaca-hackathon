import { dispatchWorkflow, easternParts, insideRefreshWindow } from "./index.js";

const HALF_HOUR = 30 * 60 * 1000;

export function nextRefreshSlot(after) {
  let candidate = (Math.floor(after / HALF_HOUR) + 1) * HALF_HOUR;
  // UTC stepping plus Eastern conversion handles weekends and DST changes.
  for (let i = 0; i < 8 * 48; i++, candidate += HALF_HOUR) {
    if (insideRefreshWindow(easternParts(candidate))) return candidate;
  }
  throw new Error("No refresh slot found within eight days");
}

// One engine per repository/workflow. Persistent storage owns all timer state.
export class Scheduler {
  constructor(storage, env) {
    this.storage = storage;
    this.env = env;
  }

  async status() {
    return {
      ...(await this.storage.get("scheduler") ?? { enabled: false }),
      alarmAt: await this.storage.getAlarm(),
    };
  }

  async saveAndArm(state) {
    await this.storage.transaction(async txn => {
      await txn.put("scheduler", state);
      if (state.enabled) await txn.setAlarm(state.nextRunAt);
      else await txn.deleteAlarm();
    });
  }

  async start() {
    const current = await this.storage.get("scheduler");
    if (current?.enabled) return this.status();
    const now = Date.now();
    // One timer-driven startup validation, then exact :00/:30 weekday slots.
    const validationAt = now + 10000;
    const nextRunAt = insideRefreshWindow(easternParts(validationAt))
      ? validationAt : nextRefreshSlot(now);
    await this.saveAndArm({
      ...current, enabled: true, generation: crypto.randomUUID(),
      startedAt: now, nextRunAt, attempts: 0,
    });
    console.log(JSON.stringify({ event: "alarm_scheduler_started", nextRunAt }));
    return this.status();
  }

  async stop() {
    const state = await this.storage.get("scheduler") ?? {};
    await this.saveAndArm({ ...state, enabled: false, generation: crypto.randomUUID() });
    return this.status();
  }

  async alarm() {
    const state = await this.storage.get("scheduler");
    if (!state?.enabled) return;
    const now = Date.now();
    // A duplicate delivery after success cannot repeat that completed slot.
    if (state.nextRunAt > now) {
      await this.storage.setAlarm(state.nextRunAt);
      return;
    }
    const dueAt = state.nextRunAt;
    let result;
    let failure;
    try {
      // Gate the actual firing time, not an old timestamp after an outage.
      result = await dispatchWorkflow(now, this.env);
    } catch (error) {
      failure = error instanceof Error ? error.message.slice(0, 200) : "Dispatch failed";
    }
    // stop()/start() may interleave while GitHub is responding. Never undo stop.
    const latest = await this.storage.get("scheduler");
    if (!latest?.enabled || latest.generation !== state.generation) return;
    const completedAt = Date.now();
    const attempts = failure ? (state.attempts ?? 0) + 1 : 0;
    const regularNext = nextRefreshSlot(completedAt);
    const retryAt = completedAt + 60000;
    const nextRunAt = failure && attempts <= 3 && insideRefreshWindow(easternParts(retryAt))
      ? Math.min(retryAt, regularNext) : regularNext;
    const updated = {
      ...state, nextRunAt, attempts: nextRunAt === regularNext ? 0 : attempts,
      lastAlarmAt: now, lastScheduledAt: dueAt,
      lastOutcome: failure ? "failed" : result.dispatched ? "dispatched" : "skipped",
      lastError: failure ?? null,
      ...(result?.dispatched ? {
        lastSuccessAt: completedAt, lastGithubRequestId: result.githubRequestId,
      } : {}),
    };
    await this.saveAndArm(updated);
    const log = failure ? console.error : console.log;
    log(JSON.stringify({
      event: failure ? "alarm_refresh_failed" : "alarm_refresh_completed",
      scheduledAt: dueAt, firedAt: now, nextRunAt,
      outcome: updated.lastOutcome, githubRequestId: result?.githubRequestId,
      error: failure,
    }));
  }
}
