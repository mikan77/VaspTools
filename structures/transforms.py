"""Structure transformations for EOS and elastic workflows."""

from __future__ import annotations

from typing import Iterable

import numpy as np
from pymatgen.core import Lattice, Structure


VOIGT_LABELS = ("xx", "yy", "zz", "yz", "xz", "xy")


def scale_structure_to_volume(
    structure: Structure,
    *,
    volume_factor: float | None = None,
    target_volume: float | None = None,
) -> Structure:
    """Return a copy of structure scaled to a target volume."""

    if (volume_factor is None) == (target_volume is None):
        raise ValueError("Provide exactly one of volume_factor or target_volume.")
    if volume_factor is not None and volume_factor <= 0:
        raise ValueError("volume_factor must be positive.")
    if target_volume is not None and target_volume <= 0:
        raise ValueError("target_volume must be positive.")

    scaled = structure.copy()
    final_volume = target_volume if target_volume is not None else structure.volume * volume_factor
    scaled.scale_lattice(float(final_volume))
    return scaled


def strain_matrix_from_voigt(strain: Iterable[float]) -> np.ndarray:
    """Convert Voigt strain [xx, yy, zz, yz, xz, xy] to a 3x3 tensor.

    Shear components are engineering strains in Voigt notation, so the tensor
    off-diagonal values are half of the Voigt values.
    """

    values = np.asarray(list(strain), dtype=float)
    if values.shape != (6,):
        raise ValueError("strain must contain 6 Voigt components.")

    return np.array(
        [
            [values[0], values[5] / 2.0, values[4] / 2.0],
            [values[5] / 2.0, values[1], values[3] / 2.0],
            [values[4] / 2.0, values[3] / 2.0, values[2]],
        ],
        dtype=float,
    )


def apply_strain(structure: Structure, strain: Iterable[float]) -> Structure:
    """Apply a small Voigt strain to the lattice and keep fractional coords."""

    strain_tensor = strain_matrix_from_voigt(strain)
    deformation = np.eye(3) + strain_tensor
    determinant = float(np.linalg.det(deformation))
    if determinant <= 0:
        raise ValueError("strain produces a non-positive lattice volume.")

    new_lattice_matrix = structure.lattice.matrix @ deformation.T
    return Structure(
        Lattice(new_lattice_matrix),
        structure.species,
        structure.frac_coords,
        coords_are_cartesian=False,
        site_properties=structure.site_properties,
    )


def _format_signed_float(value: float) -> str:
    sign = "p" if value >= 0 else "m"
    body = f"{abs(value):.5f}".rstrip("0").rstrip(".")
    return f"{sign}{body.replace('.', 'p')}"


def generate_strain_vectors(
    amplitudes: Iterable[float] = (0.005, 0.01),
    *,
    components: Iterable[int] | None = None,
) -> list[tuple[str, np.ndarray]]:
    """Generate one-component +/- Voigt strain vectors."""

    component_list = list(range(6)) if components is None else list(components)
    for component in component_list:
        if component < 0 or component > 5:
            raise ValueError("strain component indices must be between 0 and 5.")

    positive_amplitudes = sorted({abs(float(value)) for value in amplitudes})
    if not positive_amplitudes or any(value <= 0 for value in positive_amplitudes):
        raise ValueError("strain amplitudes must be positive.")

    vectors: list[tuple[str, np.ndarray]] = []
    for component in component_list:
        for amplitude in positive_amplitudes:
            for signed in (-amplitude, amplitude):
                strain = np.zeros(6, dtype=float)
                strain[component] = signed
                name = f"eps_{VOIGT_LABELS[component]}_{_format_signed_float(signed)}"
                vectors.append((name, strain))
    return vectors
