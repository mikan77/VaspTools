"""Helpers for param-scan and batch workflows."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable


OUTCAR_RUNTIME_PATTERNS = (
    re.compile(r"Elapsed time\s*\(sec\)\s*:\s*([+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)"),
    re.compile(r"Total CPU time used\s*\(sec\)\s*:\s*([+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)"),
    re.compile(r"Walltime:\s*([+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)"),
)


def _parse_float(value: str) -> float | None:
    """Try to parse a single float token."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_runtime_seconds_from_text(text: str) -> float | None:
    """Extract walltime in seconds from VASP/queue output text."""

    for pattern in OUTCAR_RUNTIME_PATTERNS:
        matches = pattern.findall(text)
        if not matches:
            continue
        value = matches[-1]
        parsed = _parse_float(value)
        if parsed is not None and parsed >= 0.0:
            return parsed
    return None


def _iter_runtime_sources(directory: Path) -> Iterable[Path]:
    """Yield known files that may contain runtime information."""

    for filename in (
        "OUTCAR",
        "vasp.out",
        "vasp.stdout",
        "vasp.err",
        "slurm.out",
    ):
        path = directory / filename
        if path.exists():
            yield path
    # SLURM writes to slurm-<jobid>.out in many installations
    for path in directory.glob("slurm-*.out"):
        yield path


def read_runtime_seconds(directory: str | Path) -> float | None:
    """Read runtime in seconds from one calculation directory."""

    directory_path = Path(directory)
    for source in _iter_runtime_sources(directory_path):
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        runtime = read_runtime_seconds_from_text(text)
        if runtime is not None:
            return runtime
    return None
