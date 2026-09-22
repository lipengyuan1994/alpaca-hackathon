# T08 live service

The selected live service is the deterministic T08 TECL-and-cash runtime. It
can submit orders only for `TECL`; `XLK` is a signal-only proxy. The service is
separate from the QQQ v13.5 paper wheel and from the archived L11 TQQQ/SOXL
configuration. L11 state is never reused for T08.

T08 evaluates once per exchange session using the previous completed XLK close.
It holds a 99% TECL target while XLK is above SMA200 unless downside turbulence
and volatility expansion both exceed their thresholds, in which case the target
is 49.5%. Below SMA200 it exits to cash. Equality retains the actual position.
Missing risk features block increases while a valid trend exit remains allowed.
Target changes compare against reconciled TECL shares; rejected or unfilled
orders never become state.

The repository configuration is `observe` with `account_id: pending`, and the
container defaults to disabled. A build or staged image does not authorize
orders. The later operator sequence is:

```sh
etf-live preflight --config /app/configs/live/t08_tecl.yaml
etf-live reconcile --config /app/configs/live/t08_tecl.yaml
etf-live arm-live --config /app/configs/live/t08_tecl.yaml --reason 'explicit T08 live activation'
```

Before those commands, install the exact account credentials, verify the
account is active, dedicated, flat and has at least $2,000 of verified settled
cash, and keep `/etc/etf-live/enabled` at `0` until explicit activation. The
first live evaluation uses fresh prior-session data; it does not replay an
old signal collected while the account was being opened or funded.

The T08 journal is `/var/lib/alpaca-etf-live/t08_tecl/state.db`. Buys use fresh
SIP quotes and capped DAY limits; reductions use DAY market orders. Unknown
submissions are reconciled by client order ID and are never retried with a new
ID. Telegram is limited to operational incidents. The service does not create
a routine P&L dashboard.

The Vultr image remains `linux/amd64`, published by immutable GHCR digest and
deployed through the protected `vultr-etf-live` environment. The legacy L11
configuration remains available only as an explicitly profiled compatibility
service and cannot run with the default T08 deployment.

## Delivery before credentials

Pull requests that change the live runtime, T08 decision core, live tests,
configuration, container files, or delivery workflows run native ARM64 tests
and a credential-free Linux/amd64 image smoke test. Merges to `main` publish a
commit-tagged GHCR image with provenance and an SBOM. The smoke test starts the
image in its disabled default and only reads its status; it cannot contact a
broker or submit an order.

The `deploy-etf-live` workflow accepts a full immutable SHA-256 digest and
waits for the protected `vultr-etf-live` environment. It stages the image while
`/etc/etf-live/enabled` is `0`. When that file is `1`, deployment runs a
credentialed preflight, waits for the container health check, runs a second
post-start preflight, and restores the prior image if replacement fails.

Before the first disabled staging deployment, configure the protected GitHub
environment with `VULTR_DEPLOY_HOST`, `VULTR_DEPLOY_USER`,
`VULTR_DEPLOY_SSH_KEY`, and `VULTR_SSH_KNOWN_HOSTS`. The deployment key is
restricted to GHCR login/logout and immutable-digest deployment. Store Alpaca
and Telegram values only in read-only files on Vultr under
`/etc/etf-live-secrets`; do not add them to GitHub Actions, the image, or this
repository.

The container restarts after three consecutive run failures. It emits an
explicit failure marker before restart, while the runtime records operational
incidents. A healthy container means the disabled/status probe is working;
preflight remains the broker and account-readiness check.

Every release must show one of these states separately: implementation
validated, staged disabled, account verified, or live enabled. The research
backtest is historical evidence and does not itself authorize activation.
