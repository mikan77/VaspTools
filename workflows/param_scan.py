"""Parameter scan workflow mode.

`ParamScanMode` creates a set of static calculations from one reference
structure while overriding user-specified INCAR tags for every point of a
multi-parameter grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, Mapping

from ..core.models import Calculation
from ..io.results import read_energy, read_volume
from ..structures import load_poscar
from .base import WorkflowMode


__all__ = ["ParamScanMode", "ScanPoint"]


def _normalize_scan_parameter_values(values: Iterable[object]) -> tuple[object, ...]:
    """Normalize parameter values to a non-empty immutable sequence."""

    values_list = [value for value in values]
    if not values_list:
        return ()

    deduplicated: list[object] = []
    for value in values_list:
        if value not in deduplicated:
            deduplicated.append(value)
    return tuple(deduplicated)


def _format_value_for_dir(value: object) -> str:
    """Create a filesystem-friendly token from a scan value."""

    if isinstance(value, float):
        text = f"{value:.10g}"
    else:
        text = str(value)

    text = text.strip()
    text = text.replace(" ", "_")
    text = text.replace("+", "p").replace("-", "m").replace(".", "p")
    text = text.replace("/", "p")

    if not text:
        return "na"
    return text


@dataclass(frozen=True)
class ScanPoint:
    """One point in parameter grid."""

    index: int
    values: dict[str, object]


class ParamScanMode(WorkflowMode):
    """Prepare parameter scan calculations by overriding INCAR tags."""

    branch = "param_scan"

    def prepare_calculations(
        self,
        scan_parameters: Mapping[str, Iterable[object]],
        *,
        stage: str = "single_static",
    ) -> list[Calculation]:
        """Prepare one VASP job for each parameter combination.

        Parameters are provided as mapping from INCAR tag name to values.
        Every combination of values is prepared as an independent calculation.
        """

        if not isinstance(scan_parameters, Mapping):
            raise TypeError("scan_parameters must be a mapping.")
        if not scan_parameters:
            raise ValueError("scan_parameters must not be empty.")
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("stage must be a non-empty string.")

        normalized: dict[str, tuple[object, ...]] = {}
        for key, values in scan_parameters.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Invalid scan parameter name: {key!r}")
            normalized_values = _normalize_scan_parameter_values(values)
            if not normalized_values:
                raise ValueError(f"No values provided for scan parameter {key!r}")
            normalized[key.strip()] = normalized_values

        structure = load_poscar(self.inputs.poscar)
        keys = list(normalized.keys())
        value_sets = [normalized[key] for key in keys]

        calculations: list[Calculation] = []
        scan_root = self.root / "scan"

        for index, values in enumerate(product(*value_sets), start=1):
            current = dict(zip(keys, values))
            token = "_".join(
                f"{key}_{_format_value_for_dir(current[key])}" for key in keys
            )
            calc_name = f"param_scan_{index:03d}_{token}" if keys else f"param_scan_{index:03d}"
            label = f"{index:04d}_{token}" if keys else f"{index:04d}"

            scan_point = {
                str(key): current[key] for key in keys
            }
            calculations.append(
                self.factory.prepare(
                    directory=scan_root / label,
                    structure=structure,
                    stage=stage,
                    name=calc_name,
                    metadata={
                        "branch": self.branch,
                        "scan_index": index,
                        "scan_tokens": scan_point,
                        "initial_volume": float(structure.volume),
                        **{f"scan_{k}": v for k, v in scan_point.items()},
                    },
                    incar_overrides=scan_point,
                )
            )
        return calculations

    def collect_results(self) -> list[dict[str, object]]:
        """Collect parsed metrics for all prepared scan points.

        Returned records contain a status and optional parsed
        `final_volume`/`final_energy`.
        """

        scan_root = self.root / "scan"
        if not scan_root.exists():
            return []

        rows: list[dict[str, object]] = []
        for directory in sorted(path for path in scan_root.iterdir() if path.is_dir()):
            try:
                metadata = self.read_metadata(directory)
            except Exception:
                metadata = {}

            if metadata.get("branch") not in (None, self.branch):
                continue

            row: dict[str, object] = {
                "path": str(directory),
                "branch": self.branch,
                "scan_tokens": metadata.get("scan_tokens", {}),
                "status": "missing_outputs",
                "scan_index": metadata.get("scan_index"),
                "initial_volume": metadata.get("initial_volume"),
            }
            try:
                row["final_volume"] = read_volume(directory)
                row["final_energy"] = read_energy(directory)
                row["status"] = "completed"
            except (FileNotFoundError, ValueError):
                row["final_volume"] = None
                row["final_energy"] = None

            rows.append(row)

        return rows

    def available_scan_points(self) -> list[ScanPoint]:
        """List scan points discovered from prepared scan directories."""

        scan_root = self.root / "scan"
        if not scan_root.exists():
            return []

        points: list[ScanPoint] = []
        for directory in sorted(path for path in scan_root.iterdir() if path.is_dir()):
            metadata = self.read_metadata(directory)
            tokens = metadata.get("scan_tokens", {})
            if not isinstance(tokens, dict):
                continue
            points.append(
                ScanPoint(
                    index=int(metadata.get("scan_index", 0) or 0),
                    values={str(key): value for key, value in tokens.items()},
                )
            )
        return points
