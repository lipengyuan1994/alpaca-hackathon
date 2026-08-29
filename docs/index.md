# Documentation index

This directory is the navigation hub for the Alpaca hackathon project. The root plan states **what the team is building and why**; the documents below define **how parallel work integrates**.

## Start here

| Document | Purpose | Primary audience | Authority |
|---|---|---|---|
| [`HACKATHON_PLAN.md`](../HACKATHON_PLAN.md) | Competition requirements, team decisions, product scope, risk posture, six-person ownership, schedule, gates, and submission plan | Entire team | Canonical competition plan |
| [`architecture/SYSTEM_ARCHITECTURE.md`](architecture/SYSTEM_ARCHITECTURE.md) | Process topology, trust boundaries, package dependency rules, event flow, persistence, deployment, and scaling path | Platform, execution, frontend, release | Normative system design |
| [`architecture/STRATEGY_API.md`](architecture/STRATEGY_API.md) | Versioned plug-in contract that lets strategies be developed independently without broker or exact-order access | All strategy researchers and reviewers | Normative strategy boundary |
| [`plans/SKELETON_IMPLEMENTATION_PLAN.md`](plans/SKELETON_IMPLEMENTATION_PLAN.md) | Ordered repository-skeleton and vertical-slice implementation backlog with owners, reviewers, deliverables, and gates | All developers | Delivery sequence |
| [`plans/STRATEGY_RESEARCH_PLAN.md`](plans/STRATEGY_RESEARCH_PLAN.md) | Common free-Alpaca data protocol, six-symbol delegation, hypotheses, validation, metrics, artifacts, and promotion gates | Six researchers, quant lead, risk reviewer | Normative research protocol |

## Read by role

- **Quant/product lead:** read all five documents; own scope, contracts, registry status, risk budget, and release decisions.
- **Data/backend platform:** start with system architecture, then skeleton phases 0–3 and the research data contract.
- **Alpha researchers:** start with the strategy API and research plan; do not build directly against Alpaca payloads or broker clients.
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
4. The implementation or research plan.
5. A code comment, notebook, chat message, or slide.

Contract schemas generated from code become the executable source of truth only after the interface-freeze gate. Until then, these documents and committed golden fixtures must agree. Any change to a V1 contract requires the release captain and at least one consuming owner.

## Documentation ownership

| Area | Owner | Required reviewer | Update trigger |
|---|---|---|---|
| Competition decisions and submission | Person 1 | Person 6 | Rule clarification, scope or schedule change |
| Architecture and event contracts | Person 2 | Persons 4 and 5 for risk/execution boundaries | Interface or deployment change |
| Strategy API and registry | Person 1 | Persons 3 and 4 | Plug-in or promotion-policy change |
| Implementation plan | Person 1 | Module owner affected by the change | Gate completion or delivery replan |
| Research protocol and evidence | Person 3 | Person 4 or another non-author quant | Data, metric, hypothesis, or promotion change |

Replace Person 1–6 with names once responsibilities are assigned.

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
