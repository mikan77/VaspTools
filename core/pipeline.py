"""High-level OOP coordinator for VASP mechanical-property workflows."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Iterable, Mapping

from ..analysis.elastic import ElasticFitResult, StrainStressPoint
from ..analysis.eos import EOSFitResult, EOSPoint
from ..execution.runners import CalculationRunner, SbatchRunner
from ..io.jobs import Submission, resolve_job_template_path
from ..workflows.base import WorkflowMode
from ..workflows.elastic import ElasticMode
from ..workflows.eos import EOSMode
from ..workflows.param_scan import ParamScanMode
from .factory import VaspCalculationFactory
from .models import Calculation, PipelineConfig, PipelineInputs
from .policies import IncarPolicy


class MechanicalPipeline:
    """Coordinator that composes workflow modes, factories, policies, and runners.

    Existing convenience methods are kept for compatibility. New functionality
    should usually be added as a ``WorkflowMode`` subclass and registered with
    ``register_mode``.
    """

    def __init__(
        self,
        inputs: PipelineInputs,
        config: PipelineConfig,
        *,
        incar_policy: IncarPolicy | None = None,
        runner: CalculationRunner | None = None,
        modes: dict[str, WorkflowMode] | None = None,
    ):
        self.inputs = self._normalize_inputs(inputs)
        self.config = self._normalize_config(config)
        self._validate_inputs()

        self.incar_policy = incar_policy or IncarPolicy(
            require_kspacing=self.config.require_kspacing
        )
        self.factory = VaspCalculationFactory(
            inputs=self.inputs,
            config=self.config,
            incar_policy=self.incar_policy,
        )
        self.runner = runner or SbatchRunner(script_name=self.config.job_script_name)

        self.modes: dict[str, WorkflowMode] = {}
        self.eos = EOSMode(inputs=self.inputs, config=self.config, factory=self.factory)
        self.elastic = ElasticMode(inputs=self.inputs, config=self.config, factory=self.factory)
        self.param_scan = ParamScanMode(
            inputs=self.inputs, config=self.config, factory=self.factory
        )
        self.register_mode("eos", self.eos)
        self.register_mode("elastic", self.elastic)
        self.register_mode("param_scan", self.param_scan)
        if modes:
            for name, mode in modes.items():
                self.register_mode(name, mode)

    @classmethod
    def from_workdir(
        cls,
        workdir: str | Path,
        *,
        name: str = "vasp_mechanics",
        volume_factors: tuple[float, ...] = PipelineConfig.volume_factors,
        strain_amplitudes: tuple[float, ...] = PipelineConfig.strain_amplitudes,
        job_script_name: str = "job.sh",
        input_poscar: str = "POSCAR",
        input_potcar: str = "POTCAR",
        input_incar: str = "INCAR",
        input_job_template: str | None = None,
        require_kspacing: bool = True,
        vasp_kbar_to_gpa: float = -0.1,
        incar_policy: IncarPolicy | None = None,
        runner: CalculationRunner | None = None,
    ) -> "MechanicalPipeline":
        """Create a pipeline from a workdir containing POSCAR/POTCAR/INCAR and a *.sh job template."""

        workdir = Path(workdir)
        inputs = PipelineInputs(
            poscar=workdir / input_poscar,
            potcar=workdir / input_potcar,
            incar=workdir / input_incar,
            job_template=resolve_job_template_path(workdir, input_job_template),
        )
        config = PipelineConfig(
            workdir=workdir,
            name=name,
            volume_factors=tuple(volume_factors),
            strain_amplitudes=tuple(strain_amplitudes),
            job_script_name=job_script_name,
            require_kspacing=require_kspacing,
            vasp_kbar_to_gpa=vasp_kbar_to_gpa,
        )
        return cls(inputs, config, incar_policy=incar_policy, runner=runner)

    @staticmethod
    def _normalize_inputs(inputs: PipelineInputs) -> PipelineInputs:
        return PipelineInputs(
            poscar=Path(inputs.poscar),
            potcar=Path(inputs.potcar),
            incar=Path(inputs.incar),
            job_template=Path(inputs.job_template),
        )

    @staticmethod
    def _normalize_config(config: PipelineConfig) -> PipelineConfig:
        return PipelineConfig(
            workdir=Path(config.workdir),
            name=config.name,
            volume_factors=tuple(config.volume_factors),
            strain_amplitudes=tuple(config.strain_amplitudes),
            job_script_name=config.job_script_name,
            require_kspacing=config.require_kspacing,
            vasp_kbar_to_gpa=config.vasp_kbar_to_gpa,
        )

    def _validate_inputs(self) -> None:
        for path in (
            self.inputs.poscar,
            self.inputs.potcar,
            self.inputs.incar,
            self.inputs.job_template,
        ):
            if not path.exists():
                raise FileNotFoundError(path)
            if not path.is_file():
                raise ValueError(f"Expected a file: {path}")

    def register_mode(self, name: str, mode: WorkflowMode) -> None:
        """Register or replace a workflow mode."""

        if not name:
            raise ValueError("mode name must be non-empty.")
        self.modes[name] = mode

    def get_mode(self, name: str) -> WorkflowMode:
        """Return a registered workflow mode."""

        try:
            return self.modes[name]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow mode: {name}") from exc

    def prepare_eos_relaxations(
        self,
        volume_factors: Iterable[float] | None = None,
        *,
        combined_job: bool = True,
    ) -> list[Calculation]:
        """Prepare EOS jobs, combined with static by default."""

        return self.eos.prepare_relaxations(volume_factors, combined_job=combined_job)

    def prepare_relax(
        self,
        *,
        branch: str = "both",
        volume_factors: Iterable[float] | None = None,
        strain_amplitudes: Iterable[float] | None = None,
        combined_job: bool = True,
    ) -> list[Calculation]:
        """Prepare jobs for ``eos``, ``elastic``, or ``both``.

        By default each returned job runs relaxation and static sequentially
        under one SLURM job ID. Set ``combined_job=False`` for the legacy
        relaxation-only preparation flow.
        """

        calculations: list[Calculation] = []
        for branch_name in self._normalize_branches(branch):
            if branch_name == "eos":
                calculations.extend(
                    self.prepare_eos_relaxations(
                        volume_factors,
                        combined_job=combined_job,
                    )
                )
            elif branch_name == "elastic":
                calculations.extend(
                    self.prepare_elastic_relaxations(
                        strain_amplitudes,
                        combined_job=combined_job,
                    )
                )
        return calculations

    def prepare_eos_statics(self, *, allow_unrelaxed: bool = False) -> list[Calculation]:
        """Prepare EOS static calculations."""

        return self.eos.prepare_statics(allow_unrelaxed=allow_unrelaxed)

    def collect_eos_points(self) -> list[EOSPoint]:
        """Collect E(V) points."""

        return self.eos.collect_points()

    def fit_eos_from_statics(self) -> EOSFitResult:
        """Fit EOS from static calculations."""

        return self.eos.fit_from_statics()

    def prepare_elastic_relaxations(
        self,
        strain_amplitudes: Iterable[float] | None = None,
        *,
        combined_job: bool = True,
    ) -> list[Calculation]:
        """Prepare elastic jobs, combined with static by default."""

        return self.elastic.prepare_relaxations(
            strain_amplitudes,
            combined_job=combined_job,
        )

    def prepare_elastic_statics(self, *, allow_unrelaxed: bool = False) -> list[Calculation]:
        """Prepare elastic static calculations."""

        return self.elastic.prepare_statics(allow_unrelaxed=allow_unrelaxed)

    def prepare_static(
        self,
        *,
        branch: str = "both",
        allow_unrelaxed: bool = False,
    ) -> list[Calculation]:
        """Prepare static calculations for ``eos``, ``elastic``, or ``both``."""

        calculations: list[Calculation] = []
        for branch_name in self._normalize_branches(branch):
            if branch_name == "eos":
                calculations.extend(self.prepare_eos_statics(allow_unrelaxed=allow_unrelaxed))
            elif branch_name == "elastic":
                calculations.extend(
                    self.prepare_elastic_statics(allow_unrelaxed=allow_unrelaxed)
                )
        return calculations

    def prepare_param_scan(
        self,
        scan_parameters: Mapping[str, Iterable[object]],
        *,
        stage: str = "single_static",
    ) -> list[Calculation]:
        """Prepare parameter-scan calculations from the reference structure."""

        return self.param_scan.prepare_calculations(scan_parameters, stage=stage)

    def collect_param_scan(self) -> list[dict[str, object]]:
        """Collect parameter-scan result rows."""

        return self.param_scan.collect_results()

    def collect_elastic_points(self) -> list[StrainStressPoint]:
        """Collect strain/stress points."""

        return self.elastic.collect_points()

    def fit_elastic_from_statics(self) -> tuple[ElasticFitResult, dict[str, object]]:
        """Fit elastic tensor from static calculations."""

        return self.elastic.fit_from_statics()

    def fit(self, *, branch: str = "both") -> dict[str, object]:
        """Fit completed static calculations for ``eos``, ``elastic``, or ``both``."""

        results: dict[str, object] = {}
        for branch_name in self._normalize_branches(branch):
            if branch_name == "eos":
                results["eos"] = self.fit_eos_from_statics()
            elif branch_name == "elastic":
                elastic_fit, properties = self.fit_elastic_from_statics()
                results["elastic"] = {
                    "fit": elastic_fit,
                    "properties": properties,
                }
        return results

    def submit_calculations(
        self,
        calculations: Iterable[Calculation],
        *,
        dry_run: bool = False,
    ) -> list[Submission]:
        """Submit prepared calculations through the configured runner."""

        return self.runner.submit(calculations, dry_run=dry_run)

    def submit(
        self,
        calculations: Iterable[Calculation],
        *,
        dry_run: bool = False,
    ) -> list[Submission]:
        """Submit prepared calculations through the configured runner."""

        return self.submit_calculations(calculations, dry_run=dry_run)

    @staticmethod
    def _normalize_branches(branch: str) -> tuple[str, ...]:
        if branch == "both":
            return ("eos", "elastic")
        if branch in {"eos", "elastic"}:
            return (branch,)
        raise ValueError("branch must be 'eos', 'elastic', or 'both'.")

    def write_summary(self) -> None:
        """Write a small machine-readable workflow summary."""

        self.config.workdir.mkdir(parents=True, exist_ok=True)
        summary = {
            "inputs": {key: str(value) for key, value in asdict(self.inputs).items()},
            "config": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(self.config).items()
            },
            "registered_modes": sorted(self.modes),
            "rules": {
                "KPOINTS": "not_created_not_copied",
                "k_mesh_source": "KSPACING_from_INCAR",
                "ISIF": 2,
                "static_required": {
                    "ALGO": "All",
                    "ISEARCH": 1,
                    "IBRION": -1,
                    "NSW": 0,
                },
            },
        }
        (self.config.workdir / "vasptools_summary.json").write_text(
            json.dumps(summary, indent=2) + "\n",
            encoding="utf-8",
        )
