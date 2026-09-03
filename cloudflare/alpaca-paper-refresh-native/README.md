# Alpaca paper refresh Worker

This Cloudflare Worker dispatches `.github/workflows/pages.yml` every 30 minutes
on weekdays. The Worker applies an additional `America/New_York` gate so only
events from 9:00 AM through 5:00 PM ET reach GitHub.

The `GITHUB_TOKEN` secret must be a fine-grained GitHub token with Actions write
access to `lipengyuan1994/alpaca-hackathon`. It is configured in Cloudflare and
must never be committed to this directory.

- `GET /` or `GET /healthz` returns non-sensitive deployment health.
- `POST /dispatch` performs an immediate, in-window dispatch and requires the
  exact `GITHUB_TOKEN` as a Bearer token.
- The scheduled handler awaits the GitHub request so a failed dispatch is
  recorded as a failed Cron invocation.
- Every scheduled invocation logs `scheduled_received` before checking the
  refresh window or contacting GitHub. A successful dispatch logs
  `github_dispatch_succeeded` with the GitHub request ID.

## Diagnosing scheduled delivery

Check both Workers Logs and Settings > Trigger Events > View events. HTTP health
requests and authenticated manual dispatches are not proof of Cron delivery.
For API diagnostics, the GraphQL dataset `workersInvocationsScheduled` exposes
`datetime`, `scheduledDatetime`, `scriptName`, `cron`, and `status`.

If delivery itself needs isolation, temporarily adding the exact expression
`* * * * *` enables a log-only probe. This expression is deliberately intercepted
before any GitHub call. It logs `scheduler_probe`, does not require a token, and
must be removed after diagnosis. It is not part of the committed production
configuration. Production uses only `0,30 * * * MON-FRI` with the Eastern window
above. Allow up to 15 minutes for schedule changes to propagate before judging
delivery; do not repeatedly rewrite the schedule during that window.

Run the dependency-free tests with native Node:

```sh
node --test cloudflare/alpaca-paper-refresh-native/index.test.js
```
