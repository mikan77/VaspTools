"""Extract unique molecular geometries from POSCAR and CIF structures."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import csv
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from pymatgen.core import Lattice, Molecule, Structure
from pymatgen.core.periodic_table import Element
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from ..structures import load_poscar, write_poscar
from .models import (
    ExtractionResult,
    GeometryType,
    MoleculeExtractionConfig,
    MoleculeOccurrence,
    SymmetryMapping,
    SymmetryUniqueMolecule,
)


_DEFAULT_COVALENT_RADII = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "S": 1.05,
    "Cl": 1.02,
    "Br": 1.14,
    "I": 1.33,
}


@dataclass
class _MoleculeRecord:
    instance_id: str
    source_atom_indices: tuple[int, ...]
    species: tuple[str, ...]
    fractional_coords: np.ndarray
    molecule: Molecule
    formula: str
    fingerprint: str
    geometry_id: str = ""


class MolecularStructureExtractor:
    """Extract molecular geometries and symmetry-unique molecules.

    Molecule detection uses a periodic covalent-radius graph. Geometry
    equivalence is decided by fingerprint prefilter plus Kabsch RMSD. Symmetry
    equivalence is decided by applying actual space-group operations to all
    atoms, including the periodic translation needed to compare molecules that
    cross a cell boundary.
    """

    def __init__(
        self,
        input_path: str | Path,
        *,
        config: MoleculeExtractionConfig | None = None,
        covalent_radii: Mapping[str, float] | None = None,
    ):
        self.input_path = Path(input_path)
        self.config = config or MoleculeExtractionConfig()
        self.covalent_radii = {
            **_DEFAULT_COVALENT_RADII,
            **({key: float(value) for key, value in covalent_radii.items()} if covalent_radii else {}),
        }
        self.structure: Structure | None = None
        self._molecules: list[_MoleculeRecord] = []
        self._geometries: list[GeometryType] = []
        self._symmetry_unique: list[SymmetryUniqueMolecule] = []
        self._symmetry_mappings: list[SymmetryMapping] = []
        self._space_group_symbol = "Unknown"
        self._space_group_number: int | None = None
        self._symmetry_operations_count = 0

    @classmethod
    def from_file(
        cls,
        input_path: str | Path,
        *,
        bond_tolerance_factor: float = 1.20,
        fingerprint_tolerance_A: float = 0.05,
        rmsd_tolerance_A: float = 0.15,
        symprec_A: float = 0.05,
        angle_tolerance_deg: float = 5.0,
        vacuum_padding_A: float = 10.0,
        allow_reflection_equivalence: bool = False,
        covalent_radii: Mapping[str, float] | None = None,
    ) -> "MolecularStructureExtractor":
        return cls(
            input_path,
            config=MoleculeExtractionConfig(
                bond_tolerance_factor=bond_tolerance_factor,
                fingerprint_tolerance_A=fingerprint_tolerance_A,
                rmsd_tolerance_A=rmsd_tolerance_A,
                symprec_A=symprec_A,
                angle_tolerance_deg=angle_tolerance_deg,
                vacuum_padding_A=vacuum_padding_A,
                allow_reflection_equivalence=allow_reflection_equivalence,
            ),
            covalent_radii=covalent_radii,
        )

    @property
    def geometries(self) -> tuple[GeometryType, ...]:
        return tuple(self._geometries)

    @property
    def occurrences(self) -> tuple[MoleculeOccurrence, ...]:
        return tuple(self._public_occurrence(item) for item in self._molecules)

    @property
    def symmetry_unique_molecules(self) -> tuple[SymmetryUniqueMolecule, ...]:
        return tuple(self._symmetry_unique)

    @property
    def symmetry_mappings(self) -> tuple[SymmetryMapping, ...]:
        return tuple(self._symmetry_mappings)

    def extract(self, output_dir: str | Path) -> ExtractionResult:
        """Extract molecules and write isolated POSCAR/XYZ and CSV reports."""

        self._molecules = []
        self._geometries = []
        self._symmetry_unique = []
        self._symmetry_mappings = []
        self._load_structure()
        self._read_symmetry_metadata()
        self._identify_molecules()
        self._classify_geometries()
        self._classify_symmetry_orbits()

        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        self._write_geometry_outputs(output_root)
        self._write_symmetry_outputs(output_root)
        summary = self._summary()
        self._write_reports(output_root, summary)

        return ExtractionResult(
            output_dir=output_root,
            geometries=self.geometries,
            occurrences=self.occurrences,
            symmetry_unique_molecules=self.symmetry_unique_molecules,
            symmetry_mappings=self.symmetry_mappings,
            summary=summary,
        )

    def _load_structure(self) -> None:
        if not self.input_path.exists():
            raise FileNotFoundError(self.input_path)
        lower_name = self.input_path.name.lower()
        if lower_name.endswith(".cif") or lower_name.endswith(".cif.gz"):
            self.structure = Structure.from_file(str(self.input_path))
        else:
            self.structure = load_poscar(self.input_path)
        if len(self.structure) == 0:
            raise ValueError("The input structure contains no atoms.")

    def _read_symmetry_metadata(self) -> None:
        assert self.structure is not None
        try:
            analyzer = SpacegroupAnalyzer(
                self.structure,
                symprec=self.config.symprec_A,
                angle_tolerance=self.config.angle_tolerance_deg,
            )
            self._space_group_symbol = analyzer.get_space_group_symbol()
            self._space_group_number = analyzer.get_space_group_number()
            self._symmetry_operations_count = len(analyzer.get_symmetry_operations())
        except Exception:
            self._space_group_symbol = "Unknown"
            self._space_group_number = None
            self._symmetry_operations_count = 0

    def _covalent_radius(self, symbol: str) -> float:
        if symbol in self.covalent_radii:
            return self.covalent_radii[symbol]
        radius = Element(symbol).covalent_radius
        if radius is None:
            raise ValueError(f"No covalent radius is available for element {symbol}.")
        return float(radius)

    def _identify_molecules(self) -> None:
        assert self.structure is not None
        n_sites = len(self.structure)
        adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {
            index: [] for index in range(n_sites)
        }
        radii = [self._covalent_radius(site.specie.symbol) for site in self.structure.sites]
        search_radius = max(radii) * 2.0 * self.config.bond_tolerance_factor

        for index, site in enumerate(self.structure.sites):
            for neighbor in self.structure.get_neighbors(site, search_radius):
                other = int(neighbor.index)
                image = tuple(int(value) for value in neighbor.image)
                if other == index and image == (0, 0, 0):
                    continue
                cutoff = (radii[index] + radii[other]) * self.config.bond_tolerance_factor
                if float(neighbor.nn_distance) > cutoff:
                    continue
                adjacency[index].append((other, image))
                adjacency[other].append((index, tuple(-value for value in image)))

        visited: set[int] = set()
        records: list[_MoleculeRecord] = []
        for root in range(n_sites):
            if root in visited:
                continue
            offsets = {root: (0, 0, 0)}
            queue: deque[int] = deque([root])
            visited.add(root)
            while queue:
                current = queue.popleft()
                for neighbor, image in adjacency[current]:
                    candidate = tuple(
                        int(value)
                        for value in np.asarray(offsets[current], dtype=int) + np.asarray(image)
                    )
                    if neighbor not in offsets:
                        offsets[neighbor] = candidate
                        visited.add(neighbor)
                        queue.append(neighbor)
                    elif offsets[neighbor] != candidate:
                        raise ValueError(
                            "The covalent graph is periodic/infinite; "
                            "finite molecular fragments cannot be extracted."
                        )

            members = tuple(sorted(offsets))
            species = tuple(self.structure[index].specie.symbol for index in members)
            fractional = np.asarray(
                [
                    np.asarray(self.structure[index].frac_coords, dtype=float)
                    + np.asarray(offsets[index], dtype=float)
                    for index in members
                ],
                dtype=float,
            )
            coordinates = fractional @ self.structure.lattice.matrix
            molecule = Molecule(species, coordinates)
            records.append(
                _MoleculeRecord(
                    instance_id=f"I{len(records) + 1:06d}",
                    source_atom_indices=members,
                    species=species,
                    fractional_coords=fractional,
                    molecule=molecule,
                    formula=molecule.composition.alphabetical_formula,
                    fingerprint=self._fingerprint(species, coordinates),
                )
            )
        self._molecules = records

    def _classify_geometries(self) -> None:
        geometries: list[GeometryType] = []
        representatives: dict[str, _MoleculeRecord] = {}
        for molecule in self._molecules:
            geometry_id = ""
            for geometry in geometries:
                if geometry.fingerprint != molecule.fingerprint:
                    continue
                representative = representatives[geometry.geometry_id]
                if self._molecules_match(representative.molecule, molecule.molecule):
                    geometry_id = geometry.geometry_id
                    break
            if not geometry_id:
                geometry_id = f"G{len(geometries) + 1:06d}"
                representatives[geometry_id] = molecule
                geometries.append(
                    GeometryType(
                        geometry_id=geometry_id,
                        formula=molecule.formula,
                        atom_count=len(molecule.species),
                        fingerprint=molecule.fingerprint,
                        occurrence_ids=(),
                    )
                )
            molecule.geometry_id = geometry_id

        for geometry in geometries:
            occurrence_ids = tuple(
                item.instance_id for item in self._molecules if item.geometry_id == geometry.geometry_id
            )
            index = geometries.index(geometry)
            geometries[index] = replace(geometry, occurrence_ids=occurrence_ids)
        self._geometries = geometries

    def _classify_symmetry_orbits(self) -> None:
        assert self.structure is not None
        if not self._molecules:
            self._symmetry_unique = []
            return

        try:
            analyzer = SpacegroupAnalyzer(
                self.structure,
                symprec=self.config.symprec_A,
                angle_tolerance=self.config.angle_tolerance_deg,
            )
            operations = analyzer.get_symmetry_operations(cartesian=False)
        except Exception:
            operations = []

        parent = list(range(len(self._molecules)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first: int, second: int) -> None:
            first_root, second_root = find(first), find(second)
            if first_root != second_root:
                parent[second_root] = first_root

        for operation_index, operation in enumerate(operations):
            for source_index, source in enumerate(self._molecules):
                transformed_frac = np.asarray(
                    [operation.operate(coord) for coord in source.fractional_coords],
                    dtype=float,
                )
                transformed_com = np.mean(transformed_frac, axis=0)
                for target_index, target in enumerate(self._molecules):
                    if source.formula != target.formula:
                        continue
                    shift = np.rint(np.mean(target.fractional_coords, axis=0) - transformed_com)
                    shifted_frac = transformed_frac + shift
                    shifted_cart = shifted_frac @ self.structure.lattice.matrix
                    rmsd = self._direct_rmsd(
                        source.species,
                        shifted_cart,
                        target.species,
                        target.molecule.cart_coords,
                    )
                    if rmsd is None or rmsd > self.config.rmsd_tolerance_A:
                        continue
                    union(source_index, target_index)
                    self._symmetry_mappings.append(
                        SymmetryMapping(
                            source_instance_id=source.instance_id,
                            target_instance_id=target.instance_id,
                            operation_index=operation_index,
                            rmsd_A=rmsd,
                        )
                    )
                    break

        groups: dict[int, list[_MoleculeRecord]] = {}
        for index, molecule in enumerate(self._molecules):
            groups.setdefault(find(index), []).append(molecule)
        unique: list[SymmetryUniqueMolecule] = []
        for group in groups.values():
            representative = min(group, key=lambda item: item.instance_id)
            unique.append(
                SymmetryUniqueMolecule(
                    unique_molecule_id=f"U{len(unique) + 1:06d}",
                    geometry_id=representative.geometry_id,
                    representative_instance_id=representative.instance_id,
                    occurrence_ids=tuple(item.instance_id for item in group),
                    orbit_size=len(group),
                )
            )
        self._symmetry_unique = unique

    def _molecules_match(self, first: Molecule, second: Molecule) -> bool:
        first_species = [site.specie.symbol for site in first.sites]
        second_species = [site.specie.symbol for site in second.sites]
        rmsd = self._best_kabsch_rmsd(
            first_species,
            first.cart_coords,
            second_species,
            second.cart_coords,
        )
        return rmsd is not None and rmsd <= self.config.rmsd_tolerance_A

    def _direct_rmsd(
        self,
        first_species: Sequence[str],
        first_coords: np.ndarray,
        second_species: Sequence[str],
        second_coords: np.ndarray,
    ) -> float | None:
        if sorted(first_species) != sorted(second_species):
            return None
        mappings = self._atom_mappings(first_species, first_coords, second_species, second_coords)
        if not mappings:
            return None
        return min(
            float(
                np.sqrt(
                    np.mean(
                        np.sum(
                            (first_coords - second_coords[list(mapping)]) ** 2,
                            axis=1,
                        )
                    )
                )
            )
            for mapping in mappings
        )

    def _best_kabsch_rmsd(
        self,
        first_species: Sequence[str],
        first_coords: np.ndarray,
        second_species: Sequence[str],
        second_coords: np.ndarray,
    ) -> float | None:
        if sorted(first_species) != sorted(second_species):
            return None
        mappings = self._atom_mappings(first_species, first_coords, second_species, second_coords)
        if not mappings:
            return None
        best = float("inf")
        for mapping in mappings:
            aligned = second_coords[list(mapping)]
            first_centered = first_coords - np.mean(first_coords, axis=0)
            second_centered = aligned - np.mean(aligned, axis=0)
            covariance = first_centered.T @ second_centered
            u, _, vt = np.linalg.svd(covariance)
            rotation = vt.T @ u.T
            if not self.config.allow_reflection_equivalence and np.linalg.det(rotation) < 0:
                vt[-1, :] *= -1.0
                rotation = vt.T @ u.T
            rotated = first_centered @ rotation
            best = min(best, float(np.sqrt(np.mean(np.sum((rotated - second_centered) ** 2, axis=1)))))
        return best

    def _atom_mappings(
        self,
        first_species: Sequence[str],
        first_coords: np.ndarray,
        second_species: Sequence[str],
        second_coords: np.ndarray,
    ) -> list[tuple[int, ...]]:
        if len(first_species) != len(second_species):
            return []
        if len(first_species) == 1:
            return [(0,)]
        first_distances = np.linalg.norm(
            first_coords[:, None, :] - first_coords[None, :, :], axis=2
        )
        second_distances = np.linalg.norm(
            second_coords[:, None, :] - second_coords[None, :, :], axis=2
        )
        tolerance = max(self.config.fingerprint_tolerance_A * 2.0, self.config.rmsd_tolerance_A)
        order = sorted(
            range(len(first_species)),
            key=lambda index: (
                first_species[index],
                tuple(round(value / tolerance) for value in first_distances[index]),
            ),
        )
        mappings: list[tuple[int, ...]] = []
        mapping: dict[int, int] = {}
        used: set[int] = set()

        def visit(position: int) -> None:
            if len(mappings) >= 256:
                return
            if position == len(order):
                mappings.append(tuple(mapping[index] for index in range(len(first_species))))
                return
            first_index = order[position]
            for second_index, symbol in enumerate(second_species):
                if symbol != first_species[first_index] or second_index in used:
                    continue
                if not all(
                    abs(first_distances[first_index, mapped_first] - second_distances[second_index, mapped_second])
                    <= tolerance
                    for mapped_first, mapped_second in mapping.items()
                ):
                    continue
                mapping[first_index] = second_index
                used.add(second_index)
                visit(position + 1)
                used.remove(second_index)
                del mapping[first_index]

        visit(0)
        return mappings

    def _fingerprint(self, species: Sequence[str], coords: np.ndarray) -> str:
        centered = np.asarray(coords, dtype=float) - np.mean(coords, axis=0)
        entries = []
        tolerance = self.config.fingerprint_tolerance_A
        for i in range(len(species)):
            for j in range(i + 1, len(species)):
                entries.append(
                    (
                        tuple(sorted((species[i], species[j]))),
                        int(round(float(np.linalg.norm(centered[i] - centered[j])) / tolerance)),
                    )
                )
        payload = {"species": sorted(species), "distances": sorted(entries)}
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _write_geometry_outputs(self, output_root: Path) -> None:
        representatives = {
            geometry.geometry_id: next(
                item for item in self._molecules if item.geometry_id == geometry.geometry_id
            )
            for geometry in self._geometries
        }
        for geometry in self._geometries:
            molecule = representatives[geometry.geometry_id].molecule
            xyz_path = output_root / "geometries" / f"{geometry.geometry_id}.xyz"
            poscar_path = output_root / "geometries" / f"{geometry.geometry_id}.vasp"
            self._write_xyz(xyz_path, molecule, geometry.geometry_id)
            write_poscar(self._isolated_structure(molecule), poscar_path)
            index = self._geometries.index(geometry)
            self._geometries[index] = replace(geometry, xyz_path=xyz_path, poscar_path=poscar_path)

    def _write_symmetry_outputs(self, output_root: Path) -> None:
        for unique in self._symmetry_unique:
            molecule = next(
                item.molecule
                for item in self._molecules
                if item.instance_id == unique.representative_instance_id
            )
            xyz_path = output_root / "symmetry_unique" / f"{unique.unique_molecule_id}.xyz"
            poscar_path = output_root / "symmetry_unique" / f"{unique.unique_molecule_id}.vasp"
            self._write_xyz(xyz_path, molecule, unique.unique_molecule_id)
            write_poscar(self._isolated_structure(molecule), poscar_path)
            index = self._symmetry_unique.index(unique)
            self._symmetry_unique[index] = replace(unique, xyz_path=xyz_path, poscar_path=poscar_path)

    def _isolated_structure(self, molecule: Molecule) -> Structure:
        coordinates = np.asarray(molecule.cart_coords, dtype=float)
        lower = np.min(coordinates, axis=0)
        upper = np.max(coordinates, axis=0)
        spans = np.maximum(upper - lower, 1.0)
        padding = self.config.vacuum_padding_A
        lattice = Lattice(np.diag(spans + 2.0 * padding))
        shifted = coordinates - lower + padding
        return Structure(lattice, molecule.species, shifted, coords_are_cartesian=True)

    def _write_xyz(self, path: Path, molecule: Molecule, comment: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [str(len(molecule)), comment]
        lines.extend(
            f"{site.specie.symbol} {site.coords[0]:.8f} {site.coords[1]:.8f} {site.coords[2]:.8f}"
            for site in molecule.sites
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_reports(self, output_root: Path, summary: Mapping[str, object]) -> None:
        reports = output_root / "statistics"
        self._write_csv(
            reports / "molecule_instances.csv",
            [
                {
                    "molecule_instance_id": item.instance_id,
                    "geometry_id": item.geometry_id,
                    "formula": item.formula,
                    "source_atom_indices": "|".join(str(value) for value in item.source_atom_indices),
                    "com_frac_x": float(np.mean(item.fractional_coords[:, 0])),
                    "com_frac_y": float(np.mean(item.fractional_coords[:, 1])),
                    "com_frac_z": float(np.mean(item.fractional_coords[:, 2])),
                    "com_cart_x": float(item.molecule.center_of_mass[0]),
                    "com_cart_y": float(item.molecule.center_of_mass[1]),
                    "com_cart_z": float(item.molecule.center_of_mass[2]),
                }
                for item in self._molecules
            ],
        )
        self._write_csv(
            reports / "geometry_types.csv",
            [
                {
                    "geometry_id": item.geometry_id,
                    "formula": item.formula,
                    "atom_count": item.atom_count,
                    "fingerprint": item.fingerprint,
                    "occurrence_count": len(item.occurrence_ids),
                    "occurrence_ids": "|".join(item.occurrence_ids),
                    "xyz_path": self._relative_path(item.xyz_path, output_root),
                    "poscar_path": self._relative_path(item.poscar_path, output_root),
                }
                for item in self._geometries
            ],
        )
        self._write_csv(
            reports / "symmetry_unique_molecules.csv",
            [
                {
                    "unique_molecule_id": item.unique_molecule_id,
                    "geometry_id": item.geometry_id,
                    "representative_instance_id": item.representative_instance_id,
                    "orbit_size": item.orbit_size,
                    "occurrence_ids": "|".join(item.occurrence_ids),
                    "xyz_path": self._relative_path(item.xyz_path, output_root),
                    "poscar_path": self._relative_path(item.poscar_path, output_root),
                }
                for item in self._symmetry_unique
            ],
        )
        self._write_csv(
            reports / "symmetry_mappings.csv",
            [
                {
                    "source_instance_id": item.source_instance_id,
                    "target_instance_id": item.target_instance_id,
                    "operation_index": item.operation_index,
                    "rmsd_A": item.rmsd_A,
                }
                for item in self._symmetry_mappings
            ],
        )
        self._write_csv(reports / "summary.csv", [dict(summary)])

    def _summary(self) -> dict[str, object]:
        return {
            "input_path": str(self.input_path),
            "molecule_count": len(self._molecules),
            "unique_geometry_count": len(self._geometries),
            "symmetry_unique_count": len(self._symmetry_unique),
            "space_group": self._space_group_symbol,
            "space_group_number": self._space_group_number,
            "symmetry_operations": self._symmetry_operations_count,
            "bond_tolerance_factor": self.config.bond_tolerance_factor,
            "fingerprint_tolerance_A": self.config.fingerprint_tolerance_A,
            "rmsd_tolerance_A": self.config.rmsd_tolerance_A,
            "symprec_A": self.config.symprec_A,
            "angle_tolerance_deg": self.config.angle_tolerance_deg,
            "vacuum_padding_A": self.config.vacuum_padding_A,
        }

    def _public_occurrence(self, item: _MoleculeRecord) -> MoleculeOccurrence:
        com_frac = np.mean(item.fractional_coords, axis=0)
        com_cart = np.asarray(item.molecule.center_of_mass, dtype=float)
        return MoleculeOccurrence(
            molecule_instance_id=item.instance_id,
            geometry_id=item.geometry_id,
            formula=item.formula,
            source_atom_indices=item.source_atom_indices,
            com_frac=tuple(float(value) for value in com_frac),
            com_cart_A=tuple(float(value) for value in com_cart),
        )

    @staticmethod
    def _relative_path(path: Path | None, root: Path) -> str:
        if path is None:
            return ""
        return str(path.relative_to(root))

    @staticmethod
    def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("\n", encoding="utf-8")
            return
        fields = list(rows[0])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
