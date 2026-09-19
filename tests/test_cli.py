from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VaspTools.structures import write_poscar

from test_core import write_basic_inputs
from test_molecules import two_water_conformations


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "VaspTools.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


class CliTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        (root / "structs").mkdir()
        write_poscar(two_water_conformations(), root / "structs" / "a.vasp")
        (root / "tmpl").mkdir()
        write_basic_inputs(root / "tmpl")

    def test_init_submit_collect_with_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)

            init = run_cli(["relax", "init"], root)
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertTrue((root / "vasptools.yaml").exists())
            self.assertTrue((root / "relax.yaml").exists())
            # second init keeps the files
            again = run_cli(["relax", "init"], root)
            self.assertIn("keeping it", again.stdout)

            submit = run_cli(["relax", "submit", "--dry-run"], root)
            self.assertEqual(submit.returncode, 0, submit.stderr)
            self.assertIn("2 step(s)", submit.stdout)  # relax.yaml template has two steps
            self.assertTrue((root / "tmpl" / "relax_runs" / "1000_a" / "relax" / "step_02" / "INCAR").exists())
            self.assertTrue((root / "summary.csv").exists())

            # a flag overrides the config value
            override = run_cli(["relax", "submit", "--dry-run", "--output-csv", "other.csv"], root)
            self.assertEqual(override.returncode, 0, override.stderr)
            self.assertTrue((root / "other.csv").exists())

            collect = run_cli(["relax", "collect"], root)
            self.assertEqual(collect.returncode, 0, collect.stderr)
            with (root / "summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["status"], "missing_outputs")

    def test_config_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            (root / "bad.yaml").write_text("structures_dir: structs\nfoo: 1\n")
            bad = run_cli(["relax", "submit", "--config", "bad.yaml", "--dry-run"], root)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("unknown key(s) ['foo']", bad.stderr + bad.stdout)

            (root / "min.yaml").write_text("template_dir: tmpl\n")
            missing = run_cli(["relax", "submit", "--config", "min.yaml", "--dry-run"], root)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("--structures-dir", missing.stderr)
            self.assertIn("vasptools.yaml", missing.stderr)

            none = run_cli(["relax", "submit", "--dry-run"], root)  # no config at all
            self.assertNotEqual(none.returncode, 0)
            self.assertIn("missing --structures-dir", none.stderr)

    def test_legacy_and_passthrough_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "run2" / "calc_00001" / "calc_00001"
            (work / "step_1_final").mkdir(parents=True)
            write_poscar(two_water_conformations(), root / "run2" / "calc_00001" / "POSCAR_00001")
            (work / "step_1_final" / "OUTCAR").write_text(
                "free  energy   TOTEN  = -1.0 eV\n General timing and accounting informations for this job:\n"
            )
            write_poscar(two_water_conformations(), work / "step_1_final" / "CONTCAR")
            legacy = run_cli(["legacy", "collect", "run2"], root)
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertTrue((root / "run2_summary.csv").exists())
            self.assertTrue((root / "run2_relaxed" / "00001_POSCAR").exists())

            multi = run_cli(["multi", "--help"], root)
            self.assertEqual(multi.returncode, 0)
            self.assertIn("--volume-factors", multi.stdout)
            scan = run_cli(["scan", "--help"], root)
            self.assertIn("--scan", scan.stdout)


if __name__ == "__main__":
    unittest.main()
