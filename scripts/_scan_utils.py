"""Utility helpers for lightweight CLI runners."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Iterable

from pymatgen.core import Structure

from VaspTools.core import MechanicalPipeline, PipelineConfig, PipelineInputs
from VaspTools.io.results import read_energy, read_volume
from VaspTools.structures import load_poscar, write_poscar

from ._scan_runtime import read_runtime_seconds


def iter_structure_files(
    directory: str | Path,
    *,
    extensions: Iterable[str] | None = None,
) -> list[Path]:
    """Collect supported structure files sorted by name."""

    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(root)

    allowed = {
        (ext.lower() if ext.startswith(".") else f".{ext.lower()}")
        for ext in (extensions or (".vasp", ".poscar", ".cif", ".cif.gz"))
    }
    extra_suffixes = {".cif.gz", ".vasp.gz"}
    files = [
        path
        for path in sorted(root.iterdir())
        if path.is_file()
        and (
            path.name.lower().endswith(tuple(allowed.union(extra_suffixes)))
            and not path.name.startswith(".")
        )
    ]
    return files


def load_structure(path: str | Path) -> Structure:
    """Load POSCAR/CIF structure."""

    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".cif" or str(path).lower().endswith(".cif.gz"):
        return Structure.from_file(str(path))
    return load_poscar(path)


def write_poscar_copy(structure: Structure, target: str | Path) -> Path:
    """Write structure to POSCAR and return path."""

    target = Path(target)
    write_poscar(structure, target)
    return target


def make_workdir_pipeline(
    *,
    structure_poscar: Path,
    workdir: Path,
    shared_inputs: PipelineInputs,
    name: str,
    volume_factors: tuple[float, ...] = PipelineConfig.volume_factors,
    strain_amplitudes: tuple[float, ...] = PipelineConfig.strain_amplitudes,
    job_script_name: str = "job.sh",
    require_kspacing: bool = True,
    vasp_kbar_to_gpa: float = -0.1,
) -> MechanicalPipeline:
    """Create a pipeline for one structure without requiring shared files in the same dir."""

    inputs = PipelineInputs(
        poscar=structure_poscar,
        potcar=shared_inputs.potcar,
        incar=shared_inputs.incar,
        job_template=shared_inputs.job_template,
    )
    config = PipelineConfig(
        workdir=workdir,
        name=name,
        volume_factors=volume_factors,
        strain_amplitudes=strain_amplitudes,
        job_script_name=job_script_name,
        require_kspacing=require_kspacing,
        vasp_kbar_to_gpa=vasp_kbar_to_gpa,
    )
    return MechanicalPipeline(inputs, config)


def parse_scan_range(spec: str) -> tuple[float, float, float]:
    """Parse `start:stop:step` style scan range."""

    parts = [part.strip() for part in spec.split(":")]
    if len(parts) == 2:
        raise ValueError(f"Range spec '{spec}' must be start:stop:step.")
    if len(parts) != 3:
        raise ValueError(f"Invalid range spec '{spec}'. Expected start:stop:step.")

    start = float(parts[0])
    stop = float(parts[1])
    step = float(parts[2])
    if step == 0.0:
        raise ValueError(f"Range spec '{spec}' has zero step.")

    if (stop > start and step < 0.0) or (stop < start and step > 0.0):
        raise ValueError(f"Range spec '{spec}' cannot reach stop {stop} from start {start} with step {step}.")

    return start, stop, step


def float_range(start: float, stop: float, step: float) -> tuple[float, ...]:
    """Create an inclusive numeric range with stable float accumulation."""

    values: list[float] = []
    tolerance = 1e-10 * (abs(stop) + abs(start) + 1.0)
    num_steps = max(0, int(abs((stop - start) / step)))

    current = start
    for _ in range(num_steps + 1):
        if step > 0 and current > stop + tolerance:
            break
        if step < 0 and current < stop - tolerance:
            break
        values.append(float(current))
        current += step

    if abs(current - stop) <= tolerance:
        if not values or abs(values[-1] - stop) > tolerance:
            values.append(float(stop))

    elif not values or abs(values[-1] - stop) > tolerance:
        values.append(stop)

    # Deduplicate near-equal values
    normalized: list[float] = []
    for value in values:
        if normalized and abs(value - normalized[-1]) <= tolerance:
            continue
        normalized.append(float(value))
    return tuple(normalized)


def parse_scan_spec(raw: str) -> tuple[str, tuple[object, ...]]:
    """Parse one scan spec from CLI to `(tag, values)`."""

    if "=" not in raw:
        raise ValueError(f"Invalid scan spec '{raw}'. Expected tag=value or tag=start:stop:step.")

    tag, value_part = (piece.strip() for piece in raw.split("=", 1))
    if not tag:
        raise ValueError(f"Empty tag in scan spec '{raw}'.")
    if ":" in value_part:
        values = float_range(*parse_scan_range(value_part))
        return tag, values

    if "," in value_part:
        values = tuple(float(part.strip()) for part in value_part.split(",") if part.strip())
        if not values:
            raise ValueError(f"No values in scan spec '{raw}'.")
        return tag, values

    return tag, (float(value_part),)


def parse_scan_specs(specs: Iterable[str]) -> dict[str, tuple[object, ...]]:
    """Merge repeated tags into scan grids."""

    result: dict[str, list[object]] = {}
    for raw in specs:
        tag, values = parse_scan_spec(raw)
        bucket = result.setdefault(tag, [])
        for value in values:
            if value not in bucket:
                bucket.append(value)
    return {tag: tuple(values) for tag, values in result.items()}


def read_result_row(
    directory: Path,
    structure_name: str,
    *,
    branch: str,
    fallback_initial_volume: float | None = None,
) -> dict[str, object]:
    """Build one result row from a finished calculation directory."""

    metadata = {}
    metadata_path = directory / "vasptools_metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}

    row: dict[str, object] = {
        "structure_file": structure_name,
        "branch": branch,
        "path": str(directory),
    }

    # Base metadata
    row["status"] = "completed" if (directory / "OUTCAR").exists() or (
        directory / "OSZICAR"
    ).exists() else "missing_outputs"
    row["stage"] = metadata.get("stage")
    row["initial_volume"] = metadata.get("initial_volume", fallback_initial_volume)
    row["final_volume"] = None
    row["final_energy"] = None
    if row["initial_volume"] is None:
        try:
            row["initial_volume"] = float(read_volume(directory))
        except Exception:
            row["initial_volume"] = None

    try:
        row["final_volume"] = read_volume(directory)
    except Exception:
        row["final_volume"] = None
    try:
        row["final_energy"] = read_energy(directory)
    except Exception:
        row["final_energy"] = None

    # Timing
    row["runtime_sec"] = read_runtime_seconds(directory)

    # Parameter columns
    for key, value in metadata.items():
        if key == "scan_tokens" and isinstance(value, dict):
            for token_key, token_value in value.items():
                row[f"param__{token_key}"] = token_value
            continue
        if key.startswith("scan_"):
            row[f"param__{key.removeprefix('scan_')}"] = value
        elif key in {"volume_factor", "strain"}:
            row[f"param__{key}"] = value

    if "strain" in metadata:
        row["param__strain"] = metadata["strain"]

    if "volume_factor" in metadata:
        row["param__volume_factor"] = metadata["volume_factor"]

    return row


def aggregate_runtime_for_static_and_relax(static_dir: Path, relax_dir: Path | None) -> float | None:
    """Return runtime static + relax (if provided)."""

    runtimes = []
    for directory in (static_dir, relax_dir):
        if directory is None or not directory.exists():
            continue
        value = read_runtime_seconds(directory)
        if value is not None:
            runtimes.append(value)
    if not runtimes:
        return None
    return float(sum(runtimes))


def flatten_rows(rows: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]]]:
    """Sort/normalize row keys while preserving insertion order."""

    ordered_keys = [
        "structure_file",
        "branch",
        "path",
        "status",
        "initial_volume",
        "runtime_sec",
        "final_volume",
        "final_energy",
        "stage",
    ]
    params: set[str] = set()
    for row in rows:
        for key in row:
            if key not in ordered_keys:
                params.add(key)
    for key in sorted(params):
        ordered_keys.append(key)
    return ordered_keys, rows
