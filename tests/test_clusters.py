from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

from pymatgen.core import Lattice, Structure

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VaspTools import CrystalNMerGenerator, OrcaConfig
from VaspTools.structures import write_poscar


def two_co2_structure() -> Structure:
    lattice = Lattice.cubic(50.0)
    species = ["C", "O", "O", "C", "O", "O"]
    coordinates = [
        [10.0, 10.0, 10.0],
        [11.16, 10.0, 10.0],
        [8.84, 10.0, 10.0],
        [18.0, 10.0, 10.0],
        [19.16, 10.0, 10.0],
        [16.84, 10.0, 10.0],
    ]
    return Structure(lattice, species, coordinates, coords_are_cartesian=True)


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


class ClusterGeneratorTests(unittest.TestCase):
    def test_generates_unique_dimer_types_and_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "POSCAR"
            write_poscar(two_co2_structure(), input_path)

            generator = CrystalNMerGenerator.from_file(
                input_path,
                cutoffs={2: 12.0},
                supercell_padding_A=5.0,
                orca=OrcaConfig(charge=0, multiplicity=1),
            )
            result = generator.generate(root / "output", batch_size=1)

            self.assertEqual(len(result.molecule_types), 1)
            self.assertEqual(len(result.cluster_types), 1)
            self.assertEqual(result.cluster_types[0].order, 2)
            self.assertEqual(result.cluster_types[0].multiplicity, 2)
            self.assertEqual(len(result.cluster_occurrences), 2)

            full_inp = root / "output" / "calculations" / "full_cluster" / "n2" / "N2_000001.inp"
            mbe_monomer = root / "output" / "calculations" / "mbe" / "monomers" / "M000001.inp"
            mbe_dimer = root / "output" / "calculations" / "mbe" / "n2" / "N2_000001.inp"
            self.assertEqual(full_inp.read_text(encoding="utf-8").count("$new_job"), 1)
            self.assertEqual(mbe_monomer.read_text(encoding="utf-8").count("$new_job"), 1)
            self.assertEqual(mbe_dimer.read_text(encoding="utf-8").count("$new_job"), 1)
            self.assertNotIn(" : ", full_inp.read_text(encoding="utf-8"))

            with (root / "output" / "statistics" / "cluster_summary.csv").open(
                encoding="utf-8",
                newline="",
            ) as handle:
                summary = list(csv.DictReader(handle))
            self.assertEqual(summary[0]["order"], "2")
            self.assertEqual(summary[0]["unique_clusters"], "1")
            self.assertEqual(summary[0]["total_occurrences"], "2")

    def test_keeps_two_conformations_as_distinct_molecule_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "POSCAR"
            write_poscar(two_water_conformations(), input_path)

            generator = CrystalNMerGenerator.from_file(
                input_path,
                cutoffs={2: 12.0},
                supercell_padding_A=5.0,
            )
            result = generator.generate(root / "output", batch_size=None)

            self.assertEqual(len(result.molecule_types), 2)
            self.assertEqual(
                {item.molecule_type_id for item in result.molecule_types},
                {"M000001", "M000002"},
            )
            self.assertEqual(len(result.cluster_types), 1)
            self.assertEqual(
                set(result.cluster_types[0].member_type_ids),
                {"M000001", "M000002"},
            )
            self.assertEqual(
                len(list((root / "output" / "calculations" / "mbe" / "monomers").glob("*.inp"))),
                2,
            )

            with (root / "output" / "statistics" / "cluster_types.csv").open(
                encoding="utf-8",
                newline="",
            ) as handle:
                cluster_rows = list(csv.DictReader(handle))
            self.assertEqual(
                set(cluster_rows[0]["member_type_ids"].split("|")),
                {"M000001", "M000002"},
            )


if __name__ == "__main__":
    unittest.main()
