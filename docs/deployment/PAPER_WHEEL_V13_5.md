# QQQ V13.5 Alpaca paper-wheel runbook

Status: implementation runbook for the bounded 2026-08-31 through 2026-09-04
paper canary

Scope: Alpaca **paper account only**. This path cannot construct a live client,
accept a live hostname, load a live credential, or place a live-money order.

## 1. What is running

The checked-in [`v13_5_qqq.yaml`](../../configs/paper/v13_5_qqq.yaml) is the
complete mutable operator configuration. It selects strategy `v13.5`, ticker
`QQQ`, one contract per symbol, 7–14 DTE, and the frozen trend-asymmetric strike
rules:

- prior 50-session uptrend: 1% OTM put or 3% OTM covered call;
- otherwise: 3% OTM put or 1% OTM covered call;
- buy to close only when profit is strictly greater than 15% of entry credit.

The execution form is a fully cash-secured put or a share-covered call. It does
not open a separate long stock position together with the put. Assignment can
create 100 QQQ shares, after which a later eligible entry can sell one covered
call. There are no naked options, margin-funded puts, multi-contract orders,
market orders, automatic stock liquidation, LLM decisions, or live-account
paths.

The deployment schedule is intentionally an operational variant of the frozen
V13.5 research cadence. Research evaluated the first weekly 10:00 ET slot; this
paper canary permits evaluation throughout regular market hours so a transient
provider or repository-controlled failure does not spend the week's only entry
opportunity. This changes deployment timing evidence and must not be represented
as a replay of the frozen research backtest. Config schema
`paper-wheel-config/v4` makes the breaking schedule and collateral-policy
changes explicit; legacy clock-window, fixed assignment-cap, and unreserved-cash
keys are rejected rather than silently ignored.

The `activation` window authorizes **new entries only**. Reconciliation and a
risk-reducing buy-to-close remain available after the arm expires so a 7–14 DTE
position cannot be stranded when the canary entry window ends.

## 2. Broker and data authority

Only [`AlpacaPaperWheelBroker`](../../packages/paper_wheel/broker.py) owns broker
I/O. Its trading client is always created with `paper=True`; the accepted origin
is the literal `https://paper-api.alpaca.markets`. The runtime uses:

- Alpaca paper Trading API for account, clock, positions, orders, contracts,
  cancellation, calendar, and order submission;
- Alpaca Basic IEX daily bars and latest QQQ quote;
- Alpaca indicative option quotes, not OPRA;
- read-only `OPASN` and `OPEXP` account activities to distinguish assignment
  from expiration before changing the wheel lifecycle.

The strategy layer is deterministic. It consumes broker-normalized dataclasses,
not raw SDK objects. The LLM has no input to this runtime and cannot alter the
symbol, strike rule, contract, quantity, price, collateral, or risk result.

## 3. Secret contract

The default bundle is outside the repository:

```text
/Users/lipengyuan/.config/great_secrets/alpaca/alpaca_api_key.yaml
```

It must be a regular YAML file under `REGIMESWITCH_SECRETS_DIR` and contain the
existing keys `paper_alpaca_api_key`, `paper_alpaca_api_secret`, and
`paper_account_id`. The expected account ID is checked immediately after client
creation and again during runtime preflight. Never copy values into `.env`, the
YAML strategy config, launchd plist, logs, tests, or Git.

## 4. Native ARM64 validation

From the repository root:

```zsh
.venv/bin/python -c 'import platform; assert platform.machine() == "arm64"'
.venv/bin/python -m pytest tests/paper_wheel -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check packages/paper_wheel tests/paper_wheel
zsh -n infra/launchd/run-v13-5-qqq-paper-wheel.zsh infra/launchd/install-v13-5-qqq-paper-wheel.zsh
plutil -lint infra/launchd/com.regimeswitch.v13-5-qqq-paper-wheel.plist.template
```

No Rosetta or x86 Python is permitted. The scheduler wrapper independently
checks `platform.machine() == "arm64"` before every run.

## 5. Read-only preflight and arming

Preflight reads the paper account but submits and cancels nothing:

```zsh
.venv/bin/python -m packages.paper_wheel.cli preflight \
  --config configs/paper/v13_5_qqq.yaml
```

`PREFLIGHT_READY` requires the exact account, current broker clock, option level
1 or higher, no account block, acceptable daily drawdown, no foreign position or
open order, and exact agreement between persisted wheel state and account
positions. Do not weaken the YAML or delete state to bypass a refusal.

After a green preflight, create the short-lived, config-hash/account-hash-bound
arm token:

```zsh
.venv/bin/python -m packages.paper_wheel.cli arm \
  --config configs/paper/v13_5_qqq.yaml \
  --reason "Authorized QQQ V13.5 market-hours Alpaca paper canary through 2026-09-04"
```

Any semantic YAML change changes `config_hash` and invalidates the arm. Normally,
an open position must complete its lifecycle before moving to a new configuration
or runtime root.

For an explicitly authorized in-place paper-policy change while a managed
position is open, use the audited migration command instead of editing runtime
state or deleting the arm. It requires the exact previous config hash, a
reconciled position, no open order, the same paper account and activation
window, and records start/completion events in the hash-chained journal:

```zsh
.venv/bin/python -m packages.paper_wheel.cli migrate-config \
  --config configs/paper/v13_5_qqq.yaml \
  --expected-current-config-hash sha256:<exact-previous-hash> \
  --reason "Operator authorized paper-policy migration"
```

The command never submits, cancels, or replaces an order. A partially completed
migration is recoverable only when its journal start event and old/new hashes
match exactly; every other mismatch fails closed.

Verify that the future schedule window, current config hash, and authenticated
paper account all match the token:

```zsh
.venv/bin/python -m packages.paper_wheel.cli verify-arm \
  --config configs/paper/v13_5_qqq.yaml
```

## 6. Automatic schedule

Install only after preflight and arm succeed:

```zsh
infra/launchd/install-v13-5-qqq-paper-wheel.zsh
```

The installer reruns both read-only preflight and `verify-arm` before writing or
loading a launchd plist. Any refusal leaves the service uninstalled.

The launchd job wakes every 60 seconds. Its wrapper exits before importing the
runtime outside weekdays 08:30–16:30 America/New_York, so it does not poll Alpaca
overnight or on weekends. While the paper market is open, a flat, armed runtime
may evaluate a new entry on every poll until the strict 15:15 ET cutoff. There
is no 10:00–10:05 window and no first-session-of-week restriction. An owned
working order or managed option prevents another entry; lifecycle management
and reconciliation continue during the wider wrapper window.

Verify the installed service:

```zsh
launchctl print "gui/$(id -u)/com.regimeswitch.v13-5-qqq-paper-wheel"
.venv/bin/python -m packages.paper_wheel.cli status \
  --config configs/paper/v13_5_qqq.yaml
```

## 7. Safety and recovery

Every plan has a canonical hash and deterministic client order ID. A fsynced,
hash-chained journal records preparation before submission. Provider/data
failures before submission are safe to evaluate again on the next scheduler
poll. Unknown submission never retries blindly; it reconciles by client order
ID. A nonterminal limit order is
canceled after 180 seconds and must become terminal within another 120 seconds.
Even after cancellation, the same client order ID is never reused.

The runtime holds a nonblocking process lease, allows one account order at a
time, rejects stale/crossed/wide quotes, enforces cash and share collateral,
blocks new entries after a 2% daily drawdown, and keeps buy-to-close available.
There is no independent fixed assignment-notional ceiling or unreserved-cash
requirement. A cash-secured put is eligible only when paper cash and options
buying power each cover 100 shares at the strike. Whole-contract sizing, full
cash collateral, and the one-contract-per-symbol limit remain mandatory.
Missing assignment/expiration activity waits up to 30 minutes and then halts
instead of guessing.

Emergency stop:

```zsh
.venv/bin/python -m packages.paper_wheel.cli halt \
  --config configs/paper/v13_5_qqq.yaml \
  --reason "Operator requested emergency paper halt"
```

Halt prevents every later runtime submission and requests cancellation of each
owned working order. It does **not** liquidate QQQ shares or buy back an already
open short option automatically. Inspect Alpaca paper positions/orders and the
local journal before any recovery. There is no automatic re-arm path.

Do not delete or hand-edit the control files. Runtime evidence is stored with
mode `0600` under
`artifacts/paper_wheel/v13_5_qqq_market_hours_cash_secured/`:

```text
state.json
control/arm.json
journal.jsonl
launchd.stdout.log
launchd.stderr.log
runtime.lock
```

`state.json`, `arm.json`, and every journal event are self-hashed; journal events
also bind the preceding event hash. Invalid state, a broken journal chain,
unknown broker status, cancellation uncertainty, assignment drift, or config
hash drift fails closed.

## 8. Known limitations

- This is a single-account, one-contract QQQ canary, not a general portfolio
  allocator or a promotion decision for V13.5.
- Indicative options quotes are not OPRA and may be less executable than the
  historical research observations; limit orders and spread/age gates remain
  mandatory.
- The local filesystem journal is durable for one Mac but is not the final
  Postgres outbox/inbox control plane described by the system architecture.
- The runtime does not exercise options, send DNE instructions, trade shares,
  roll contracts, or automatically flatten assigned shares.
- launchd is macOS-specific. The deterministic config, contracts, risk logic,
  and broker adapter remain portable; another scheduler must preserve the same
  single-process lease and one-shot command.
- The market-hours deployment policy has not been backtested as the frozen
  first-weekly-slot policy. Its paper results are operational evidence only.

The evidence generated by this canary is `broker_reported_paper`, not a
backtest, official contest score, or authorization for live-money trading.
