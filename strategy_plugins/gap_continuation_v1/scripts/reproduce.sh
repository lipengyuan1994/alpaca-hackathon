#!/bin/sh
# Section 9.3 reproduction wrapper: run the module entry point with the
# plan-mandated src/ layout importable, from the repository root.
set -eu

PKG_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$(CDPATH= cd -- "$PKG_DIR/../.." && pwd)

PYTHONPATH="$PKG_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

cd "$REPO_ROOT"
exec uv run python -m gap_continuation_v1.reproduce "$@"
