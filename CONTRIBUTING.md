# Contributing

Use the verified native Apple Silicon interpreter and `/opt/homebrew/bin/uv`.
Run `uv run python -m pytest` before proposing a change. Contract changes need
schema snapshots and at least one consuming-owner review. Do not add live
trading behavior, secrets, direct broker access from decision/strategy code, or
public mutation routes.
