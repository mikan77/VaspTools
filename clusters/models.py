"""Models for molecular-crystal n-mer generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class NMerConfig:
    """Configuration for periodic molecular-cluster generation."""

    cutoffs: Mapping[int, float]
    supercell_padding_A: float = 5.0
    bond_tolerance_factor: float = 1.20
    fingerprint_tolerance_A: float = 0.05
    rmsd_tolerance_A: float = 0.15
    symprec_A: float = 0.05
    angle_tolerance_deg: float = 5.0
    max_neighbors: int = 1000
    use_symmetry_metadata: bool = True
    allow_reflection_equivalence: bool = False

    def __post_init__(self) -> None:
        normalized = {int(order): float(cutoff) for order, cutoff in self.cutoffs.items()}
        if not normalized:
            raise ValueError("cutoffs must contain at least one n-mer order.")
        if any(order < 2 for order in normalized):
            raise ValueError("cutoff orders must be integers greater than or equal to 2.")
        if any(cutoff <= 0.0 for cutoff in normalized.values()):
            raise ValueError("all n-mer cutoffs must be positive.")
        if self.supercell_padding_A <= 0.0:
            raise ValueError("supercell_padding_A must be positive.")
        if self.bond_tolerance_factor <= 0.0:
            raise ValueError("bond_tolerance_factor must be positive.")
        if self.fingerprint_tolerance_A <= 0.0:
            raise ValueError("fingerprint_tolerance_A must be positive.")
        if self.rmsd_tolerance_A <= 0.0:
            raise ValueError("rmsd_tolerance_A must be positive.")
        if self.symprec_A <= 0.0:
            raise ValueError("symprec_A must be positive.")
        if self.max_neighbors < 1:
            raise ValueError("max_neighbors must be positive.")
        object.__setattr__(self, "cutoffs", normalized)

    @property
    def max_order(self) -> int:
        """Largest requested n-mer order."""

        return max(self.cutoffs)


@dataclass(frozen=True)
class OrcaConfig:
    """User-controlled ORCA input settings."""

    method_line: str = "! DLPNO-CCSD(T) aug-cc-pVDZ aug-cc-pVDZ/C AutoAux TightPNO TightSCF PModel"
    maxcore: int = 8000
    nprocs: int = 2
    charge: int = 0
    multiplicity: int = 1

    def __post_init__(self) -> None:
        if not self.method_line.strip():
            raise ValueError("method_line must be non-empty.")
        if self.maxcore <= 0 or self.nprocs <= 0:
            raise ValueError("maxcore and nprocs must be positive.")
        if self.multiplicity < 1:
            raise ValueError("multiplicity must be positive.")


@dataclass(frozen=True)
class MoleculeType:
    """One unique molecular geometry used for energy reuse."""

    molecule_type_id: str
    formula: str
    atom_count: int
    fingerprint: str
    xyz_path: Path | None = None
    charge: int = 0
    multiplicity: int = 1


@dataclass(frozen=True)
class MoleculeInstance:
    """One concrete molecule occurrence in the generated supercell."""

    molecule_instance_id: str
    molecule_type_id: str
    source_atom_indices: tuple[int, ...]
    translation: tuple[int, int, int]
    com_cart_A: tuple[float, float, float]
    is_central: bool


@dataclass(frozen=True)
class ClusterType:
    """One unique n-mer geometry."""

    cluster_type_id: str
    order: int
    central_instance_id: str
    member_instance_ids: tuple[str, ...]
    member_type_ids: tuple[str, ...]
    multiplicity: int
    cutoff_A: float
    fingerprint: str
    xyz_path: Path | None = None
    inp_path: Path | None = None
    full_inp_path: Path | None = None
    mbe_inp_path: Path | None = None


@dataclass(frozen=True)
class ClusterOccurrence:
    """One occurrence of a unique cluster in the supercell."""

    occurrence_id: str
    cluster_type_id: str
    central_instance_id: str
    member_instance_ids: tuple[str, ...]


@dataclass(frozen=True)
class GenerationResult:
    """Result returned by the high-level generator."""

    output_dir: Path
    molecule_types: tuple[MoleculeType, ...]
    molecule_instances: tuple[MoleculeInstance, ...]
    cluster_types: tuple[ClusterType, ...]
    cluster_occurrences: tuple[ClusterOccurrence, ...]
    summary: tuple[dict[str, object], ...]
    calculation_manifest: tuple[dict[str, object], ...]
    batch_paths: tuple[Path, ...]
