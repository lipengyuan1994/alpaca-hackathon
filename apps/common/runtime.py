"""Host-runtime invariants shared by local application entry points."""

from __future__ import annotations

import platform
import sys


def assert_native_developer_runtime() -> None:
    """Reject Intel/Rosetta Python on macOS while allowing Linux containers.

    Apple Silicon is a local-development invariant. Linux container architecture
    is selected and verified by each Docker build, so it must not be rejected by
    a Darwin-specific runtime check.
    """

    if sys.platform == "darwin" and platform.machine() != "arm64":
        raise RuntimeError("LOCAL_RUNTIME_MUST_BE_ARM64")
