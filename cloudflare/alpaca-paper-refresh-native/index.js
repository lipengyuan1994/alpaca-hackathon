const SERVICE_NAME = "alpaca-paper-refresh-native";
const DISPATCH_URL =
  "https://api.github.com/repos/lipengyuan1994/alpaca-hackathon/actions/workflows/pages.yml/dispatches";
const CRON = "0,30 * * * MON-FRI";
// Optional delivery probe: never sends a GitHub request, even if left configured.
const DIAGNOSTIC_CRON = "* * * * *";

const eastern = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function easternParts(timestamp) {
  return Object.fromEntries(
    eastern
      .formatToParts(new Date(timestamp))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}

function insideRefreshWindow(parts) {
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return (
    ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(parts.weekday) &&
    minutes >= 9 * 60 &&
    minutes <= 17 * 60
  );
}

function easternLabel(parts) {
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second} America/New_York`;
}

async function dispatchWorkflow(scheduledTime, env) {
  const parts = easternParts(scheduledTime);
  const scheduledEastern = easternLabel(parts);
  if (!insideRefreshWindow(parts)) {
    console.log(
      JSON.stringify({
        event: "dispatch_skipped",
        service: SERVICE_NAME,
        scheduledTime,
        scheduledEastern,
        reason: "outside_refresh_window",
      }),
    );
    return { dispatched: false, reason: "outside_refresh_window", scheduledEastern };
  }

  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN is not configured");
  }

  const response = await fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "cloudflare-worker-alpaca-paper-refresh",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  const githubRequestId = response.headers.get("x-github-request-id");

  if (!response.ok) {
    const responseBody = (await response.text()).slice(0, 300);
    console.error(
      JSON.stringify({
        event: "github_dispatch_failed",
        service: SERVICE_NAME,
        scheduledTime,
        scheduledEastern,
        githubStatus: response.status,
        githubRequestId,
        responseBody,
      }),
    );
    throw new Error(`GitHub workflow dispatch failed with status ${response.status}`);
  }

  console.log(
    JSON.stringify({
      event: "github_dispatch_succeeded",
      service: SERVICE_NAME,
      scheduledTime,
      scheduledEastern,
      githubStatus: response.status,
      githubRequestId,
    }),
  );
  return { dispatched: true, githubStatus: response.status, githubRequestId, scheduledEastern };
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

async function authorized(request, env) {
  const supplied = request.headers.get("authorization") || "";
  const expected = `Bearer ${env.GITHUB_TOKEN || ""}`;
  const [suppliedHash, expectedHash] = await Promise.all([sha256(supplied), sha256(expected)]);
  let difference = suppliedHash.length ^ expectedHash.length;
  for (let index = 0; index < suppliedHash.length; index += 1) {
    difference |= suppliedHash[index] ^ expectedHash[index];
  }
  return Boolean(env.GITHUB_TOKEN) && difference === 0;
}

function jsonResponse(body, status = 200) {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

export default {
  async scheduled(controller, env) {
    console.log(JSON.stringify({
      event: "scheduled_received",
      service: SERVICE_NAME,
      cron: controller.cron,
      scheduledTime: controller.scheduledTime,
    }));
    if (controller.cron === DIAGNOSTIC_CRON) {
      console.log(JSON.stringify({ event: "scheduler_probe", service: SERVICE_NAME }));
      return;
    }
    await dispatchWorkflow(controller.scheduledTime ?? Date.now(), env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/healthz")) {
      return jsonResponse({
        ok: true,
        service: SERVICE_NAME,
        handler: "scheduled",
        cron: CRON,
        refreshWindow: "Monday-Friday, 9:00 AM-5:00 PM America/New_York",
      });
    }

    if (request.method === "POST" && url.pathname === "/dispatch") {
      if (!(await authorized(request, env))) {
        return jsonResponse({ ok: false, error: "unauthorized" }, 401);
      }
      try {
        const result = await dispatchWorkflow(Date.now(), env);
        return jsonResponse({ ok: true, ...result });
      } catch (error) {
        console.error(
          JSON.stringify({
            event: "manual_dispatch_failed",
            service: SERVICE_NAME,
            error: error instanceof Error ? error.message : String(error),
          }),
        );
        return jsonResponse({ ok: false, error: "dispatch_failed" }, 502);
      }
    }

    return jsonResponse({ ok: false, error: "not_found" }, 404);
  },
};
