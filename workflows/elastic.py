"""Elastic-tensor workflow mode."""

from __future__ import annotations

from typing import Iterable

from ..analysis.elastic import ElasticFitResult, StrainStressPoint
from ..analysis.elastic import fit_elastic_tensor, mechanical_properties
from ..analysis.elastic import write_elastic_fit_json, write_mechanical_properties_json
from ..core.models import Calculation
from ..io.results import read_stress
from ..structures import apply_strain, generate_strain_vectors, load_poscar
from .base import WorkflowMode


class ElasticMode(WorkflowMode):
    """Prepare and post-process finite-strain elastic tensor calculations."""

    branch = "elastic"

    def prepare_relaxations(
        self,
        strain_amplitudes: Iterable[float] | None = None,
        *,
        combined_job: bool = True,
    ) -> list[Calculation]:
        """Prepare elastic jobs, optionally combining both stages."""

        structure = load_poscar(self.inputs.poscar)
        amplitudes = tuple(
            self.config.strain_amplitudes if strain_amplitudes is None else strain_amplitudes
        )
        calculations = []
        for label, strain in generate_strain_vectors(amplitudes):
            strained = apply_strain(structure, strain)
            relax_directory = self.root / "relax" / label
            static_directory = self.root / "static" / label
            strain_metadata = [float(value) for value in strain]
            metadata = {
                "branch": self.branch,
                "strain": strain_metadata,
            }
            if combined_job:
                calculation = self.factory.prepare_relax_static(
                    relax_directory=relax_directory,
                    static_directory=static_directory,
                    structure=strained,
                    relax_stage="elastic_relax",
                    static_stage="elastic_static",
                    name=f"elastic_relax_{label}",
                    metadata=metadata,
                    static_metadata=metadata,
                )
            else:
                calculation = self.factory.prepare(
                    directory=relax_directory,
                    structure=strained,
                    stage="elastic_relax",
                    name=f"elastic_relax_{label}",
                    metadata=metadata,
                )
            calculations.append(calculation)
        return calculations

    def prepare_statics(self, *, allow_unrelaxed: bool = False) -> list[Calculation]:
        """Prepare elastic static calculations from strained relaxation CONTCAR files."""

        relax_root = self.root / "relax"
        if not relax_root.exists():
            raise FileNotFoundError(f"No elastic relaxation directory found: {relax_root}")

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
                    stage="elastic_static",
                    name=f"elastic_static_{label}",
                    metadata={
                        "branch": self.branch,
                        "source_relax_dir": str(relax_dir),
                        "strain": metadata["strain"],
                    },
                )
            )
        return calculations

    def collect_points(self) -> list[StrainStressPoint]:
        """Collect strain/stress points from elastic static directories."""

        static_root = self.root / "static"
        if not static_root.exists():
            raise FileNotFoundError(f"No elastic static directory found: {static_root}")

        points = []
        for directory in sorted(path for path in static_root.iterdir() if path.is_dir()):
            metadata = self.read_metadata(directory)
            strain = tuple(float(value) for value in metadata["strain"])
            stress = read_stress(directory, kbar_to_gpa=self.config.vasp_kbar_to_gpa)
            points.append(
                StrainStressPoint(
                    name=directory.name,
                    strain=strain,
                    stress_GPa=tuple(float(value) for value in stress),
                    path=str(directory),
                )
            )
        return points

    def fit_from_statics(self) -> tuple[ElasticFitResult, dict[str, object]]:
        """Fit Cij from static stresses and write elastic reports."""

        points = self.collect_points()
        fit = fit_elastic_tensor(points)
        properties = mechanical_properties(fit.Cij_GPa)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        write_elastic_fit_json(fit, self.report_dir / "elastic_tensor.json")
        write_mechanical_properties_json(
            properties,
            self.report_dir / "mechanical_properties.json",
        )
        return fit, properties
