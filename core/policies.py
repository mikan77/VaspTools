"""Workflow policies for VASP input generation."""

from __future__ import annotations

from typing import Mapping

from ..io.incar import Incar, make_stage_incar, validate_kspacing_incar


class IncarPolicy:
    """Encapsulates all automatic INCAR decisions.

    Subclass this to add a different set of stage rules while keeping the same
    calculation factory and workflow modes.
    """

    def __init__(self, *, require_kspacing: bool = True):
        self.require_kspacing = require_kspacing

    def validate_template(self, template: Mapping[str, object]) -> None:
        """Validate the user INCAR template before calculations are prepared."""

        if self.require_kspacing:
            validate_kspacing_incar(template)

    def make_incar(
        self,
        template: Mapping[str, object],
        *,
        system: str,
        stage: str,
        extra_overrides: Mapping[str, object] | None = None,
    ) -> Incar:
        """Create the INCAR for one workflow stage."""

        return make_stage_incar(
            template,
            system=system,
            stage=stage,
            require_kspacing=self.require_kspacing,
            extra_overrides=extra_overrides,
        )
