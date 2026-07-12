"""Factory for preparing individual VASP calculation directories."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from ..io.incar import Incar, load_incar, write_incar
from ..io.jobs import (
    render_job_script,
    render_two_stage_job_script,
    sanitize_job_name,
    write_job_script,
)
from ..structures import write_poscar
from .models import Calculation, PipelineConfig, PipelineInputs
from .policies import IncarPolicy


OUTPUT_ARTIFACTS = (
    "OUTCAR",
    "OSZICAR",
    "CONTCAR",
    "vasprun.xml",
    "XDATCAR",
    "EIGENVAL",
    "CHGCAR",
    "WAVECAR",
)


class VaspCalculationFactory:
    """Create VASP calculation directories from shared inputs and policies."""

    def __init__(
        self,
        *,
        inputs: PipelineInputs,
        config: PipelineConfig,
        incar_policy: IncarPolicy | None = None,
        template_incar: Incar | None = None,
    ):
        self.inputs = inputs
        self.config = config
        self.incar_policy = incar_policy or IncarPolicy(
            require_kspacing=config.require_kspacing
        )
        self.template_incar = template_incar or load_incar(inputs.incar)
        self.incar_policy.validate_template(self.template_incar)

    def write_metadata(self, directory: str | Path, metadata: dict[str, object]) -> None:
        """Write calculation metadata."""

        path = Path(directory) / "vasptools_metadata.json"
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def read_metadata(self, directory: str | Path) -> dict[str, object]:
        """Read calculation metadata."""

        path = Path(directory) / "vasptools_metadata.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def prepare(
        self,
        *,
        directory: str | Path,
        structure,
        stage: str,
        name: str,
        metadata: dict[str, object] | None = None,
        incar_overrides: dict[str, object] | None = None,
    ) -> Calculation:
        """Prepare one calculation directory.

        The factory writes POSCAR/POTCAR/INCAR/job script and metadata. It never
        creates KPOINTS and stops if one already exists in the target directory.
        """

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / "KPOINTS").exists():
            raise FileExistsError(
                f"{directory / 'KPOINTS'} exists. Remove it manually; this workflow uses KSPACING."
            )
        stale_outputs = [name for name in OUTPUT_ARTIFACTS if (directory / name).exists()]
        if stale_outputs:
            joined = ", ".join(stale_outputs)
            raise FileExistsError(
                f"{directory} contains old VASP output files ({joined}). "
                "Move or remove them before preparing a new calculation there."
            )

        job_name = sanitize_job_name(f"{self.config.name}_{name}")
        write_poscar(structure, directory / "POSCAR")
        shutil.copy2(self.inputs.potcar, directory / "POTCAR")

        incar = self.incar_policy.make_incar(
            self.template_incar,
            system=job_name,
            stage=stage,
            extra_overrides=incar_overrides,
        )
        write_incar(incar, directory / "INCAR")
        write_job_script(
            self.inputs.job_template,
            directory / self.config.job_script_name,
            job_name=job_name,
        )

        full_metadata = {
            "name": name,
            "stage": stage,
            "job_name": job_name,
            "directory": str(directory),
        }
        if metadata:
            full_metadata.update(metadata)
        self.write_metadata(directory, full_metadata)

        return Calculation(
            name=name,
            stage=stage,
            directory=directory,
            job_name=job_name,
            metadata=full_metadata,
        )

    def prepare_relax_static(
        self,
        *,
        relax_directory: str | Path,
        static_directory: str | Path,
        structure,
        relax_stage: str,
        static_stage: str,
        name: str,
        metadata: dict[str, object] | None = None,
        static_metadata: dict[str, object] | None = None,
        relax_incar_overrides: dict[str, object] | None = None,
        static_incar_overrides: dict[str, object] | None = None,
    ) -> Calculation:
        """Prepare relaxation and static inputs for one SLURM job.

        The returned calculation points to the relaxation directory. Its
        ``job.sh`` is a driver that runs the rendered relaxation template,
        copies ``CONTCAR`` to the static directory, and runs the static
        template in the same SLURM allocation.
        """

        relax_directory = Path(relax_directory)
        static_directory = Path(static_directory)
        relax_metadata = {
            "combined_job": True,
            "static_directory": str(static_directory),
            "static_stage": static_stage,
        }
        if metadata:
            relax_metadata.update(metadata)

        relax_calculation = self.prepare(
            directory=relax_directory,
            structure=structure,
            stage=relax_stage,
            name=name,
            metadata=relax_metadata,
            incar_overrides=relax_incar_overrides,
        )

        static_full_metadata = {
            "combined_job": True,
            "source_relax_dir": str(relax_directory),
            "parent_job_name": relax_calculation.job_name,
        }
        if static_metadata:
            static_full_metadata.update(static_metadata)

        self.prepare(
            directory=static_directory,
            structure=structure,
            stage=static_stage,
            name=f"{name}_static",
            metadata=static_full_metadata,
            incar_overrides=static_incar_overrides,
        )

        template = self.inputs.job_template.read_text(encoding="utf-8")
        rendered_stage = render_job_script(template, relax_calculation.job_name)
        relax_stage_script = relax_directory / ".vasptools_relax_stage.sh"
        relax_stage_script.write_text(rendered_stage, encoding="utf-8")

        relative_static_directory = os.path.relpath(static_directory, relax_directory)
        driver = render_two_stage_job_script(
            template,
            relax_calculation.job_name,
            static_directory=relative_static_directory,
            static_stage_script=self.config.job_script_name,
        )
        (relax_directory / self.config.job_script_name).write_text(
            driver,
            encoding="utf-8",
        )
        return relax_calculation
