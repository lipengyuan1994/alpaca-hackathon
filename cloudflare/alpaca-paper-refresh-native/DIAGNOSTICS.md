# Scheduled refresh diagnosis — September 3, 2026

## Resolution: persistent Cloudflare alarm

The original absence of Cron events did not prove a Cloudflare platform fault.
On the user's request for another approach, scheduling was moved to a
SQLite-backed Durable Object within the same Worker. No paid-plan upgrade or
external scheduler was used. The old Cron registration was removed.

- Deployment: `8204751b-9a98-4866-bb91-939bf6e4206c`.
- The authenticated start call armed a timer; it did not dispatch GitHub.
- The timer fired automatically at `2026-09-03T19:54:08.016Z` (3:54:08 PM ET).
- Cloudflare recorded event type `alarm`, outcome `ok`.
- GitHub returned HTTP 204 with request ID `2AD7:1A2ED4:7D6470:195712C:6A99D060`.
- [Pages run 33799120057](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33799120057)
  succeeded. Public JSON was generated at `2026-09-03T19:54:22.759843Z`.
- Persistent status recorded `lastOutcome: dispatched`, `lastError: null`, and
  `alarmAt: 1788465600000` — the next normal slot, 4:00 PM ET.
- With no Worker requests sent between validation and the normal slot, the
  recurring alarm fired at `2026-09-03T20:00:00.034Z` (4:00 PM ET), dispatched
  GitHub request `3302:3EB921:9C886D:1F262FC:6A99D1C0`, and rearmed itself for
  `2026-09-03T20:30:00Z`. Its status remained `dispatched` with no error.
- [Normal-slot Pages run 33799673542](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33799673542)
  was created at 20:00:01 UTC by that alarm and completed successfully.
  Workers Logs recorded the normal event as `alarm` / `ok` and emitted both
  `github_dispatch_succeeded` and `alarm_refresh_completed`.
- Fourteen tests passed, including real Durable Object alarm/rearm behavior,
  duplicate suppression, retry bounds, authenticated controls, weekend and DST
  handling, and preserving a stop during an in-flight GitHub request.
- [Repository CI 33799264993](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33799264993)
  passed for commit `c92a1fd`, including the new Cloudflare runtime tests.

The sections below preserve the earlier Cron investigation as historical
evidence. Production now uses alarms, so an empty Cron Events page is expected.

## Historical findings: Cron approach

- The earlier recorded exceptions were HTTP GET requests reporting
  `Handler does not export a fetch() function.` They were not scheduled events.
- Persistent observability was disabled at the top level. It is now enabled
  with 100% sampling and invocation logs.
- The production secret successfully authenticates to GitHub. An authenticated
  request through the deployed Worker at 18:37:28 UTC returned GitHub HTTP 204,
  request ID `33B6:2357E2:12C6D1:3BD3E7:6A99BE68`.
- That request produced successful Pages run
  [33791615528](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33791615528).
  The public snapshot's `generated_at` was `2026-09-03T18:37:40.912092Z`.
- This manual end-to-end test does **not** establish automatic Cron delivery.
- A second manual request through the final deployment at 19:14:50 UTC also
  returned GitHub HTTP 204, request ID `2E29:267752:6729D7:145C05E:6A99C72A`.
  Pages run [33795300775](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33795300775)
  completed successfully, and both live tail and stored logs recorded the
  Worker request with no exception.
- The 18:30 and 19:00 UTC slots produced neither a scheduled invocation in
  Workers Logs nor a new GitHub workflow-dispatch run during observation.
- GraphQL `workersInvocationsScheduled` also returned no events for the account.
- The production deployment exports both `fetch` and `scheduled`; the secret
  binding exists, and the account is not suspended.

## Controlled scheduler test

At 19:04 UTC the trigger was recreated as `0,30 * * * MON-FRI` (equivalent to
the previous `*/30 * * * MON-FRI`). A temporary `* * * * *` log-only probe was
added to distinguish delivery failure from refresh logic. The probe's handler
returns before contacting GitHub and is tested to make zero fetch calls.

Deployment version: `5e6d0787-e48c-4a2f-a433-1ef233334631`.

Cloudflare's native ARM64 local runtime successfully invoked the same handler:
the probe logged `scheduled_received` and `scheduler_probe`, while a weekend
production event logged `dispatch_skipped`. Seven unit tests and repository CI
[33794455374](https://github.com/lipengyuan1994/alpaca-hackathon/actions/runs/33794455374)
passed.

At 19:20:22 UTC, more than 15 minutes after probe registration, there were still
zero scheduled events in Workers Logs or GraphQL Cron Events. A live tail
connected at 19:11 UTC received the health GET and the successful manual POST,
but no scheduled events through 19:20:21 UTC. This rules out relying solely on
potentially delayed analytics as the explanation for the missing probe.

The temporary probe schedule and live tail were removed after the test. The
remaining production trigger was read back as `0,30 * * * MON-FRI`. No token,
account billing setting, or trading behavior was changed.

The final public snapshot was verified with `generated_at` equal to
`2026-09-03T19:15:04.411079Z`. It came from the manual dispatch, not a cron run.
The latest observed GitHub fallback `schedule` run was at 16:43 UTC, so that
fallback was not assumed to guarantee freshness either.

**Historical unresolved question:** why the original Cron registration did not
produce observable invocations. No infrastructure root cause was established.
This is no longer a dependency of the working alarm-based refresh path above.

Cloudflare documents propagation of trigger changes as taking
[up to 15 minutes](https://developers.cloudflare.com/workers/configuration/cron-triggers/).

## Read-only verification query

Use the Cloudflare GraphQL API with the account ID supplied as a variable:

```graphql
query ScheduledRefreshEvents($accountTag: string!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      workersInvocationsScheduled(
        limit: 100
        filter: {
          scriptName: "alpaca-paper-refresh-native"
          datetime_geq: "2026-09-03T00:00:00Z"
        }
        orderBy: [datetime_DESC]
      ) {
        datetime
        scheduledDatetime
        scriptName
        cron
        status
      }
    }
  }
}
```

Never include secret values or Authorization headers in diagnostics.
