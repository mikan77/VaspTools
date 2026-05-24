"""Base workflow mode class."""

from __future__ import annotations

from abc import ABC
from pathlib import Path

from ..core.factory import VaspCalculationFactory
from ..core.models import PipelineConfig, PipelineInputs


class WorkflowMode(ABC):
    """Base class for add-on workflow modes.

    New modes should subclass this and use ``self.factory.prepare(...)`` to
    create VASP calculation directories under ``self.root``.
    """

    branch = "base"

    def __init__(
        self,
        *,
        inputs: PipelineInputs,
        config: PipelineConfig,
        factory: VaspCalculationFactory,
    ):
        self.inputs = inputs
        self.config = config
        self.factory = factory

    @property
    def root(self) -> Path:
        """Root directory for this mode."""

        return self.config.workdir / self.branch

    @property
    def report_dir(self) -> Path:
        """Shared report directory."""

        return self.config.workdir / "reports"

    def read_metadata(self, directory: str | Path) -> dict[str, object]:
        """Read calculation metadata."""

        return self.factory.read_metadata(directory)
