"""Elastic tensor fitting and mechanical-property post-processing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class StrainStressPoint:
    """One strain/stress data point."""

    name: str
    strain: tuple[float, float, float, float, float, float]
    stress_GPa: tuple[float, float, float, float, float, float]
    path: str = ""


@dataclass(frozen=True)
class ElasticFitResult:
    """Fitted elastic tensor result."""

    Cij_GPa: tuple[tuple[float, ...], ...]
    intercept_stress_GPa: tuple[float, float, float, float, float, float]
    residual_rms_GPa: float
    n_points: int


def fit_elastic_tensor(
    points: Iterable[StrainStressPoint],
    *,
    include_intercept: bool = True,
    symmetrize: bool = True,
) -> ElasticFitResult:
    """Fit stress = C strain by least squares."""

    point_list = list(points)
    if len(point_list) < 6:
        raise ValueError("At least 6 strain/stress points are required.")

    strains = np.array([point.strain for point in point_list], dtype=float)
    stresses = np.array([point.stress_GPa for point in point_list], dtype=float)
    if strains.shape[1] != 6 or stresses.shape[1] != 6:
        raise ValueError("strain and stress vectors must have 6 Voigt components.")

    if include_intercept:
        design = np.column_stack([strains, np.ones(len(point_list))])
    else:
        design = strains

    coefficients, _, _, _ = np.linalg.lstsq(design, stresses, rcond=None)
    if include_intercept:
        c_matrix = coefficients[:6, :].T
        intercept = coefficients[6, :]
    else:
        c_matrix = coefficients.T
        intercept = np.zeros(6, dtype=float)

    if symmetrize:
        c_matrix = 0.5 * (c_matrix + c_matrix.T)

    predicted = design @ coefficients
    rms = float(np.sqrt(np.mean((stresses - predicted) ** 2)))
    return ElasticFitResult(
        Cij_GPa=tuple(tuple(float(value) for value in row) for row in c_matrix),
        intercept_stress_GPa=tuple(float(value) for value in intercept),
        residual_rms_GPa=rms,
        n_points=len(point_list),
    )


def _as_matrix(cij: Iterable[Iterable[float]]) -> np.ndarray:
    matrix = np.array(cij, dtype=float)
    if matrix.shape != (6, 6):
        raise ValueError("Cij must be a 6x6 matrix.")
    return matrix


def check_mechanical_stability(
    cij: Iterable[Iterable[float]],
    *,
    tolerance_GPa: float = 1.0e-6,
) -> dict[str, object]:
    """Generic stability check from positive definiteness of Cij."""

    matrix = 0.5 * (_as_matrix(cij) + _as_matrix(cij).T)
    eigenvalues = np.linalg.eigvalsh(matrix)
    return {
        "stable": bool(np.all(eigenvalues > tolerance_GPa)),
        "criterion": "positive_definite_Cij",
        "min_eigenvalue_GPa": float(np.min(eigenvalues)),
        "eigenvalues_GPa": [float(value) for value in eigenvalues],
        "tolerance_GPa": float(tolerance_GPa),
    }


def mechanical_properties(cij: Iterable[Iterable[float]]) -> dict[str, object]:
    """Calculate isotropic VRH moduli and simple anisotropy metrics."""

    c = 0.5 * (_as_matrix(cij) + _as_matrix(cij).T)
    s = np.linalg.inv(c)

    c11, c22, c33 = c[0, 0], c[1, 1], c[2, 2]
    c12, c13, c23 = c[0, 1], c[0, 2], c[1, 2]
    c44, c55, c66 = c[3, 3], c[4, 4], c[5, 5]

    b_voigt = (c11 + c22 + c33 + 2.0 * (c12 + c13 + c23)) / 9.0
    g_voigt = (
        c11 + c22 + c33 - c12 - c13 - c23 + 3.0 * (c44 + c55 + c66)
    ) / 15.0

    s11, s22, s33 = s[0, 0], s[1, 1], s[2, 2]
    s12, s13, s23 = s[0, 1], s[0, 2], s[1, 2]
    s44, s55, s66 = s[3, 3], s[4, 4], s[5, 5]

    b_reuss = 1.0 / (s11 + s22 + s33 + 2.0 * (s12 + s13 + s23))
    g_reuss = 15.0 / (
        4.0 * (s11 + s22 + s33)
        - 4.0 * (s12 + s13 + s23)
        + 3.0 * (s44 + s55 + s66)
    )

    b_hill = 0.5 * (b_voigt + b_reuss)
    g_hill = 0.5 * (g_voigt + g_reuss)
    young = 9.0 * b_hill * g_hill / (3.0 * b_hill + g_hill)
    poisson = (3.0 * b_hill - 2.0 * g_hill) / (2.0 * (3.0 * b_hill + g_hill))
    universal_anisotropy = 5.0 * (g_voigt / g_reuss) + (b_voigt / b_reuss) - 6.0

    stability = check_mechanical_stability(c)
    return {
        "bulk_modulus_voigt_GPa": float(b_voigt),
        "bulk_modulus_reuss_GPa": float(b_reuss),
        "bulk_modulus_hill_GPa": float(b_hill),
        "shear_modulus_voigt_GPa": float(g_voigt),
        "shear_modulus_reuss_GPa": float(g_reuss),
        "shear_modulus_hill_GPa": float(g_hill),
        "young_modulus_GPa": float(young),
        "poisson_ratio": float(poisson),
        "universal_anisotropy_index": float(universal_anisotropy),
        "linear_compressibility_a_1_per_GPa": float(s11 + s12 + s13),
        "linear_compressibility_b_1_per_GPa": float(s12 + s22 + s23),
        "linear_compressibility_c_1_per_GPa": float(s13 + s23 + s33),
        "mechanical_stability": stability,
    }


def write_elastic_fit_json(result: ElasticFitResult, path: str | Path) -> None:
    """Write fitted Cij to JSON."""

    Path(path).write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def write_mechanical_properties_json(properties: dict[str, object], path: str | Path) -> None:
    """Write mechanical properties to JSON."""

    Path(path).write_text(json.dumps(properties, indent=2) + "\n", encoding="utf-8")
