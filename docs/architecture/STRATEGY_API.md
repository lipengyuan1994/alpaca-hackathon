# Strategy plug-in API

Status: normative V1 design draft

Audience: strategy researchers, platform, risk, and execution owners

## 1. Purpose

The strategy API lets multiple developers build and test independent strategies without coupling them to Alpaca payloads, database tables, model providers, risk internals, or execution code.

The boundary is deliberately asymmetric:

- A strategy may say **what exposure template it wants and why**.
- Deterministic platform code decides **which contracts, how many, at what limit, whether risk approves, and whether an order is submitted**.

This keeps strategy research scalable while preventing a plug-in from becoming a hidden broker client.

“Plug in any strategy” means any strategy that can express itself through the versioned semantic contract and registered template catalog. It does not mean arbitrary broker logic. New strategy families add normalized features/templates or, for a genuinely breaking need, a reviewed future API version; they do not bypass planning and risk.

## 2. V1 design rules

A V1 plug-in is:

- synchronous;
- stateless except for explicit versioned state passed in/out;
- deterministic for identical canonical inputs;
- pure: no network, filesystem, database, environment-variable, clock, random-global, model, or broker access;
- schema-limited: unknown input/output fields fail validation;
- unable to import adapters, risk, execution, apps, or Alpaca packages;
- unable to select exact option symbols, strikes, expiration dates, quantities, prices, order types, TIFs, client IDs, or broker IDs.

If a strategy needs data not present in the context, it declares a versioned data requirement. It does not fetch the data itself. These properties are enforced at two layers: static dependency/import policy and an isolated `strategy_runner` subprocess using canonical JSON IPC, a cleared environment, no network, a minimal read-only filesystem, no inherited file descriptors, and strict CPU, memory, output-size, and wall-time limits. Import lint alone is not a security boundary.

## 3. Python protocol

The executable implementation should use Pydantic models for the frozen V1 contract family and a runtime-checkable protocol equivalent to:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class StrategyPluginV1(Protocol):
    @property
    def metadata(self) -> StrategyMetadataV1:
        ...

    def data_requirements(
        self,
        config: StrategyConfigV1,
    ) -> DataRequirementsV1:
        ...

    def evaluate(
        self,
        context: StrategyContextV1,
        config: StrategyConfigV1,
    ) -> StrategyEvaluationV1:
        ...
```

`metadata` and `data_requirements` are deterministic descriptions. `evaluate` is the only decision method. Entry, hold, reduce, and close behavior use one discriminated output union so the orchestrator has one stable integration point.

## 4. Input contracts

### `StrategyMetadataV1`

```text
api_version                 = "strategy-plugin/v1"
plugin_id                   stable lowercase slug
plugin_version              semantic version
decision_schema_version     "strategy-evaluation/v1"
owner                       team owner ID
economic_hypothesis_id      preregistered hypothesis reference
deterministic               must be true
```

The runtime compares metadata with the central registry and the installed content hash. A self-declared manifest never enables itself.

### `DataRequirementsV1`

A plug-in declares only normalized needs:

- underlying symbols from the research/deployment allowlist;
- bar timeframes and lookback windows;
- named feature schema/version;
- required option-surface aggregates, such as ATM IV availability, skew bucket, term bucket, indicative spread-quality summary, or eligible-contract count;
- maximum acceptable observation age;
- whether logical strategy-position state is required.

It cannot request arbitrary endpoints, raw account payloads, unrestricted news/web access, OPRA when the runtime is Basic/indicative, or an unregistered feature.

### `StrategyContextV1`

The context is immutable, `extra="forbid"`, canonical-hashed, and contains:

```text
evaluation_id
as_of                         explicit evaluation time; no wall-clock calls
market_snapshot_id/hash
feature_vector_id/hash
feed_identity                 iex + indicative for the free-tier build
quality_flags                 freshness, missingness, entitlement, session state
universe_features             normalized lagged features by allowed underlying
option_surface_summaries      aggregates only; no OCC symbols or exact contracts
logical_positions             strategy-owned IDs and normalized exposure state
allowed_intent_tuples         catalog-projected template/horizon/risk-tier tuples with max TTL
prior_state                   explicit StrategyStateV1
config_hash
context_hash
```

The context must not contain:

- broker/client/MCP objects;
- keys, secrets, account IDs, or owner identity;
- raw Alpaca responses;
- exact option symbols, strikes, expiries, quotes, quantities, or broker order state;
- executable buying power or hidden risk-limit values;
- mutable services, callbacks, or file paths;
- a model client or arbitrary text that can act as instructions.

Logical positions expose only what strategy behavior needs: `strategy_position_id`, underlying, direction/template, normalized entry/mark return, age bucket, lifecycle state, and precomputed risk/exit flags. Broker reconciliation remains outside the plug-in.

### `StrategyConfigV1`

Configuration is schema-validated and frozen. It contains research-frozen signal thresholds, feature names, horizon buckets, entry/exit reason parameters, and no executable risk limits. Its canonical hash is part of every evaluation and promotion record.

### `StrategyStateV1`

Any state is explicit:

```text
state_schema_version
plugin_id/version
as_of
sequence
payload                     plug-in-specific schema-limited state
state_hash
```

The plug-in returns `next_state`; the orchestrator writes the evaluation event and next state and compare-and-swaps the prior sequence/hash in one transaction. The central registry pins the state schema version and schema hash. Hidden class/module state is forbidden. Reprocessing the same evaluation uses the prior committed state and produces the same result; a sequence, schema, or hash mismatch returns a stable refusal.

## 5. Output contract

`StrategyEvaluationV1` wraps one discriminated decision plus provenance:

```text
evaluation_id
plugin_id/version/content_hash
context_hash
config_hash
decision
next_state
evaluation_hash
```

Reason codes, evidence references, signal strength, and expiry live only in the applicable decision variant below. They are not duplicated at envelope level, avoiding two conflicting sources of truth.

`ReasonCodeV1` is a closed enum namespace per decision kind. `ArtifactRefV1` is a typed object containing an allowlisted artifact type, immutable content hash, and optional schema-limited record ID; it is not a free-form string. Plug-in state uses a registry-pinned schema. The resolver reads only the discriminated `decision` fields listed below and never parses reason/evidence/state/prose into executable semantics; changing provenance or state alone cannot change the executable intent tuple.

The decision union is:

```python
StrategyDecisionV1 = (
    NoTradeV1
    | EntryTemplateRequestV1
    | PositionDirectiveV1
)
```

### `NoTradeV1`

Required for invalid data, no edge, cooldown, unsupported regime, or insufficient evidence:

```text
kind = "NO_TRADE"
primary_reason_code
retry_after                    optional explicit time
```

`NO_TRADE` is persisted and shown to judges; it is not treated as an exception.

### `EntryTemplateRequestV1`

Allowed fields:

```text
kind = "ENTRY_TEMPLATE_REQUEST"
underlying                     one registered/allowed symbol
template_id                    preapproved template catalog ID; catalog derives direction
horizon_bucket                 e.g. INTRADAY_15_60M
risk_tier                      e.g. TINY | STANDARD; platform maps to limits
signal_strength_bucket         LOW | MEDIUM | HIGH
intent_expires_at
entry_reason_codes[]          closed EntryReasonCodeV1 enums
evidence_refs[]               typed ArtifactRefV1 objects
```

Structurally forbidden fields include option symbols, strikes, exact expiry, legs, ratio quantities, limit prices, order class/type, TIF, client IDs, account IDs, maximum-loss dollars, or broker instructions.

The catalog owns closed allowed tuples `(template_id, horizon_bucket, risk_tier, max_intent_ttl_seconds)`; the registry projects only enabled tuples into the context. The resolver requires an exact tuple match, derives direction from `template_id`, and requires `as_of < intent_expires_at <= as_of + max_intent_ttl_seconds` (five minutes maximum in competition V1). A mismatch yields `NO_TRADE`; fields are never independently combined.

### `PositionDirectiveV1`

Allowed fields:

```text
kind = "POSITION_DIRECTIVE"
strategy_position_id           logical ID, not broker ID
action                         HOLD | REDUCE | CLOSE
urgency                        NORMAL | RISK_EXIT
reason_codes[]                closed PositionReasonCodeV1 enums
directive_expires_at
```

V1 may map `REDUCE` to `CLOSE` if the exact spread cannot be reduced atomically within invariants. The order planner builds the exact closing plan; the strategy cannot leg out.

## 6. Resolver and AI relationship

The plug-in does not call the LLM. The workflow is:

```text
StrategyEvaluationV1
        + frozen AgentThesisV1
        + fixed resolver policy
        ↓
TradeIntentV1 or NoTradeRecordedV1
```

`AgentThesisV1` must bind `context_hash`, `strategy_evaluation_hash`, `model_input_hash`, model/prompt versions, raw-output hash, and `expires_at`. A stale or mismatched thesis yields `NO_TRADE`. Its only executable recommendation enum is `ALLOW_UNCHANGED | VETO`; free-form rationale and diagnostic confidence never enter template, horizon, direction, risk-tier, selector, planner, or sizing logic.

The resolver rules are monotonic:

- In competition V1, AI may leave the entry unchanged or veto it/choose `NO_TRADE`; it cannot substitute or narrow the strategy-selected template, and confidence cannot alter executable fields.
- AI may not add an underlying/template, reverse direction, extend the horizon, increase risk tier, choose contracts, or override strategy/risk gates.
- If the plug-in and advisory thesis conflict outside a predeclared resolution rule, the result is `NO_TRADE`.
- Replay uses the frozen `AgentThesisV1`; a fresh model call is a separate behavior evaluation.

## 7. Template catalog boundary

The central `template_catalog.yaml` owns contract-selection policy. Example IDs:

- `LONG_CALL_V1`
- `LONG_PUT_V1`
- `CALL_DEBIT_SPREAD_V1`
- `PUT_DEBIT_SPREAD_V1`
- `EQUITY_PROTECTIVE_PUT_V1` — designed but disabled in initial deployment

Each template defines derived direction, allowed horizon/risk-tier/TTL tuples, option side/position intents, DTE buckets, moneyness/delta policy with free-feed fallback, width policy, atomicity requirements, maximum legs, exit construction, and assignment/expiry exclusions.

Alpaca does not currently support an equity leg and option leg in one MLeg order. Therefore `EQUITY_PROTECTIVE_PUT_V1` cannot be enabled until a separately reviewed two-order saga proves hedge-order sequencing, rollback, orphan exposure, and reconciliation. The competition V1 should use atomic option-only structures.

## 8. Standard plug-in package

```text
strategy_plugins/regime_momentum_v1/
├── pyproject.toml                  # only contracts + strategy_sdk dependencies
├── manifest.yaml
├── README.md
├── hypothesis.yaml
├── defaults.yaml
├── src/regime_momentum_v1/
│   ├── __init__.py
│   ├── plugin.py
│   ├── signal.py
│   └── reason_codes.py
├── tests/
│   ├── fixtures/
│   ├── golden/
│   ├── test_contract.py
│   ├── test_determinism.py
│   ├── test_no_trade.py
│   └── test_boundary.py
└── evidence/
    └── promotion.json
```

Example manifest:

```yaml
api_version: strategy-plugin/v1
plugin_id: regime_momentum
plugin_version: 1.0.0
entrypoint: regime_momentum_v1.plugin:Plugin
owner: person_3
reviewer: person_4
allowed_underlyings: [SPY, QQQ, TQQQ, SMH, SOXL, IGV]
allowed_templates: [CALL_DEBIT_SPREAD_V1, PUT_DEBIT_SPREAD_V1]
required_feature_schema: feature-vector/v1
network_access: false
deterministic: true
```

The manifest states capability. The central strategy registry states authority.

### Isolated runner contract

The decision worker never imports a plug-in into its own process. It launches a pinned runner image/process and sends exactly one canonical request envelope containing the registry hash, plug-in content hash, context, config, and prior state. The runner:

- starts with a fixed executable and argument list rather than a shell;
- receives canonical JSON on stdin and returns one schema-limited canonical JSON response on stdout;
- has no network, writable project tree, ambient environment, inherited secret/file descriptor, or shared interpreter state;
- runs with a minimal read-only package filesystem, a fresh temporary working directory, and hard CPU, memory, output-size, and wall-time ceilings;
- emits no arbitrary logs into the decision response; stderr is size-limited and scrubbed as diagnostic evidence;
- is terminated and converted to `PLUGIN_ISOLATION_FAILURE` or `PLUGIN_TIMEOUT` on any boundary violation.

Only repository-owned, registry-pinned code is eligible in V1. The isolation boundary limits mistakes and dependency compromise; it is not permission to load third-party or user-supplied code during the hackathon.

## 9. Central registry and lifecycle

`configs/strategy_registry.yaml` pins:

- plug-in ID, version, entry point, and content hash;
- owner and independent reviewer;
- lifecycle status;
- allowed underlyings/templates/modes;
- frozen config, hypothesis, dataset, report, and promotion-evidence hashes;
- review timestamp and reason;
- deployment flag and rollback version.

Lifecycle:

```text
proposed → research_only → backtested → paper_candidate → paper_enabled → retired
                                      └───────────────→ paper_demo_only
```

Unknown, unregistered, hash-mismatched, insufficiently promoted, or retired plug-ins fail startup or evaluate to a stable registry rejection. No plug-in, LLM, backtest score, or automated job can promote itself.

`paper_demo_only` permits a minimal operational demonstration after all safety gates but makes no alpha claim. It is not an alias for `paper_enabled`.

## 10. Compatibility policy

- `api_version` changes only for breaking plug-in protocol changes.
- Adding optional fields with explicit defaults may be backward compatible; adding required fields or changing semantics is breaking.
- Committed JSON Schema snapshots and golden examples detect drift.
- The registry pins exact plug-in content hash; semantic version alone is insufficient.
- A plug-in upgrade runs old and new versions against the same frozen replay corpus before registry change.
- V1 remains supported through submission freeze. Do not introduce V2 during the hackathon.

## 11. Required plug-in tests

Every plug-in must pass the shared conformance suite:

1. Manifest and metadata agree with the registry candidate.
2. Input and output reject unknown fields.
3. Same canonical context/config produces byte-identical canonical output.
4. Stale, missing, crossed, entitlement-mismatched, or market-closed context returns `NO_TRADE`.
5. No executable decision field can represent an option symbol, strike, exact expiration, quantity, limit, order ID, or account ID; resolver/planner tests prove provenance/state changes cannot become executable inputs.
6. Forbidden imports and dependencies fail CI.
7. The production-equivalent runner proves network denial, cleared environment, minimal read-only filesystem, closed inherited descriptors, and CPU/memory/output/time ceilings with malicious fixture plug-ins.
8. Logical-position exit cases cover `HOLD`, signal invalidation, max age, risk exit, and final competition flatten.
9. Replay over golden fixtures produces stable reason codes and hashes.
10. Property/fuzz tests never emit an underlying or catalog tuple outside the supplied allowlists and reject incompatible template/horizon/risk-tier or excessive-TTL combinations.
11. Missing prior state or state-version mismatch fails closed.
12. Evidence and promotion records refer to the exact plug-in/config/content hashes.
13. Evaluation event, prior-state compare-and-swap, and next-state write are atomic; concurrent use of the same prior state produces one commit and one stable conflict.

The repository includes `always_no_trade_v1` as the reference plug-in. It proves the integration path can represent a safe refusal before a research candidate exists.

## 12. Example evaluation

Conceptual input:

```json
{
  "evaluation_id": "eval_...",
  "as_of": "2026-08-31T14:15:00Z",
  "feed_identity": {"equity": "iex", "options": "indicative"},
  "allowed_intent_tuples": [
    {
      "template_id": "CALL_DEBIT_SPREAD_V1",
      "horizon_bucket": "INTRADAY_15_60M",
      "risk_tier": "TINY",
      "max_intent_ttl_seconds": 300
    }
  ],
  "context_hash": "sha256:..."
}
```

Conceptual plug-in decision:

```json
{
  "kind": "ENTRY_TEMPLATE_REQUEST",
  "underlying": "QQQ",
  "template_id": "CALL_DEBIT_SPREAD_V1",
  "horizon_bucket": "INTRADAY_15_60M",
  "risk_tier": "TINY",
  "signal_strength_bucket": "MEDIUM",
  "entry_reason_codes": ["TREND_VWAP_ALIGNED", "VOLATILITY_ALLOWED"],
  "evidence_refs": [
    {
      "artifact_type": "FEATURE_VECTOR",
      "content_hash": "sha256:...",
      "record_id": "feature_..."
    }
  ],
  "intent_expires_at": "2026-08-31T14:20:00Z"
}
```

No exact order exists yet. The deterministic resolver, template catalog, contract selector, order planner, final risk engine, and execution worker remain separate gates.
