# Alpaca paper refresh Worker

For the complete broker-to-dashboard flow, GitHub fallback, credential boundaries,
freshness semantics, and operator troubleshooting, see the
[paper performance refresh runbook](../../docs/deployment/PAPER_PERFORMANCE_REFRESH.md).

This Cloudflare Worker uses a **SQLite-backed Durable Object alarm** to dispatch
`.github/workflows/pages.yml` at :00 and :30, Monday-Friday, from 9:00 AM through
5:00 PM `America/New_York`. It survives laptop shutdown and Worker eviction.
It does not rely on Cron Triggers. SQLite-backed Durable Objects are supported
on the [Workers Free plan](https://developers.cloudflare.com/durable-objects/platform/pricing/).

The `GITHUB_TOKEN` secret must be a fine-grained GitHub token with Actions write
access to `lipengyuan1994/alpaca-hackathon`. It is configured in Cloudflare and
must never be committed to this directory.

- `GET /` or `GET /healthz` returns non-sensitive deployment health.
- `POST /dispatch` performs an immediate, in-window dispatch and requires the
  exact `GITHUB_TOKEN` as a Bearer token.
- `GET /scheduler/status` returns persistent enabled/last-result/next-alarm state.
- `POST /scheduler/start` idempotently starts the timer. A newly started timer
  performs one automatic validation refresh after ten seconds if in-window,
  then continues at exact :00/:30 slots.
- `POST /scheduler/stop` disables the timer and cancels its alarm.
- All `/scheduler/*` endpoints require the same Bearer authentication as
  `/dispatch`; health GETs are read-only and never start or keep alive the timer.

## Deployment and verification

Deploy `wrangler.jsonc` including the Durable Object binding and migration,
preserving the `GITHUB_TOKEN` secret. Authenticate and POST `/scheduler/start`
once. Do not put token values in source, shell history, logs, or this document.

Verify `lastOutcome: dispatched`, `lastGithubRequestId`, and a future `alarmAt`
through the authenticated status endpoint. Match the alarm event to a new
GitHub Pages run and its published JSON timestamp. Logs use
`alarm_refresh_completed` / `alarm_refresh_failed` and Workers event type
`alarm`. **Cron Events will be empty by design**: production has `crons: []`,
and stale Cron invocations are ignored to prevent competing schedulers.

The GitHub dispatch has a 20-second timeout. Failures are recorded persistently
and retried up to three times at one-minute intervals within the monitoring
window, then resume at the next normal slot. Duplicate deliveries after a
successful slot are skipped. An interruption after GitHub accepts a dispatch
but before the success record commits can still cause a duplicate refresh;
this is a read-only snapshot workflow, never an order submission.

Run tests using native ARM64 Node. CI runs both the original dispatch tests and
Durable Object tests in Cloudflare's runtime:

```sh
npm ci --prefix cloudflare/alpaca-paper-refresh-native
npm test --prefix cloudflare/alpaca-paper-refresh-native
```
