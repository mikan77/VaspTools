"""Numerical post-processing routines."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ElasticFitResult",
    "EOSFitResult",
    "EOSPoint",
    "StrainStressPoint",
    "birch_murnaghan_energy",
    "check_mechanical_stability",
    "fit_elastic_tensor",
    "fit_eos",
    "mechanical_properties",
]


def __getattr__(name: str) -> Any:
    if name in {"EOSFitResult", "EOSPoint", "birch_murnaghan_energy", "fit_eos"}:
        from .eos import EOSFitResult, EOSPoint, birch_murnaghan_energy, fit_eos

        exports = {
            "EOSFitResult": EOSFitResult,
            "EOSPoint": EOSPoint,
            "birch_murnaghan_energy": birch_murnaghan_energy,
            "fit_eos": fit_eos,
        }
    elif name in {
        "ElasticFitResult",
        "StrainStressPoint",
        "check_mechanical_stability",
        "fit_elastic_tensor",
        "mechanical_properties",
    }:
        from .elastic import (
            ElasticFitResult,
            StrainStressPoint,
            check_mechanical_stability,
            fit_elastic_tensor,
            mechanical_properties,
        )

        exports = {
            "ElasticFitResult": ElasticFitResult,
            "StrainStressPoint": StrainStressPoint,
            "check_mechanical_stability": check_mechanical_stability,
            "fit_elastic_tensor": fit_elastic_tensor,
            "mechanical_properties": mechanical_properties,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
