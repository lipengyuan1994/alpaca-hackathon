# Documentation index

This directory is the navigation hub for the Alpaca hackathon project. The root plan states **what the team is building and why**; the documents below define **how parallel work integrates**.

## Start here

| Document | Purpose | Primary audience | Authority |
|---|---|---|---|
| [`HACKATHON_PLAN.md`](../HACKATHON_PLAN.md) | Competition requirements, team decisions, product scope, risk posture, six-person ownership, schedule, gates, and submission plan | Entire team | Canonical competition plan |
| [`architecture/SYSTEM_ARCHITECTURE.md`](architecture/SYSTEM_ARCHITECTURE.md) | Process topology, trust boundaries, package dependency rules, event flow, persistence, deployment, and scaling path | Platform, execution, frontend, release | Normative system design |
| [`architecture/STRATEGY_API.md`](architecture/STRATEGY_API.md) | Versioned plug-in contract that lets strategies be developed independently without broker or exact-order access | All strategy researchers and reviewers | Normative strategy boundary |
| [`architecture/RESEARCH_INTERFACE_FREEZE.md`](architecture/RESEARCH_INTERFACE_FREEZE.md) | Pinned research release values, host-interface status, exact conformance commands, and remaining integration/paper-safety boundaries | Researchers, platform, release | Published for credential-free research; not paper authorization |
| [`plans/SKELETON_IMPLEMENTATION_PLAN.md`](plans/SKELETON_IMPLEMENTATION_PLAN.md) | Ordered repository-skeleton and vertical-slice implementation backlog with owners, reviewers, deliverables, and gates | All developers | Delivery sequence |
| [`deployment/COMPOSE_SECRETS.md`](deployment/COMPOSE_SECRETS.md) | File-mounted Compose secret roles, provisioning constraints, and egress boundary | Platform and release owner | Paper deployment runbook |
| [`deployment/LOCAL_POSTGRES.md`](deployment/LOCAL_POSTGRES.md) | Local ARM64 Docker PostgreSQL, migration bootstrap, role isolation, and judge reproduction | Platform and release owner | Local runtime runbook |
| [`deployment/ECONOMIC_CONTEXT.md`](deployment/ECONOMIC_CONTEXT.md) | One daily pre-market Alpaca proxy capture, economics support/veto gate, audit rows, and scheduler contract | Platform, agent, execution, release | Paper runtime runbook |
| [`deployment/judge-reproduce.md`](deployment/judge-reproduce.md) | Credential-free judge replay, optional Gemini advisory setup, and paper-only safety boundaries | Judges, demo owner, release owner | Submission reproduction runbook |
| [`plans/STRATEGY_RESEARCH_PLAN.md`](plans/STRATEGY_RESEARCH_PLAN.md) | Architecture-readiness gates, six delegated strategy families, common free-Alpaca research protocol, plug-in/parity deliverables, validation, metrics, and promotion gates | Six researchers, platform owner, quant lead, risk reviewer | Normative research and integration handoff protocol |
| [`research/README.md`](research/README.md) | Routes the six symbols and six strategy families into three independently shareable, credential-free research packets | Research owners and reviewers | Delegation index; subordinate to the normative research protocol |
| [`research/trading_foundation.md`](research/trading_foundation.md) | Plain-language ETF/options vocabulary, defined-risk expressions, and research safety boundaries | New strategy researchers | Educational reference; subordinate to the research protocol |
| [`research/quant_trading_basic.md`](research/quant_trading_basic.md) | Platform-neutral setup, read-only Alpaca data pattern, no-look-ahead features, proxy simulation, and package handoff | New strategy researchers | Educational reference; subordinate to the research protocol |

## Research group handoffs

| Packet | Owned symbol cells | Assigned families | Independent handoff |
|---|---|---|---|
| Group A — broad tech controls | SPY, QQQ | Normalized intraday continuation; normalized VWAP reversion | [`research/GROUP_A_BROAD_TECH_PLAN.md`](research/GROUP_A_BROAD_TECH_PLAN.md) |
| Group B — semiconductor pair | SMH, SOXL | Opening-range breakout; standardized gap continuation | [`research/GROUP_B_SEMICONDUCTOR_PLAN.md`](research/GROUP_B_SEMICONDUCTOR_PLAN.md) |
| Group C — leveraged/software technology | TQQQ, IGV | Benchmark-residual relative strength; compression breakout | [`research/GROUP_C_LEVERAGED_SOFTWARE_PLAN.md`](research/GROUP_C_LEVERAGED_SOFTWARE_PLAN.md) |

The packets allocate ownership; they do not create isolated two-symbol winner searches. Candidate identity retains its complete compatible symbol set, and central integration applies the common selector and multiple-testing controls across every viewed trial.

## Read by role

- **Quant/product lead:** read the canonical plan, normative architecture/API/research documents, and the published research freeze; own scope, contracts, registry status, risk budget, and release decisions.
- **Data/backend platform:** start with system architecture, then skeleton phases 0–3 and the research data contract.
- **Alpha researchers:** start with the trading foundation and quant basics, then the research routing index and assigned group packet, strategy API, normative research plan, and published freeze. Windows, Linux, and non-ARM macOS are supported for offline research; do not build directly against Alpaca payloads or broker clients.
- **Options/risk quant:** read strategy API output constraints, order-planner boundary, research cost/quote gates, and final risk sequence.
- **Agent/execution engineer:** read the credential boundary, execution state machine, plan-hash authorization sequence, and integration phases.
- **Frontend/submission owner:** read the event/read-model flow, deployment topology, fixture vertical slice, and final reporting labels.

## Decision labels

Documents use five kinds of statements. Do not silently promote one category into another:

- **Official contest requirement:** explicitly present on the event page, event rules/terms, or a dated written organizer clarification.
- **Verified Alpaca platform fact:** capability or constraint established by current Alpaca documentation; this does not create a hackathon rule.
- **Team decision:** chosen by the team for this build, even if the official rule is broader.
- **Safety default:** conservative behavior used until an owner approves a versioned change.
- **Organizer confirmation required:** unresolved contest detail. The current default remains active until written clarification is captured.

## Source-of-truth order

When documents appear to conflict, use this order:

1. A dated written organizer clarification for this event.
2. The current event-specific requirement captured in `HACKATHON_PLAN.md`.
3. The normative system or strategy API document.
4. The implementation or normative research plan.
5. A research group handoff or research interface-status record.
6. A code comment, notebook, chat message, or slide.

Contract schemas generated from code are the executable host-interface source of truth at the pinned release. `RESEARCH_INTERFACE_FREEZE.md` distinguishes closed host baselines from candidate-specific and paper-safety evidence that remains open; publication never self-promotes a candidate. Any change to a V1 contract requires the release captain and at least one consuming owner.

## Documentation ownership

| Area | Owner | Required reviewer | Update trigger |
|---|---|---|---|
| Competition decisions and submission | Release captain | Evidence/submission owner | Rule clarification, scope or schedule change |
| Architecture and event contracts | Platform architect | Risk and execution owners | Interface or deployment change |
| Strategy API and registry | Quant/release owner | Platform and risk reviewers | Plug-in or promotion-policy change |
| Implementation plan | Release captain | Module owner affected by the change | Gate completion or delivery replan |
| Research protocol and evidence | Quant/research lead | Independent non-author research reviewer | Data, metric, hypothesis, or promotion change |

## Documentation rules

- Keep secrets, account keys, personal prize documents, and private chain-of-thought out of the repository.
- Preserve dated rule captures and organizer clarifications as evidence, but do not commit credentials or private account data.
- Every diagram must agree with the credential boundary: only the execution worker may submit, cancel, or reconcile competition orders.
- Every performance number must identify its layer: `backtest`, `shadow`, `broker_reported_paper`, or `conservative_shadow`. Reserve `official_contest_score` for a written organizer formula.
- A four-session competition result is demonstration evidence, not proof of a durable Sharpe ratio.
- Unknown data, stale quotes, missing entitlements, failed reconciliation, or insufficient evidence lead to `NO_TRADE` or a failed gate—not an undocumented workaround.

## Planned follow-on documents

The skeleton plan schedules the following as implementation artifacts; they should be added only when their owning code exists:

- JSON Schema snapshots and event catalog.
- Architecture decision records for the modular monolith, credential boundary, outbox/inbox, plug-in API, and frozen replay.
- Native ARM64 bootstrap runbook.
- Paper arming, reconciliation-unknown, orphan-leg remediation, and final-flatten runbooks.
- Security policy and dependency-boundary matrix.
- Demo and submission runbook.
