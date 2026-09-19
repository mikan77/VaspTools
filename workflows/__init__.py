"""Workflow modes."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ElasticMode",
    "EOSMode",
    "ParamScanMode",
    "RelaxMode",
    "RelaxProtocol",
    "RelaxStep",
    "WorkflowMode",
    "load_relax_protocol",
]


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
    elif name in {"RelaxMode", "RelaxProtocol", "RelaxStep", "load_relax_protocol"}:
        from .relax import RelaxMode, RelaxProtocol, RelaxStep, load_relax_protocol

        exports = {
            "RelaxMode": RelaxMode,
            "RelaxProtocol": RelaxProtocol,
            "RelaxStep": RelaxStep,
            "load_relax_protocol": load_relax_protocol,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
