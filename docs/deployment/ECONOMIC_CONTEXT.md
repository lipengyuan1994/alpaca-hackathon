# Daily economic-context gate

Status: paper-only runtime. This gate is advisory and fail-closed; it does not
create a live-trading path.

## Runtime behavior

`economic-context` is a one-shot Compose role. During the configured
08:45–09:25 America/New_York collection window it claims the date in the same
private PostgreSQL database used by the outbox, then makes one bounded Alpaca
Market Data capture:

- daily IEX bars for the configured liquid market proxies (`SPY`, `QQQ`,
  `IWM`, and `TLT` by default);
- the registered strategy-underlying context (`SPY` and `QQQ` by default); and
- Alpaca News headline metadata only, never article bodies.

The capture is stored as `DailyEconomicContextV1` in
`daily_economic_context_v1`. A second invocation for the same date returns the
stored context without contacting Alpaca. A cache miss after the morning
window, a failed capture, unavailable credentials, or an in-progress claim is
a terminal no-context result; no intraday refresh or same-day retry occurs.

Schedule the one-shot role externally on intended regular-session days at
08:45 ET, after the PostgreSQL service is healthy:

```zsh
docker compose -f infra/compose.yaml run --rm economic-context
```

Compose is not a calendar scheduler. Configure the deployment scheduler with
the `America/New_York` timezone and the exchange-session calendar. The worker
also enforces the time window itself, so an accidental late invocation cannot
retrieve new intraday data.

## What the LLM may decide

For each generated semantic `TradeIntentV1`, the decision worker reads only the
stored context for that trading date and sends the frozen context plus a
sanitized semantic signal to the internal agent service. The resulting
`EconomicAssessmentV1` can only return `ALLOW_UNCHANGED` or `VETO`:

```text
strategy signal → first advisory gate → semantic intent
                → frozen daily economic context + economic assessment
                → original intent unchanged | NO_TRADE
                → option plan → risk → outbox
```

The economic gate runs before exact option selection, quantity, price, risk
reservation, and outbox publication. It verifies all content hashes, the
trading date, and both expiries. An unavailable agent, invalid output, missing
context, or `VETO` writes a `NO_TRADE` audit record and never enqueues an
option order.

The current Alpaca capture is explicitly **market/news proxy context**, not
official macroeconomic releases. Alpaca's [documented historical-data
surfaces](https://docs.alpaca.markets/us/docs/historical-api) cover market data
and news; this implementation does not label ETF moves or headlines as CPI,
employment, GDP, or central-bank data. Add a separately reviewed official macro
source only if that broader data scope is approved.

## Durable audit rows

`signal_decision_audit_v1` contains one row for every generated signal,
including strategy refusals, advisory vetoes, economic vetoes, risk rejections,
and approved/enqueued plans. It retains hashes for the signal, both advisory
artifacts, the economic context, plan/client order ID when present, a
supplemental JSON payload, and a full canonical payload.

`order_placed` stays `false` for no-trade, rejected, enqueued, and unknown
states. It changes to `true` only after a broker event establishes an accepted,
partial, filled, cancelled, or expired order state. The execution worker owns
that update; its first terminal broker observation is retained against stale
later events.

For an operational read-only review, query the integrated database with:

```sql
SELECT
    trading_date,
    recorded_at,
    decision_status,
    placement_state,
    order_placed,
    reason_code,
    strategy_evaluation_hash,
    economic_context_hash,
    economic_assessment_hash,
    client_order_id,
    supplemental
FROM signal_decision_audit_v1
ORDER BY recorded_at DESC, record_id;
```

The decision, economic-context, and execution roles use the same private
database DSN in the local Compose design. This does not grant the collector or
decision worker a broker credential or an order-submission code path. See the
[Compose secret runbook](COMPOSE_SECRETS.md) and
[local PostgreSQL runbook](LOCAL_POSTGRES.md) for deployment details.
