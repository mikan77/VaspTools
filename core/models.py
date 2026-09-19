"""Shared data models for VaspTools workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineInputs:
    """Required user-provided input files."""

    poscar: Path
    potcar: Path
    incar: Path
    job_template: Path


POTCAR_MODES = ("copy", "hardlink", "symlink")


@dataclass(frozen=True)
class PipelineConfig:
    """Workflow configuration."""

    workdir: Path
    name: str = "vasp_mechanics"
    volume_factors: tuple[float, ...] = (0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06)
    strain_amplitudes: tuple[float, ...] = (0.005, 0.01)
    job_script_name: str = "job.sh"
    require_kspacing: bool = True
    vasp_kbar_to_gpa: float = -0.1
    potcar_mode: str = "copy"
    """How POTCAR is placed in each calculation directory: ``copy``, ``hardlink``
    (same content, no extra disk space; falls back to copy across filesystems) or
    ``symlink``."""

    def __post_init__(self) -> None:
        if self.potcar_mode not in POTCAR_MODES:
            raise ValueError(f"potcar_mode must be one of {POTCAR_MODES}, got {self.potcar_mode!r}.")


@dataclass(frozen=True)
class Calculation:
    """A prepared calculation directory."""

    name: str
    stage: str
    directory: Path
    job_name: str
    metadata: dict[str, object]
