# Research data on T9

- This repository's permanent ETF research library is `/Volumes/T9/TradingResearch`. Require the mounted T9 volume UUID `8D84339B-38CA-350F-A7C6-A3DDE650156E`; never create a replacement `/Volumes/T9` directory on the internal disk.
- Run `etf-cash-research list-data` to discover immutable dataset snapshots and studies. Run `etf-cash-research verify-data` before reusing a snapshot. Name its exact `data_manifest.json` in every backtest or reproduction; do not select a mutable latest path as evidence.
- To extend the archive, use `etf-cash-research update-data --through YYYY-MM-DD`. It publishes a new snapshot. If provider overlap differs or coverage is incomplete, inspect the saved difference report and do not silently replace older data.
- Use the verified native ARM64 `/opt/homebrew/bin/uv` and a `macos-aarch64` Python. Check `platform.machine() == "arm64"` before selecting an environment. Do not use Intel/Rosetta Python.
- The ETF suite is deterministic research. Its Gemini veto has not been performance-tested. Archived results and data do not authorize paper or live trading; leave the v13.5 wheel and broker order paths untouched.
- See `docs/research/ETF_CASH_RESEARCH.md` for exact commands and library layout.
