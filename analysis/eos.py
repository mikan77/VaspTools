"""Equation-of-state fitting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import json
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.optimize import curve_fit

from ..io.results import EV_PER_ANG3_TO_GPA


@dataclass(frozen=True)
class EOSPoint:
    """One E(V) point."""

    volume: float
    energy: float
    path: str = ""


@dataclass(frozen=True)
class EOSFitResult:
    """Birch-Murnaghan EOS fit result."""

    e0_eV: float
    v0_ang3: float
    b0_eV_ang3: float
    b0_GPa: float
    b0_prime: float
    residual_rms_eV: float
    n_points: int


def birch_murnaghan_energy(
    volume: np.ndarray | float,
    e0: float,
    v0: float,
    b0: float,
    b0_prime: float,
) -> np.ndarray:
    """Third-order Birch-Murnaghan E(V). B0 is in eV/A^3."""

    volume_array = np.asarray(volume, dtype=float)
    eta = (v0 / volume_array) ** (2.0 / 3.0)
    return e0 + (9.0 * v0 * b0 / 16.0) * (
        b0_prime * (eta - 1.0) ** 3
        + (eta - 1.0) ** 2 * (6.0 - 4.0 * eta)
    )


def fit_eos(points: Iterable[EOSPoint]) -> EOSFitResult:
    """Fit E(V) points with the Birch-Murnaghan EOS."""

    point_list = sorted(list(points), key=lambda point: point.volume)
    if len(point_list) < 4:
        raise ValueError("At least 4 EOS points are required to fit E0, V0, B0, and B0'.")

    volumes = np.array([point.volume for point in point_list], dtype=float)
    energies = np.array([point.energy for point in point_list], dtype=float)
    if np.any(volumes <= 0):
        raise ValueError("EOS volumes must be positive.")

    min_index = int(np.argmin(energies))
    initial = (
        float(energies[min_index]),
        float(volumes[min_index]),
        0.05,
        4.0,
    )
    bounds = (
        (-np.inf, min(volumes) * 0.5, 1.0e-8, 0.0),
        (np.inf, max(volumes) * 1.5, np.inf, 20.0),
    )
    params, _ = curve_fit(
        birch_murnaghan_energy,
        volumes,
        energies,
        p0=initial,
        bounds=bounds,
        maxfev=20000,
    )
    predicted = birch_murnaghan_energy(volumes, *params)
    rms = float(np.sqrt(np.mean((energies - predicted) ** 2)))
    e0, v0, b0, b0_prime = [float(value) for value in params]
    return EOSFitResult(
        e0_eV=e0,
        v0_ang3=v0,
        b0_eV_ang3=b0,
        b0_GPa=b0 * EV_PER_ANG3_TO_GPA,
        b0_prime=b0_prime,
        residual_rms_eV=rms,
        n_points=len(point_list),
    )


def write_eos_points_csv(points: Iterable[EOSPoint], path: str | Path) -> None:
    """Write E(V) points to CSV."""

    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("volume", "energy", "path"))
        writer.writeheader()
        for point in points:
            writer.writerow(asdict(point))


def write_eos_fit_json(result: EOSFitResult, path: str | Path) -> None:
    """Write EOS fit result to JSON."""

    Path(path).write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
