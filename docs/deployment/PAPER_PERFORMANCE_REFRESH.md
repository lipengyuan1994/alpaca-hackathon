# Paper performance refresh: Cloudflare → GitHub Actions → GitHub Pages

This runbook describes the read-only publication pipeline for the
[paper performance dashboard](https://lipengyuan1994.github.io/alpaca-hackathon/paper-performance.html)
and the home-page account card. Implementation and automatic-run evidence were
checked on September 3, 2026.

The paper system went live on **Tuesday, September 1, 2026**. It operates with
real market data in an Alpaca **paper** account. Publishing its results is a
separate job: this pipeline does not start the trading loop, submit or cancel
orders, or authorize live-money trading. See the separate
[V13.5 paper execution runbook](PAPER_WHEEL_V13_5.md) for the trading process.

## End-to-end flow

```text
Cloudflare: persistent SQLite Durable Object alarm
  │ authenticated workflow_dispatch, ref=main
  ▼
GitHub Actions: .github/workflows/pages.yml
  │ GET account, closed orders, daily portfolio history from Alpaca paper
  ▼
public_snapshot.py: validate account → sanitize → hash → write JSON
  │ assemble HTML + assets and upload deployment artifact
  ▼
GitHub Pages: publish website and assets/data/live-paper-snapshot.json
  │ browser fetches deployed JSON on load and every 60 seconds
  ▼
Home-page account card + dedicated paper performance dashboard
```

Cloudflare stores the next wake-up durably. Neither an open browser nor a
running laptop is needed for refresh publication. The independent paper
execution service still has its own runtime requirements.

| Component | Source / identity | Responsibility |
|---|---|---|
| Worker | `alpaca-paper-refresh-native`; [entry point](../../cloudflare/alpaca-paper-refresh-native/alarm-entry.js) | Authenticated controls, health, Durable Object binding |
| Durable Object | `PaperRefreshScheduler`, binding `PAPER_REFRESH_SCHEDULER`; [scheduler](../../cloudflare/alpaca-paper-refresh-native/scheduler.js) | Persistent enabled state, alarm, retry and last-result records |
| Logical job | `lipengyuan1994/alpaca-hackathon:pages.yml` | Stable object identity for this one refresh job |
| Deployment | [wrangler.jsonc](../../cloudflare/alpaca-paper-refresh-native/wrangler.jsonc) | SQLite migration, binding, observability, no Cron Triggers |
| Publisher | [pages.yml](../../.github/workflows/pages.yml) | Fetch broker evidence, build site, deploy Pages |
| Snapshot builder | [public_snapshot.py](../../packages/paper_wheel/public_snapshot.py) | Account validation, allowlisted public fields, canonical artifact hash |
| Browser | [site.js](../assets/site.js) | Fetch published JSON, render metrics/history/fills, label freshness |

## Schedule and freshness

- **Primary:** Cloudflare Durable Object alarms at `:00` and `:30`, Monday–Friday,
  **9:00 AM through 5:00 PM inclusive**, in `America/New_York` (17 regular slots
  per weekday). Daylight saving time follows the IANA timezone. This is a
  weekday monitoring window, **not an exchange-holiday calendar**.
- **Startup:** enabling a stopped timer schedules one automatic validation
  attempt ten seconds later if that time is in-window; otherwise it schedules
  the next regular slot. Starting an already enabled timer is idempotent.
- **Fallback:** GitHub's own weekday schedule remains in `pages.yml`, with
  `America/New_York` specified. It is independent of Cloudflare and can be
  delayed. It uses half-hour slots from 09:00–16:30 plus 17:00.
- **Other triggers:** `workflow_dispatch` and qualifying pushes to `main`
  (website docs, snapshot builder, or the Pages workflow). A docs deployment
  can therefore refresh data outside the normal monitoring window.
- **Browser:** fetches the deployed JSON on load and every 60 seconds with
  cache bypass. This does not call Alpaca, trigger Actions, or restart an alarm.
- **Freshness:** `generated_at` is the broker snapshot generation time in UTC,
  not page-load time or deployment-completion time. A snapshot older than
  **90 minutes** is labeled stale during the publishing window; outside the
  window an expired snapshot is labeled off hours. Age is wall-clock time,
  not an accumulation of active trading minutes.

Thirty minutes is the target dispatch cadence, not a streaming-data guarantee.
GitHub queue/build/deploy time adds latency. The shared Pages concurrency group
uses `cancel-in-progress: true`; overlapping fallback, alarm, or push runs may
cancel an earlier run. Check the latest successful deployment, not just whether
one particular run was cancelled.

### Website-code caching

Both HTML pages reference `site.js` and `site.css` with `?v=` followed by the
first 12 characters of each file's SHA-256. When either asset changes, update
its version in both `docs/index.html` and `docs/paper-performance.html`.
`test_shared_assets_have_content_bound_cache_versions` enforces this in CI.
GitHub Pages can cache assets for ten minutes; polling fresh JSON does not
reload JavaScript in an already-open tab. After a website-code deployment,
reload the page (use a new page query string if the HTML itself is cached).

## Data and credential boundaries

Cloudflare has a `GITHUB_TOKEN` secret: a fine-grained token with Actions write
access to this repository. It posts to the GitHub workflow-dispatch endpoint
for `pages.yml` with `ref: main`. **HTTP 204 means accepted, not deployed.**
Cloudflare does not need the Alpaca API key or secret.

GitHub Actions supplies these repository secrets only to the snapshot step:
`ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET`, `ALPACA_PAPER_ACCOUNT_ID`, and
`ALPACA_PAPER_BASE_URL`. The base URL must remain the Alpaca paper endpoint.
The builder uses GET requests for:

- `/v2/account` — validates the returned account ID against the expected ID;
- `/v2/orders?status=closed&limit=500&direction=desc&nested=true` — selects filled
  system orders with client prefix `rs-v135-`, sorted newest first, limited to ten;
- `/v2/account/portfolio/history?period=1A&timeframe=1D&intraday_reporting=market_hours`
  — daily paper equity/P&L history, with up to 366 sanitized points.

The ten fills are **the most recent system fills, not the ten most profitable
trades**. Account/history metrics describe the account; the fills table filters
to the system prefix. The graph can be unavailable if the history request
fails while account and order reads succeed. The chart extends daily history
with `account.total_pnl` at `generated_at` when that valid snapshot is at least
as recent as the history. Its headline therefore matches the current account
cards; a dashed final segment and capture timestamp distinguish the snapshot
from daily observations. Same-timestamp observations are replaced rather than
duplicated. A lower or negative current P&L is included just like a higher one.
Without enough history, the current amount still displays but no trend is
invented. Daily-history drawdown remains a separately sampled metric, not a
continuous intraday drawdown.

The public JSON contains broker-reported paper balances, P&L, selected fills,
history, freshness metadata, and the account ID published with the owner's
approval. API credentials and broker order IDs are excluded. The canonical
SHA-256 artifact hash binds the snapshot contents; it is not a signature or
proof of profitability. Paper fills and short-history annualized calculations
are not guaranteed future returns or live-money results.

The workflow generates JSON in its checkout, atomically replaces the output,
and publishes it in the Pages artifact; it does **not** commit each snapshot
to Git. The checked-in JSON and a local preview can therefore be older than
the deployed JSON. Missing required secrets, account mismatch, or failure of
required account/order reads stops publication and leaves the previous site
deployment available.

## Operator controls

Worker base URL:
[alpaca-paper-refresh-native.lipengyuan-alpaca.workers.dev](https://alpaca-paper-refresh-native.lipengyuan-alpaca.workers.dev).

Use a trusted client that reads the approved secret into memory and supplies
`Authorization: Bearer <GITHUB_TOKEN>` without printing it. Do not paste a real
token into documentation, browser URLs, command-line arguments, logs, or shell
history. All scheduler endpoints below require authentication.

| Operation | Endpoint | Effect |
|---|---|---|
| Public health | `GET /healthz` | Reports service/configuration only; does not prove timer health or start it |
| Inspect | `GET /scheduler/status` | Read-only durable state and `alarmAt` |
| Enable | `POST /scheduler/start` | Starts a stopped timer; may cause the startup validation described above |
| Disable | `POST /scheduler/stop` | Persists disabled state and deletes the alarm; does not stop the GitHub fallback |
| Manual test | `POST /dispatch` | Immediate authenticated in-window dispatch; not proof of automatic execution |

Status timestamps are Unix milliseconds. Inspect `enabled`, `alarmAt`,
`nextRunAt`, `lastAlarmAt`, `lastScheduledAt`, `lastOutcome`, `lastError`,
`lastSuccessAt`, and `lastGithubRequestId`. `lastSuccessAt` means GitHub accepted
the dispatch, not that Pages finished. `alarmAt` may be null while the alarm
handler is executing; recheck after completion before declaring it stranded.

Stopping the timer cannot retract an already accepted GitHub run and does not
stop paper trading. To pause **all publication**, the owner must also address
the independent GitHub schedule and other workflow triggers explicitly.

## Failure handling and observability

The GitHub request has a 20-second timeout. A failed dispatch is recorded and
retried up to three times at one-minute intervals within the window, or at an
earlier regular slot; the scheduler then resumes normal slots. The next alarm
is persisted even after handled dispatch failures. A generation guard prevents
an in-flight request from undoing a concurrent stop/restart.

Known completed duplicate deliveries are skipped. A crash after GitHub accepts
the request but before success is persisted can still produce a duplicate
read-only refresh. This is not an exactly-once execution mechanism.

In Cloudflare Observability, inspect invocation type **`alarm`** and application
events `alarm_refresh_completed`, `alarm_refresh_failed`, and
`github_dispatch_succeeded`. Production has `triggers.crons: []`, so empty
**Cron Events are expected**. Stale legacy Cron invocations are ignored. The
public workers.dev domain serves health and authenticated controls; domain
counts are not evidence that an alarm is armed.

| Symptom | Check / next action |
|---|---|
| Health works, no updates | Read authenticated scheduler state; check enabled, next slot, alarm events, then downstream runs |
| Runtime outcome `ok`, stale website | Check application `lastOutcome`/`lastError`; handled failures can have runtime outcome `ok` |
| Dispatch rejected | Check sanitized HTTP status, token validity, Actions write permission, repository/workflow/ref; do not expose the token |
| Dispatch accepted, no new snapshot | Inspect GitHub Actions queue, cancellation/concurrency, secrets, build and Pages deployment steps |
| Account mismatch or required broker read fails | Repair the intended GitHub paper-account configuration; never substitute a different account |
| Graph missing but balances fresh | Inspect history-request availability; do not manufacture history |
| Local preview old, public page fresh | Local JSON is independent of the generated Pages artifact |
| Old data outside window | Expected off-hours behavior; check next eligible weekday slot |

## Deploy, test, and prove recurrence

1. Preserve the existing `GITHUB_TOKEN`, SQLite binding/migration history, and
   stable logical job identity when deploying `wrangler.jsonc`. Replacing the
   object identity can leave a second scheduler behind. SQLite Durable Objects
   are supported on the [Workers Free plan](https://developers.cloudflare.com/durable-objects/platform/pricing/);
   check current quotas before changing scale or billing.
2. Run tests with a verified native ARM64 Node/runtime on this project's Mac:

   ```sh
   npm ci --prefix cloudflare/alpaca-paper-refresh-native
   npm test --prefix cloudflare/alpaca-paper-refresh-native
   ```

   The main [CI workflow](../../.github/workflows/ci.yml) runs the dispatch tests
   and Durable Object tests in the Workers runtime. Keep these and `pages.yml`
   aligned with schedule, binding, or data-contract changes.
3. Read status; enable only if the timer is intended to run and currently
   stopped. Observe a real automatic alarm, not just a manual dispatch.
4. Correlate its application outcome/request ID with a new
   [GitHub Actions run](https://github.com/lipengyuan1994/alpaca-hackathon/actions/workflows/pages.yml).
   Verify that deployment succeeds and the
   [public JSON](https://lipengyuan1994.github.io/alpaca-hackathon/assets/data/live-paper-snapshot.json)
   has advanced `generated_at`.
5. Verify a persisted future alarm and, after initial setup, a normal cadence
   slot. Update this runbook, website copy, browser freshness contract, and CI
   together when the schedule or publication behavior changes.

### Recorded end-to-end evidence

On September 3, 2026, an automatic startup alarm succeeded. A subsequent regular
**4:00 PM ET** alarm fired at `2026-09-03T20:00:00.034Z`, dispatched
[Pages run 33799673542](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33799673542),
and published JSON with `generated_at: 2026-09-03T20:00:12.545286Z`. The next
4:30 PM ET alarm was persisted. This is historical verification, not a claim
that the service is currently healthy. See the
[diagnostic record](../../cloudflare/alpaca-paper-refresh-native/DIAGNOSTICS.md).

Cloudflare's [alarm API](https://developers.cloudflare.com/durable-objects/api/alarms/)
and [design rules](https://developers.cloudflare.com/durable-objects/best-practices/rules-of-durable-objects/)
explain the at-least-once behavior and need to explicitly rearm recurring work.
