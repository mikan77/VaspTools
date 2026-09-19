"""Parsers for VASP outputs used by the workflow."""

from __future__ import annotations

from pathlib import Path
import re

import numpy as np

from ..structures import load_poscar


EV_PER_ANG3_TO_GPA = 160.21766208
FLOAT_RE = r"[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][-+]?\d+)?"


def parse_energy_from_outcar(text: str) -> float:
    """Return the final TOTEN energy in eV from OUTCAR text."""

    matches = re.findall(
        rf"free\s+energy\s+TOTEN\s+=\s*({FLOAT_RE})",
        text,
    )
    if not matches:
        raise ValueError("No TOTEN energy found in OUTCAR.")
    return float(matches[-1])


def parse_first_energy_from_outcar(text: str) -> float:
    """Return the first TOTEN energy in eV from OUTCAR text.

    For a relaxation this is the energy of the initial geometry.
    """

    matches = re.findall(
        rf"free\s+energy\s+TOTEN\s+=\s*({FLOAT_RE})",
        text,
    )
    if not matches:
        raise ValueError("No TOTEN energy found in OUTCAR.")
    return float(matches[0])


def parse_first_energy_from_oszicar(text: str) -> float:
    """Return the first free energy in eV from OSZICAR text."""

    matches = re.findall(rf"\bF=\s*({FLOAT_RE})", text)
    if not matches:
        raise ValueError("No F= energy found in OSZICAR.")
    return float(matches[0])


def outcar_reached_required_accuracy(text: str) -> bool:
    """Return True if VASP reported ionic convergence in OUTCAR text."""

    return "reached required accuracy" in text


def outcar_finished(text: str) -> bool:
    """Return True if VASP wrote its final timing block, i.e. the run ended normally.

    A killed or still-running calculation has an OUTCAR without this block.
    """

    return "General timing and accounting" in text


def parse_energy_from_oszicar(text: str) -> float:
    """Return the final free energy in eV from OSZICAR text."""

    matches = re.findall(rf"\bF=\s*({FLOAT_RE})", text)
    if not matches:
        raise ValueError("No F= energy found in OSZICAR.")
    return float(matches[-1])


def read_energy(directory: str | Path) -> float:
    """Read final energy from OUTCAR or OSZICAR in a calculation directory."""

    directory = Path(directory)
    outcar = directory / "OUTCAR"
    if outcar.exists():
        return parse_energy_from_outcar(outcar.read_text(errors="ignore"))

    oszicar = directory / "OSZICAR"
    if oszicar.exists():
        return parse_energy_from_oszicar(oszicar.read_text(errors="ignore"))

    raise FileNotFoundError(f"No OUTCAR or OSZICAR found in {directory}.")


def read_initial_energy(directory: str | Path) -> float:
    """Read the first ionic-step energy from OUTCAR or OSZICAR."""

    directory = Path(directory)
    outcar = directory / "OUTCAR"
    if outcar.exists():
        return parse_first_energy_from_outcar(outcar.read_text(errors="ignore"))

    oszicar = directory / "OSZICAR"
    if oszicar.exists():
        return parse_first_energy_from_oszicar(oszicar.read_text(errors="ignore"))

    raise FileNotFoundError(f"No OUTCAR or OSZICAR found in {directory}.")


def read_finished(directory: str | Path) -> bool | None:
    """Return True if OUTCAR shows a normally finished run, None if OUTCAR is missing."""

    outcar = Path(directory) / "OUTCAR"
    if not outcar.exists():
        return None
    return outcar_finished(outcar.read_text(errors="ignore"))


def read_converged(directory: str | Path) -> bool | None:
    """Return ionic convergence flag from OUTCAR, or None if OUTCAR is missing."""

    outcar = Path(directory) / "OUTCAR"
    if not outcar.exists():
        return None
    return outcar_reached_required_accuracy(outcar.read_text(errors="ignore"))


def read_volume(directory: str | Path) -> float:
    """Read final volume from CONTCAR if present, otherwise POSCAR."""

    directory = Path(directory)
    for filename in ("CONTCAR", "POSCAR"):
        path = directory / filename
        if path.exists() and path.stat().st_size > 0:
            return float(load_poscar(path).volume)
    raise FileNotFoundError(f"No POSCAR/CONTCAR found in {directory}.")


def parse_stress_from_outcar(text: str, *, kbar_to_gpa: float = -0.1) -> np.ndarray:
    """Return the final VASP stress as [xx, yy, zz, yz, xz, xy] in GPa.

    VASP prints stress in kB with columns xx yy zz xy yz zx. The default sign
    converts VASP's convention to the tensile-positive convention commonly used
    for Hooke-law fitting.
    """

    stress_values: list[list[float]] = []
    for line in text.splitlines():
        if "in kB" not in line:
            continue
        tail = line.split("in kB", 1)[1]
        numbers = re.findall(FLOAT_RE, tail)
        if len(numbers) >= 6:
            stress_values.append([float(value) for value in numbers[:6]])

    if not stress_values:
        raise ValueError("No 'in kB' stress tensor line found in OUTCAR.")

    xx, yy, zz, xy, yz, zx = stress_values[-1]
    voigt = np.array([xx, yy, zz, yz, zx, xy], dtype=float)
    return voigt * float(kbar_to_gpa)


def read_stress(directory: str | Path, *, kbar_to_gpa: float = -0.1) -> np.ndarray:
    """Read final stress from OUTCAR in GPa."""

    outcar = Path(directory) / "OUTCAR"
    if not outcar.exists():
        raise FileNotFoundError(f"No OUTCAR found in {directory}.")
    return parse_stress_from_outcar(outcar.read_text(errors="ignore"), kbar_to_gpa=kbar_to_gpa)
