# Paper performance refresh: Cloudflare → GitHub Actions → GitHub Pages

This runbook describes O's read-only compatibility publication pipeline for the
[paper performance dashboard](https://lipengyuan1994.github.io/alpaca-hackathon/paper-performance.html)
and home-page account card. O's page remains live while SignalQuarry owns the
durable paper feed and GET-only Vultr exporter.

The paper system went live on **Tuesday, September 1, 2026**. It operates with
real market data in an Alpaca **paper** account. Publishing its results is a
separate job: this pipeline does not start the trading loop, submit or cancel
orders, or authorize live-money trading. See the separate
[V13.5 paper execution runbook](PAPER_WHEEL_V13_5.md) for the trading process.

## End-to-end flow

```text
O Cloudflare: persistent SQLite Durable Object alarm
  │ authenticated workflow_dispatch, ref=main, workflow=pages.yml
  ▼
O GitHub Actions: .github/workflows/pages.yml
  │ restricted SSH command: capture-current (or latest for site-only)
  ▼
SignalQuarry Vultr exporter: GET-only Alpaca paper capture + durable history
  │ validated snapshot and O-compatible allowlisted projection
  ▼
publish_shared_paper_feed.py: verify hashes/identity → write O fallback JSON
  │ assemble O HTML + assets and upload deployment artifact
  ▼
O GitHub Pages: keep the existing website URL
  │ browser fetches A's compatibility endpoint on load and every 60 seconds
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
| Publisher | [pages.yml](../../.github/workflows/pages.yml) | Retrieve the durable A feed using O's restricted publication key, stage an O-compatible fallback, build and deploy O Pages |
| O feed adapter | [publish_shared_paper_feed.py](../../scripts/publish_shared_paper_feed.py) | Strict schema and hash validation, paper-only identity check, allowlisted O projection |
| Deployed-page verifier | [verify_shared_paper_page.py](../../scripts/verify_shared_paper_page.py) | Retry Pages propagation and compare deployed JSON/script identities with the staged snapshot |
| Shared feed | [SignalQuarry latest compatibility feed](https://lipengyuan1994.github.io/signalquarry/feeds/compat/alpaca-hackathon/v1/latest.json) | Public sanitized paper balances, supported performance metrics and selected activity |
| Browser | [site.js](../assets/site.js) | Fetch SignalQuarry's compatibility JSON, render metrics/history/fills, label freshness |

## Schedule and freshness

- **Primary:** Cloudflare Durable Object alarms at `:00` and `:30`, Monday–Friday,
  **9:00 AM through 5:00 PM inclusive**, in `America/New_York` (17 regular slots
  per weekday). Daylight saving time follows the IANA timezone. This is a
  weekday monitoring window, **not an exchange-holiday calendar**.
- **Startup:** enabling a stopped timer schedules one automatic validation
  attempt ten seconds later if that time is in-window; otherwise it schedules
  the next regular slot. Starting an already enabled timer is idempotent.
- **GitHub fallback:** O's `pages.yml` weekday schedule remains enabled with
  `America/New_York` specified. It requests a durable capture from the Vultr
  exporter and publishes the O compatibility site; it does not call Alpaca
  directly. It is independent of Cloudflare and can be delayed.
- **Other triggers:** `workflow_dispatch` can request a capture or a site-only
  rebuild. Qualifying pushes to `main` use site-only mode and reuse the latest
  durable snapshot; they do not make a broker capture.
- **Browser:** fetches SignalQuarry's compatibility JSON on load and every 60
  seconds with cache bypass. This does not call Alpaca, trigger Actions, or
  restart an alarm. A and O display the same capture identity when both feeds
  are available.
- **Freshness:** `generated_at` is the broker snapshot generation time in UTC,
  not page-load time or deployment-completion time. A snapshot older than
  **90 minutes** is labeled stale during the publishing window; outside the
  window an expired snapshot is labeled off hours. Age is wall-clock time,
  not an accumulation of active trading minutes.

Thirty minutes is the target dispatch cadence, not a streaming-data guarantee.
GitHub queue/build/deploy time adds latency. O and A each serialize their own
Pages deployments with `cancel-in-progress: false`. Overlapping capture
requests reuse the durable capture-slot result; deployment serialization is
independent in each repository.

### Website-code caching

Both HTML pages reference `site.js` and `site.css` with `?v=` followed by the
first 12 characters of each file's SHA-256. When either asset changes, update
its version in both `docs/index.html` and `docs/paper-performance.html`.
`test_shared_assets_have_content_bound_cache_versions` enforces this in CI.
GitHub Pages can cache assets for ten minutes; polling fresh JSON does not
reload JavaScript in an already-open tab. After a website-code deployment,
reload the page (use a new page query string if the HTML itself is cached).

## Data and credential boundaries

Cloudflare retains its existing repository-scoped `GITHUB_TOKEN` with Actions
write access to O. It dispatches O's `pages.yml` with `ref: main`; **HTTP 204
means accepted, not deployed.** It does not hold Alpaca credentials. A has a
separate Cloudflare workflow, Worker and repository-scoped dispatch credential.

O Actions uses `PUBLIC_FEED_SSH_HOST`, `PUBLIC_FEED_SSH_USER`,
`PUBLIC_FEED_SSH_KEY`, and `PUBLIC_FEED_KNOWN_HOSTS` to invoke only the
Vultr exporter's restricted `capture-current` or `latest` commands. The A-owned
exporter holds the paper broker credential on Vultr and makes GET-only requests
to Alpaca for:

- `/v2/account` — validates the returned account ID against the expected ID;
- `/v2/orders?status=closed&limit=500&direction=desc&nested=true` — selects filled
  system orders with client prefix `rs-v135-`, sorted newest first, limited to ten;
- `/v2/account/portfolio/history?period=1A&timeframe=1D&intraday_reporting=market_hours`
  — daily paper equity/P&L history, with up to 366 sanitized points.

The feed preserves imported legacy observations and appends new captures;
rolling Alpaca history responses cannot erase older observations. The ten
fills are **the most recent system fills, not the ten most profitable trades**.
Unavailable inputs remain labeled unavailable. Net P&L and Modified Dietz daily
returns are shown only when their required cash-flow and equity observations
are available; these are not described as exact time-weighted returns.

The public JSON contains allowlisted broker-reported paper balances, supported
P&L, selected sanitized fills, accumulated history, freshness metadata and a
public deployment alias. Account identifiers, API credentials, broker order
IDs, raw responses and host paths are excluded. The canonical SHA-256 artifact
hash binds the snapshot contents; it is not a signature or proof of
profitability. Paper fills and short-history metrics are not guaranteed future
returns or live-money results.

The O workflow validates the bundle, atomically replaces the fallback, deploys
the site and verifies the actual deployed JSON and browser script against the
staged snapshot. The fallback is not committed on each run and can be older
than the deployed page. A missing feed key, invalid bundle, stale capture,
schema mismatch or deployed identity mismatch fails the workflow and leaves
the previous valid Pages deployment available.

For a local checkout, run `./scripts/install_git_hooks.sh` once. The pre-commit
hook checks staged diffs. The pre-push hook verifies the feed adapter tests and
requires any changed public snapshot to be committed; it does not call Alpaca
or require local broker credentials. The checked-in sanitized browser fallback
supports local file previews when browsers block network fetches.

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
   Verify O's deployed fallback matches the staged compatibility snapshot and
   the O browser fetch points to the same latest
   [SignalQuarry compatibility JSON](https://lipengyuan1994.github.io/signalquarry/feeds/compat/alpaca-hackathon/v1/latest.json).
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
