"""Input/output helpers for VASP workflows."""

from __future__ import annotations

from typing import Any

__all__ = [
    "Incar",
    "DEFAULT_JOB_TEMPLATE_NAMES",
    "Submission",
    "discover_calculations",
    "make_stage_incar",
    "parse_sbatch_job_id",
    "read_energy",
    "read_stress",
    "read_volume",
    "render_job_script",
    "render_two_stage_job_script",
    "resolve_job_template_path",
    "submit_sbatch",
    "validate_kspacing_incar",
]


def __getattr__(name: str) -> Any:
    if name in {"Incar", "make_stage_incar", "validate_kspacing_incar"}:
        from .incar import Incar, make_stage_incar, validate_kspacing_incar

        exports = {
            "Incar": Incar,
            "make_stage_incar": make_stage_incar,
            "validate_kspacing_incar": validate_kspacing_incar,
        }
    elif name in {
        "Submission",
        "parse_sbatch_job_id",
        "render_job_script",
        "render_two_stage_job_script",
        "submit_sbatch",
    }:
        from .jobs import (
            Submission,
            parse_sbatch_job_id,
            render_job_script,
            render_two_stage_job_script,
            submit_sbatch,
        )

        exports = {
            "Submission": Submission,
            "parse_sbatch_job_id": parse_sbatch_job_id,
            "render_job_script": render_job_script,
            "render_two_stage_job_script": render_two_stage_job_script,
            "submit_sbatch": submit_sbatch,
        }
    elif name in {"DEFAULT_JOB_TEMPLATE_NAMES", "resolve_job_template_path"}:
        from .jobs import DEFAULT_JOB_TEMPLATE_NAMES, resolve_job_template_path

        exports = {
            "DEFAULT_JOB_TEMPLATE_NAMES": DEFAULT_JOB_TEMPLATE_NAMES,
            "resolve_job_template_path": resolve_job_template_path,
        }
    elif name in {"read_energy", "read_stress", "read_volume"}:
        from .results import read_energy, read_stress, read_volume

        exports = {
            "read_energy": read_energy,
            "read_stress": read_stress,
            "read_volume": read_volume,
        }
    elif name == "discover_calculations":
        from .discovery import discover_calculations

        exports = {"discover_calculations": discover_calculations}
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = exports[name]
    globals()[name] = value
    return value
