"""Core pipeline objects."""

from __future__ import annotations

from typing import Any

__all__ = [
    "Calculation",
    "FreeRelaxIncarPolicy",
    "IncarPolicy",
    "MechanicalPipeline",
    "PipelineConfig",
    "PipelineInputs",
    "VaspCalculationFactory",
]


def __getattr__(name: str) -> Any:
    if name in {"Calculation", "PipelineConfig", "PipelineInputs"}:
        from .models import Calculation, PipelineConfig, PipelineInputs

        exports = {
            "Calculation": Calculation,
            "PipelineConfig": PipelineConfig,
            "PipelineInputs": PipelineInputs,
        }
    elif name == "MechanicalPipeline":
        from .pipeline import MechanicalPipeline

        exports = {"MechanicalPipeline": MechanicalPipeline}
    elif name == "VaspCalculationFactory":
        from .factory import VaspCalculationFactory

        exports = {"VaspCalculationFactory": VaspCalculationFactory}
    elif name in {"IncarPolicy", "FreeRelaxIncarPolicy"}:
        from .policies import FreeRelaxIncarPolicy, IncarPolicy

        exports = {"IncarPolicy": IncarPolicy, "FreeRelaxIncarPolicy": FreeRelaxIncarPolicy}
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
