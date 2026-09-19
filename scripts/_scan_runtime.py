"""Backward-compatible re-export; runtime parsing lives in ``VaspTools.io.runtime``."""

from VaspTools.io.runtime import (  # noqa: F401
    OUTCAR_RUNTIME_PATTERNS,
    read_runtime_seconds,
    read_runtime_seconds_from_text,
)

__all__ = ["OUTCAR_RUNTIME_PATTERNS", "read_runtime_seconds", "read_runtime_seconds_from_text"]
