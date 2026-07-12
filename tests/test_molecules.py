from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

from pymatgen.core import Lattice, Structure

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VaspTools import MolecularStructureExtractor
from VaspTools.structures import write_poscar


def two_water_conformations() -> Structure:
    lattice = Lattice.cubic(50.0)
    species = ["O", "H", "H", "O", "H", "H"]
    coordinates = [
        [10.00, 10.00, 10.00],
        [10.96, 10.00, 10.00],
        [9.76, 10.93, 10.00],
        [18.00, 10.00, 10.00],
        [18.96, 10.00, 10.00],
        [17.52, 10.83, 10.00],
    ]
    return Structure(lattice, species, coordinates, coords_are_cartesian=True)


def inversion_related_waters() -> Structure:
    lattice = Lattice.cubic(20.0)
    species = ["O", "H", "H", "O", "H", "H"]
    coordinates = [
        [5.00, 5.00, 5.00],
        [5.96, 5.00, 5.00],
        [5.00, 5.93, 5.00],
        [15.00, 15.00, 15.00],
        [14.04, 15.00, 15.00],
        [15.00, 14.07, 15.00],
    ]
    return Structure(lattice, species, coordinates, coords_are_cartesian=True)


class MolecularExtractorTests(unittest.TestCase):
    def test_extracts_geometry_types_and_adaptive_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "POSCAR"
            write_poscar(two_water_conformations(), input_path)

            extractor = MolecularStructureExtractor.from_file(
                input_path,
                fingerprint_tolerance_A=0.02,
                rmsd_tolerance_A=0.10,
                vacuum_padding_A=7.0,
            )
            result = extractor.extract(root / "output")

            self.assertEqual(len(result.occurrences), 2)
            self.assertEqual(len(result.geometries), 2)
            self.assertEqual(len(result.symmetry_unique_molecules), 2)
            self.assertEqual(
                len(list((root / "output" / "geometries").glob("*.xyz"))),
                2,
            )
            self.assertEqual(
                len(list((root / "output" / "symmetry_unique").glob("*.vasp"))),
                2,
            )
            isolated = Structure.from_file(root / "output" / "geometries" / "G000001.vasp")
            self.assertAlmostEqual(isolated.lattice.a, 15.20, places=2)
            self.assertAlmostEqual(isolated.lattice.b, 15.00, places=2)
            self.assertAlmostEqual(isolated.lattice.c, 15.00, places=2)

            with (root / "output" / "statistics" / "geometry_types.csv").open(
                encoding="utf-8",
                newline="",
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["poscar_path"].endswith(".vasp") for row in rows))

    def test_groups_inversion_related_molecules_into_one_orbit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "POSCAR"
            write_poscar(inversion_related_waters(), input_path)

            result = MolecularStructureExtractor.from_file(input_path).extract(root / "output")

            self.assertEqual(len(result.geometries), 1)
            self.assertEqual(len(result.symmetry_unique_molecules), 1)
            self.assertEqual(result.symmetry_unique_molecules[0].orbit_size, 2)
            self.assertTrue(result.symmetry_mappings)


if __name__ == "__main__":
    unittest.main()
