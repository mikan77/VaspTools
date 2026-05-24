"""Calculation runners."""

from __future__ import annotations

from typing import Iterable, Protocol

from ..core.models import Calculation
from ..io.jobs import Submission, submit_sbatch


class CalculationRunner(Protocol):
    """Protocol for objects that submit prepared calculations."""

    def submit(
        self,
        calculations: Iterable[Calculation],
        *,
        dry_run: bool = False,
    ) -> list[Submission]:
        """Submit prepared calculations."""


class SbatchRunner:
    """Submit calculations with SLURM sbatch."""

    def __init__(self, *, script_name: str = "job.sh"):
        self.script_name = script_name

    def submit_one(self, calculation: Calculation, *, dry_run: bool = False) -> Submission:
        """Submit one calculation."""

        return submit_sbatch(
            calculation.directory,
            script_name=self.script_name,
            dry_run=dry_run,
        )

    def submit(
        self,
        calculations: Iterable[Calculation],
        *,
        dry_run: bool = False,
    ) -> list[Submission]:
        """Submit prepared calculations."""

        return [self.submit_one(calculation, dry_run=dry_run) for calculation in calculations]
