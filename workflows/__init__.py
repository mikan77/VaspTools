"""Workflow modes."""

from __future__ import annotations

from typing import Any

__all__ = ["ElasticMode", "EOSMode", "ParamScanMode", "WorkflowMode"]


def __getattr__(name: str) -> Any:
    if name == "WorkflowMode":
        from .base import WorkflowMode

        exports = {"WorkflowMode": WorkflowMode}
    elif name == "EOSMode":
        from .eos import EOSMode

        exports = {"EOSMode": EOSMode}
    elif name == "ElasticMode":
        from .elastic import ElasticMode

        exports = {"ElasticMode": ElasticMode}
    elif name == "ParamScanMode":
        from .param_scan import ParamScanMode

        exports = {"ParamScanMode": ParamScanMode}
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
