# L11 live service

This service is the isolated deterministic L11 runtime for a dedicated Alpaca
account trading TQQQ and SOXL. It is separate from the QQQ v13.5 paper wheel,
its state, its image, its credentials and its scheduler.

The live service is disabled by default. A build, a GHCR image, or a staged
image never authorizes order submission. Live activation requires a verified
account, settled funding, a passing broker preflight and an explicit operator
activation record.

## Operating states

- **Built/tested:** local and CI tests pass; no broker credentials are needed.
- **Staged disabled:** Vultr has the immutable image, but `/etc/etf-live/enabled`
  is absent or is not exactly `1`; no orders can be submitted.
- **Live enabled:** the exact image, configuration hash, account identity and
  durable activation record have passed preflight.

The current repository configuration is observe-only with `account_id: pending`.
It is intentionally not a live activation configuration.

## Strategy behavior

L11 evaluates allocation targets on the first exchange session of each ISO week:
QQQ and SOXX must be above SMA200 and have positive 126-session returns. SOXL
also requires SOXX to beat QQQ over 63 sessions and the SOXX/QQQ ratio to be
above its 20-session average. The targets are 99% TQQQ, 49.5% TQQQ plus 49.5%
SOXL, 49.5% SOXL, or cash. It checks SMA200 exits each session. Signals use the
previous completed close and actual fills advance state.

Gemini is observation-only. Its output cannot alter targets, quantities, exits,
or timing. No performance dashboard or routine P&L report is produced. Private
order, fill, cash and reconciliation records remain necessary for safe execution.

## Account setup when funding is pending

The implementer can finish tests, image builds and disabled staging before the
account exists. When funding is complete, install file-mounted secrets on Vultr,
replace `account_id: pending` with the exact account ID, verify the account is
dedicated and flat, and verify at least $1,000 settled cash. Do not use a pending
deposit or margin buying power as settled cash.

Refresh daily history and quotes at activation. Do not submit an old signal saved
from the account-opening period. The first authorized activation session consumes
the initial review; after that weekly and daily schedules continue normally.

While the account is pending, leave `/etc/etf-live/enabled` at `0` and keep the
service stopped or staged-disabled. Nothing expires and no trading state is
advanced during this period. After funding, run the broker preflight against the
new private configuration, set the enable file to `1`, run `arm-live` once, and
then start or deploy the exact image. If the account ID or strategy configuration
changes after a preflight has bound the SQLite state, preserve that old state and
use a new state directory; do not edit the journal or reuse it across accounts.

## Operator gates

```sh
etf-live preflight --config /app/configs/live/l11_tqqq_soxl.yaml
etf-live reconcile --config /app/configs/live/l11_tqqq_soxl.yaml
etf-live arm-live --config /app/configs/live/l11_tqqq_soxl.yaml --reason 'explicit L11 live activation'
etf-live pause-buys --config /app/configs/live/l11_tqqq_soxl.yaml --reason 'operator pause'
etf-live resume-buys --config /app/configs/live/l11_tqqq_soxl.yaml --reason 'operator resume'
```

`arm-live` requires the host enable file and a live-mode configuration. The
runtime rejects account mismatches, restricted accounts, multiplier values other
than one, unmanaged positions or orders, unsupported symbols, missing cash and
the placeholder strategy hash.

Buys use capped marketable DAY limit orders after a fresh SIP quote. The service
accepts no stale or wide-spread quote and does not chase a missed buy. Deterministic
exits use regular-session DAY market orders. A timeout after submission is an
unknown order and is reconciled by client order ID; it is never retried with a
new ID.

## Deployment and recovery

The image is built for `linux/amd64` because the current Vultr host reports
`x86_64`. Local Python validation remains native ARM64. The image is published
to GHCR by commit digest and deployed through the protected `vultr-etf-live`
environment. The separate service uses `/etc/etf-live-secrets`,
`/var/lib/alpaca-etf-live/l11_tqqq_soxl`, and `/opt/alpaca-etf-live`.

A failed preflight leaves the service disabled or restores the previous service
only when its state is compatible. Rollback always uses the newest durable state.
Never delete or hand-edit the SQLite state, event journal or activation record.
Pause buys to stop increases while retaining exits. Halt the service to stop all
automated submissions; existing positions remain exposed and require a separate
operator decision.

Telegram receives operational incidents and recovery notices only: rejected or
unknown orders, stale data, account mismatch, reconciliation discrepancies,
service/deployment failures and recovery. It does not receive routine performance
reports.
