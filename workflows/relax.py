"""Free relaxation workflow mode.

`RelaxMode` prepares a chain of one or more relaxations of the reference
structure. Every step gets its own directory; a single SLURM driver in the
first step runs them sequentially and feeds ``CONTCAR`` of one step into
``POSCAR`` of the next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from ..core.models import Calculation
from ..io.incar import load_incar
from ..io.jobs import render_chain_job_script, render_job_script
from ..io.results import read_converged, read_energy, read_finished, read_initial_energy
from ..io.runtime import read_runtime_seconds
from ..structures import load_poscar
from .base import WorkflowMode


STAGE_SCRIPT_NAME = ".vasptools_stage.sh"


@dataclass(frozen=True)
class RelaxStep:
    """One relaxation step: a label and INCAR tags applied on top of the template."""

    name: str
    incar_overrides: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RelaxProtocol:
    """Ordered relaxation steps plus INCAR overrides shared by every step."""

    steps: tuple[RelaxStep, ...]
    common_incar: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("A relaxation protocol needs at least one step.")

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    def step_overrides(self) -> list[dict[str, object]]:
        """Per-step overrides with the common tags merged in (step wins)."""

        return [{**self.common_incar, **step.incar_overrides} for step in self.steps]

    def to_dict(self) -> dict[str, object]:
        """Flat representation; the same layout as the YAML protocol file."""

        return {
            **dict(self.common_incar),
            "steps": [{"name": step.name, **step.incar_overrides} for step in self.steps],
        }


STEP_META_KEYS = ("name", "incar_file")
PROTOCOL_META_KEYS = ("steps",)


def _collect_incar_tags(
    mapping: Mapping[str, object],
    *,
    reserved: Sequence[str],
    where: str,
) -> dict[str, object]:
    """Treat every non-reserved key of ``mapping`` as an INCAR tag."""

    tags: dict[str, object] = {}
    for key, value in mapping.items():
        if key in reserved:
            continue
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{where}: invalid INCAR tag name {key!r}.")
        if isinstance(value, Mapping):
            raise ValueError(
                f"{where}: tag {key!r} has a nested mapping; list INCAR tags directly "
                f"(e.g. 'ISIF: 3'), nesting is not supported."
            )
        if isinstance(value, (list, tuple)) and any(isinstance(item, (Mapping, list, tuple)) for item in value):
            raise ValueError(f"{where}: tag {key!r} must be a scalar or a flat list of scalars.")
        tags[key.strip().upper()] = value
    return tags


def load_relax_protocol(
    path: str | Path,
    *,
    template_dir: str | Path | None = None,
) -> RelaxProtocol:
    """Load a YAML relaxation protocol.

    Minimal layout: a ``steps`` list whose items are mappings of INCAR tags::

        steps:
          - ISIF: 2
            NSW: 60
          - ISIF: 3

    Optional keys: any top-level key except ``steps`` is a common INCAR tag;
    inside a step ``name`` labels it (default ``step1``, ``step2``, ...) and
    ``incar_file`` names a file read as an INCAR (relative to ``template_dir``,
    the step's own tags win over it).

    Tags not mentioned for a step are inherited from the template INCAR.
    ``template_dir`` defaults to the directory of the YAML file.
    """

    import yaml

    path = Path(path)
    base_dir = Path(template_dir) if template_dir is not None else path.parent
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: protocol root must be a mapping with a 'steps' list.")

    common = _collect_incar_tags(data, reserved=PROTOCOL_META_KEYS, where=str(path))
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, str) or not raw_steps:
        raise ValueError(f"{path}: 'steps' must be a non-empty list.")

    steps: list[RelaxStep] = []
    for index, raw in enumerate(raw_steps, start=1):
        where = f"{path}: step {index}"
        if not isinstance(raw, Mapping):
            raise ValueError(f"{where}: each step must be a mapping of INCAR tags.")

        name = raw.get("name", f"step{index}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: 'name' must be a non-empty string.")

        overrides: dict[str, object] = {}
        incar_file = raw.get("incar_file")
        if incar_file is not None:
            if not isinstance(incar_file, str) or not incar_file.strip():
                raise ValueError(f"{where}: 'incar_file' must be a file name.")
            incar_path = Path(incar_file)
            if not incar_path.is_absolute():
                incar_path = base_dir / incar_path
            if not incar_path.is_file():
                raise FileNotFoundError(f"{where}: incar_file not found: {incar_path}")
            overrides.update(load_incar(incar_path))
        overrides.update(_collect_incar_tags(raw, reserved=STEP_META_KEYS, where=where))
        steps.append(RelaxStep(name=name.strip(), incar_overrides=overrides))

    return RelaxProtocol(steps=tuple(steps), common_incar=common)


class RelaxMode(WorkflowMode):
    """Prepare and collect chained relaxations of one structure."""

    branch = "relax"
    stage = "free_relax"

    def step_directory(self, step: int) -> Path:
        """Directory of relaxation step ``step`` (1-based)."""

        return self.root / f"step_{step:02d}"

    def prepare(
        self,
        *,
        steps: int | None = None,
        protocol: RelaxProtocol | None = None,
        step_overrides: Sequence[Mapping[str, object]] | None = None,
    ) -> Calculation:
        """Prepare chained relaxation directories under one SLURM job.

        ``protocol`` (or a raw ``step_overrides`` list) supplies INCAR tags for
        every step; without it all steps share the template INCAR. ``steps``
        may be omitted when a protocol/overrides list defines the count.
        The returned calculation points to the first step; its job script is
        the driver that runs every step in the same allocation.
        """

        if protocol is not None and step_overrides is not None:
            raise ValueError("Pass either protocol or step_overrides, not both.")

        names: list[str]
        overrides: list[dict[str, object]]
        if protocol is not None:
            overrides = protocol.step_overrides()
            names = [step.name for step in protocol.steps]
        elif step_overrides is not None:
            overrides = [dict(item) for item in step_overrides]
            names = [f"step{index}" for index in range(1, len(overrides) + 1)]
            if not overrides:
                raise ValueError("step_overrides must contain at least one step.")
        else:
            count = 1 if steps is None else int(steps)
            if count < 1:
                raise ValueError("steps must be a positive integer.")
            overrides = [{} for _ in range(count)]
            names = [f"step{index}" for index in range(1, count + 1)]

        if steps is not None and int(steps) != len(overrides):
            raise ValueError(
                f"steps={steps} does not match the {len(overrides)} step(s) defined by the protocol."
            )
        steps = len(overrides)

        structure = load_poscar(self.inputs.poscar)
        calculations: list[Calculation] = []
        for step, (name, step_incar) in enumerate(zip(names, overrides), start=1):
            calculations.append(
                self.factory.prepare(
                    directory=self.step_directory(step),
                    structure=structure,
                    stage=self.stage,
                    name=f"relax_step{step:02d}",
                    metadata={
                        "branch": self.branch,
                        "step": step,
                        "step_name": name,
                        "n_steps": steps,
                        "initial_volume": float(structure.volume),
                        "combined_job": steps > 1,
                        "incar_overrides": step_incar,
                    },
                    incar_overrides=step_incar or None,
                )
            )

        first = calculations[0]
        if steps == 1:
            return first

        # Keep the rendered template as the stage script and replace job.sh
        # in step_01 with a driver, mirroring factory.prepare_relax_static.
        template = self.inputs.job_template.read_text(encoding="utf-8")
        stage_script = first.directory / STAGE_SCRIPT_NAME
        stage_script.write_text(render_job_script(template, first.job_name), encoding="utf-8")

        stage_dirs = ["."] + [
            str(Path("..") / calc.directory.name) for calc in calculations[1:]
        ]
        stage_scripts = [STAGE_SCRIPT_NAME] + [
            self.config.job_script_name for _ in calculations[1:]
        ]
        driver = render_chain_job_script(
            template,
            first.job_name,
            stage_dirs=stage_dirs,
            stage_scripts=stage_scripts,
        )
        (first.directory / self.config.job_script_name).write_text(driver, encoding="utf-8")
        return first

    def prepared_steps(self) -> list[Path]:
        """Sorted step directories that exist on disk."""

        if not self.root.exists():
            return []
        return sorted(path for path in self.root.glob("step_*") if path.is_dir())

    def _step_names(self, step_dirs: Sequence[Path]) -> str:
        names = []
        for path in step_dirs:
            try:
                names.append(str(self.read_metadata(path).get("step_name", path.name)))
            except (OSError, ValueError):
                names.append(path.name)
        return "|".join(names)

    def collect(self) -> dict[str, object]:
        """Collect energies, convergence and the final structure of the chain.

        Status values: ``not_prepared`` (no step directories), ``missing_outputs``
        (nothing started), ``running`` (the last started step has no final
        OUTCAR timing block yet: still running or killed), ``partial`` (every
        started step finished but the chain stopped early) and ``completed``.
        """

        steps = self.prepared_steps()
        started = [
            path
            for path in steps
            if (path / "OUTCAR").exists() or (path / "OSZICAR").exists()
        ]
        finished = [path for path in started if read_finished(path)]
        row: dict[str, object] = {
            "branch": self.branch,
            "path": str(self.root),
            "n_steps": len(steps),
            "step_names": self._step_names(steps),
            "steps_started": len(started),
            "steps_completed": len(finished),
            "status": "missing_outputs",
            "converged": None,
            "energy_initial_eV": None,
            "energy_final_eV": None,
            "final_structure_path": None,
            "runtime_sec": None,
        }
        if not steps:
            row["status"] = "not_prepared"
            return row
        if not started:
            return row

        if len(finished) == len(steps):
            row["status"] = "completed"
        elif len(finished) < len(started):
            row["status"] = "running"
        else:
            row["status"] = "partial"

        try:
            row["energy_initial_eV"] = read_initial_energy(started[0])
        except (FileNotFoundError, ValueError):
            pass

        # Energies and the final geometry come from the last *finished* step;
        # a step that is still running would give an intermediate CONTCAR.
        if finished:
            last = finished[-1]
            try:
                row["energy_final_eV"] = read_energy(last)
            except (FileNotFoundError, ValueError):
                pass
            row["converged"] = read_converged(last)
            contcar = last / "CONTCAR"
            if contcar.exists() and contcar.stat().st_size > 0:
                row["final_structure_path"] = str(contcar)

        runtimes = [value for value in (read_runtime_seconds(path) for path in finished) if value is not None]
        row["runtime_sec"] = float(sum(runtimes)) if runtimes else None
        return row
