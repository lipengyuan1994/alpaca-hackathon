# Security policy

This repository is paper-only. It must never contain a live endpoint, live
credential, or `LIVE` operating mode. Report security concerns privately to the
maintainers; do not commit keys, account IDs, or private incident details.

The public API is read-only. Arming and halting are private, single-use,
hash-bound operator commands. Only the execution-worker deployment may possess
the competition paper credential or access the private Alpaca adapter.
