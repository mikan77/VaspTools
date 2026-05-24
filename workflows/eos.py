"""Equation-of-state workflow mode."""

from __future__ import annotations

from typing import Iterable

from ..analysis.eos import EOSFitResult, EOSPoint, fit_eos, write_eos_fit_json
from ..analysis.eos import write_eos_points_csv
from ..core.models import Calculation
from ..io.results import read_energy, read_volume
from ..structures import load_poscar, scale_structure_to_volume
from .base import WorkflowMode


class EOSMode(WorkflowMode):
    """Prepare and post-process equation-of-state calculations."""

    branch = "eos"

    def prepare_relaxations(
        self,
        volume_factors: Iterable[float] | None = None,
    ) -> list[Calculation]:
        """Prepare fixed-shape, fixed-volume EOS relaxation calculations."""

        structure = load_poscar(self.inputs.poscar)
        factors = tuple(self.config.volume_factors if volume_factors is None else volume_factors)
        calculations = []
        for factor in factors:
            scaled = scale_structure_to_volume(structure, volume_factor=float(factor))
            label = f"V_{factor:.4f}".replace(".", "p")
            calculations.append(
                self.factory.prepare(
                    directory=self.root / "relax" / label,
                    structure=scaled,
                    stage="eos_relax",
                    name=f"eos_relax_{label}",
                    metadata={
                        "branch": self.branch,
                        "volume_factor": float(factor),
                        "target_volume": float(scaled.volume),
                    },
                )
            )
        return calculations

    def prepare_statics(self, *, allow_unrelaxed: bool = False) -> list[Calculation]:
        """Prepare EOS static calculations from EOS relaxation CONTCAR files."""

        relax_root = self.root / "relax"
        if not relax_root.exists():
            raise FileNotFoundError(f"No EOS relaxation directory found: {relax_root}")

        calculations = []
        for relax_dir in sorted(path for path in relax_root.iterdir() if path.is_dir()):
            source = relax_dir / "CONTCAR"
            if not source.exists() or source.stat().st_size == 0:
                if not allow_unrelaxed:
                    raise FileNotFoundError(f"Missing non-empty CONTCAR in {relax_dir}.")
                source = relax_dir / "POSCAR"

            structure = load_poscar(source)
            metadata = self.read_metadata(relax_dir)
            label = relax_dir.name
            calculations.append(
                self.factory.prepare(
                    directory=self.root / "static" / label,
                    structure=structure,
                    stage="eos_static",
                    name=f"eos_static_{label}",
                    metadata={
                        "branch": self.branch,
                        "source_relax_dir": str(relax_dir),
                        "volume_factor": metadata.get("volume_factor"),
                        "target_volume": float(structure.volume),
                    },
                )
            )
        return calculations

    def collect_points(self) -> list[EOSPoint]:
        """Collect E(V) points from EOS static directories."""

        static_root = self.root / "static"
        if not static_root.exists():
            raise FileNotFoundError(f"No EOS static directory found: {static_root}")

        points = []
        for directory in sorted(path for path in static_root.iterdir() if path.is_dir()):
            points.append(
                EOSPoint(
                    volume=read_volume(directory),
                    energy=read_energy(directory),
                    path=str(directory),
                )
            )
        return points

    def fit_from_statics(self) -> EOSFitResult:
        """Collect EOS points, fit Birch-Murnaghan EOS, and write reports."""

        points = self.collect_points()
        result = fit_eos(points)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        write_eos_points_csv(points, self.report_dir / "eos_points.csv")
        write_eos_fit_json(result, self.report_dir / "eos_fit.json")
        return result
