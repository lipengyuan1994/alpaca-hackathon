# Bar-open execution diagnostic — 2026-08-30

This is an exploratory, in-sample execution-model diagnostic. It does not
change `intraday_continuation_v1`, its frozen feature contract, signal, option
template, or its rejected buffered-proxy result.

Inputs are bound to the immutable underlying manifest
`sha256:370853dbecf8c34f3d81c3c90673b23ecc718d8949c8c7b4af30f56d06244c9a`,
option-observation manifest
`sha256:2c9c05eece5b9b33eee66c50776b50c23734fd661fe46c976490cc71a4b707ef`, and
frozen option-request manifest
`sha256:22c0fb0bf044c7f9a40dee73c9bc1a596e99ef2dd1be6aa425a2d7e0206398ec`.

The diagnostic uses the first common option-bar open at entry and exit, instead
of the frozen buffered buy/sell proxy. It is non-executable because it does not
model bid/ask friction. At the fixed $0.10-per-contract-leg-side fee, the
continuation sleeve reports +$260.40 across 94 observed exits; at $0.25 it is
+$204.00. The required severe stress remains −$23,260.60, so this diagnostic
does not establish a robust or promotion-eligible positive result.

Artifact: `research/candidates/group_a_bar_open_execution_diagnostic_20260830/`.
