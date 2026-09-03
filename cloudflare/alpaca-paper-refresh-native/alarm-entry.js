import { DurableObject } from "cloudflare:workers";
import worker, { authorized, jsonResponse } from "./index.js";
import { Scheduler } from "./scheduler.js";

export class PaperRefreshScheduler extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.scheduler = new Scheduler(this.ctx.storage, this.env);
  }
  async start() { return this.scheduler.start(); }
  async stop() { return this.scheduler.stop(); }
  async status() { return this.scheduler.status(); }
  async alarm() { await this.scheduler.alarm(); }
}

const JOB = "lipengyuan1994/alpaca-hackathon:pages.yml";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/scheduler/")) {
      if (!(await authorized(request, env))) return jsonResponse({ error: "unauthorized" }, 401);
      const scheduler = env.PAPER_REFRESH_SCHEDULER.getByName(JOB);
      if (request.method === "GET" && url.pathname === "/scheduler/status") {
        return jsonResponse(await scheduler.status());
      }
      if (request.method === "POST" && url.pathname === "/scheduler/start") {
        return jsonResponse(await scheduler.start());
      }
      if (request.method === "POST" && url.pathname === "/scheduler/stop") {
        return jsonResponse(await scheduler.stop());
      }
      return jsonResponse({ error: "not_found" }, 404);
    }
    if (request.method === "GET" && ["/", "/healthz"].includes(url.pathname)) {
      return jsonResponse({
        ok: true, service: "alpaca-paper-refresh-native", scheduler: "durable-object-alarm",
        refreshWindow: "Monday-Friday, every 30 minutes, 9:00 AM-5:00 PM America/New_York",
        statusEndpoint: "/scheduler/status (authentication required)",
      });
    }
    return worker.fetch(request, env);
  },
  // Ignore stale Cron registrations during cutover; only the durable timer owns automation.
  async scheduled(controller) {
    console.log(JSON.stringify({ event: "legacy_cron_ignored", cron: controller.cron }));
  },
};
