"""Factory for preparing individual VASP calculation directories."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from ..io.incar import Incar, load_incar, write_incar
from ..io.jobs import sanitize_job_name, write_job_script
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
