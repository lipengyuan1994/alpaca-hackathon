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

Run the dependency-free tests with native Node:

```sh
node --test cloudflare/alpaca-paper-refresh-native/index.test.js
```
