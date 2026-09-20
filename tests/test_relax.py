from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pymatgen.core import Lattice, Structure

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VaspTools import (
    FreeRelaxIncarPolicy,
    MechanicalPipeline,
    PipelineConfig,
    RelaxMode,
    RelaxProtocol,
    RelaxStep,
    load_relax_protocol,
)
from VaspTools.io.incar import Incar
from VaspTools.io.jobs import render_chain_job_script
from VaspTools.io.results import (
    outcar_reached_required_accuracy,
    parse_first_energy_from_outcar,
    read_initial_energy,
)
from VaspTools.molecules import count_molecules, find_molecular_fragments
from VaspTools.scripts._scan_utils import describe_structure, iter_structure_files
from VaspTools.structures import load_poscar, scale_structure_to_volume, write_poscar

from test_core import write_basic_inputs
from test_molecules import two_water_conformations


OUTCAR_TWO_STEPS = (
    "free  energy   TOTEN  = -10.000000 eV\n"
    "free  energy   TOTEN  = -10.500000 eV\n"
    " reached required accuracy - stopping structural energy minimisation\n"
    " General timing and accounting informations for this job:\n"
    " Elapsed time (sec):  100.0\n"
)
# OUTCAR of a run that is still going (or was killed): no final timing block.
OUTCAR_RUNNING = "free  energy   TOTEN  = -9.000000 eV\nfree  energy   TOTEN  = -9.200000 eV\n"


class FreeRelaxPolicyTests(unittest.TestCase):
    def test_keeps_user_relaxation_tags(self):
        template = Incar({"ENCUT": 520, "KSPACING": 0.25, "ISIF": 3, "IBRION": 2, "NSW": 50})
        incar = FreeRelaxIncarPolicy().make_incar(template, system="x", stage="free_relax")
        self.assertEqual(incar["ISIF"], 3)
        self.assertEqual(incar["IBRION"], 2)
        self.assertEqual(incar["NSW"], 50)
        self.assertEqual(incar["SYSTEM"], "x")

    def test_requires_kspacing_and_applies_static_tags(self):
        with self.assertRaises(ValueError):
            FreeRelaxIncarPolicy().make_incar(Incar({"ENCUT": 520}), system="x", stage="free_relax")
        incar = FreeRelaxIncarPolicy(require_kspacing=False).make_incar(
            Incar({"ISIF": 3}), system="x", stage="final_static"
        )
        self.assertEqual(incar["NSW"], 0)
        self.assertEqual(incar["IBRION"], -1)


class ChainJobScriptTests(unittest.TestCase):
    def test_driver_runs_stages_and_forwards_contcar(self):
        template = "#!/bin/sh\n#SBATCH --job-name={job_name}\n#SBATCH -N 1\nvasp_std\n"
        driver = render_chain_job_script(
            template,
            "chain",
            stage_dirs=[".", "../step_02", "../step_03"],
            stage_scripts=[".vasptools_stage.sh", "job.sh", "job.sh"],
        )
        self.assertIn("#SBATCH --job-name=chain", driver)
        self.assertIn("#SBATCH -N 1", driver)
        self.assertEqual(driver.count("cp \"$STAGE_DIR/CONTCAR\""), 2)
        self.assertLess(driver.index("../step_02"), driver.index("../step_03"))
        self.assertNotIn("vasp_std", driver)

    def test_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            render_chain_job_script("#SBATCH -J x\n", "x", stage_dirs=["."], stage_scripts=[])


class ResultParserTests(unittest.TestCase):
    def test_first_energy_and_convergence(self):
        self.assertEqual(parse_first_energy_from_outcar(OUTCAR_TWO_STEPS), -10.0)
        self.assertTrue(outcar_reached_required_accuracy(OUTCAR_TWO_STEPS))
        self.assertFalse(outcar_reached_required_accuracy("free  energy   TOTEN  = -1 eV\n"))

    def test_read_initial_energy_falls_back_to_oszicar(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "OSZICAR").write_text("   1 F= -0.1E+01 E0= -1\n   2 F= -0.2E+01 E0= -2\n")
            self.assertEqual(read_initial_energy(tmp), -1.0)


class RelaxModeTests(unittest.TestCase):
    def _pipeline(self, root: Path) -> MechanicalPipeline:
        inputs = write_basic_inputs(root)
        return MechanicalPipeline(
            inputs,
            PipelineConfig(workdir=root / "run", name="test"),
            incar_policy=FreeRelaxIncarPolicy(),
        )

    def test_single_step_uses_plain_job_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipe = self._pipeline(Path(tmp))
            calc = pipe.prepare_relax_chain(steps=1)
            self.assertEqual(calc.directory, Path(tmp) / "run" / "relax" / "step_01")
            self.assertIn("vasp_std", (calc.directory / "job.sh").read_text())
            self.assertFalse((calc.directory / ".vasptools_stage.sh").exists())
            self.assertIn("ISIF = 3", (calc.directory / "INCAR").read_text())

    def test_two_steps_create_driver_and_both_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipe = self._pipeline(Path(tmp))
            calc = pipe.relax.prepare(steps=2)
            relax_root = Path(tmp) / "run" / "relax"
            self.assertTrue((relax_root / "step_02" / "POSCAR").exists())
            driver = (calc.directory / "job.sh").read_text()
            self.assertIn("../step_02", driver)
            self.assertIn("cp \"$STAGE_DIR/CONTCAR\"", driver)
            self.assertIn("vasp_std", (calc.directory / ".vasptools_stage.sh").read_text())
            self.assertEqual(pipe.relax.collect()["status"], "missing_outputs")
            self.assertEqual(pipe.submit([calc], dry_run=True)[0].command, ("sbatch", "job.sh"))

    def test_driver_runs_from_slurm_spool_directory(self):
        """sbatch executes a copy of job.sh elsewhere; SLURM_SUBMIT_DIR must locate the run."""

        import os
        import shutil
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            # A typical cluster template: it cd's to $SLURM_SUBMIT_DIR itself.
            inputs.job_template.write_text(
                "#!/bin/bash\n#SBATCH --job-name={job_name}\ncd \"$SLURM_SUBMIT_DIR\"\n"
                "printf 'free  energy   TOTEN  = -1.0 eV\\n' > OUTCAR\ncp POSCAR CONTCAR\n",
                encoding="utf-8",
            )
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test"),
                incar_policy=FreeRelaxIncarPolicy(),
            )
            calc = pipe.relax.prepare(steps=2)

            spool = root / "spool" / "job123"
            spool.mkdir(parents=True)
            shutil.copy(calc.directory / "job.sh", spool / "slurm_script")
            env = {**os.environ, "SLURM_SUBMIT_DIR": str(calc.directory)}
            completed = subprocess.run(
                ["bash", str(spool / "slurm_script")],
                cwd=calc.directory,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            step_02 = pipe.relax.step_directory(2)
            self.assertTrue((step_02 / "OUTCAR").exists())
            self.assertTrue((step_02 / "CONTCAR").exists())  # step 2 really ran in its own folder
            self.assertEqual((step_02 / "POSCAR").read_text(), (calc.directory / "CONTCAR").read_text())

            # Without SLURM (plain `bash job.sh`) the script location is used.
            env.pop("SLURM_SUBMIT_DIR")
            for path in (calc.directory / "OUTCAR", calc.directory / "CONTCAR", step_02 / "OUTCAR", step_02 / "CONTCAR"):
                path.unlink()
            completed = subprocess.run(
                ["bash", str(calc.directory / "job.sh")], cwd=root, env=env, text=True, capture_output=True
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((step_02 / "OUTCAR").exists())

    def test_collect_reads_first_and_last_energies(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipe = self._pipeline(Path(tmp))
            pipe.relax.prepare(steps=2)
            step_01 = pipe.relax.step_directory(1)
            step_02 = pipe.relax.step_directory(2)
            (step_01 / "OUTCAR").write_text(OUTCAR_TWO_STEPS)
            (step_02 / "OUTCAR").write_text(OUTCAR_TWO_STEPS.replace("-10.5", "-11.0"))
            scaled = scale_structure_to_volume(load_poscar(step_01 / "POSCAR"), volume_factor=0.9)
            write_poscar(scaled, step_02 / "CONTCAR")

            result = pipe.collect_relax_chain()
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["converged"])
            self.assertEqual(result["steps_completed"], 2)
            self.assertEqual(result["energy_initial_eV"], -10.0)
            self.assertEqual(result["energy_final_eV"], -11.0)
            self.assertEqual(result["runtime_sec"], 200.0)
            self.assertEqual(result["final_structure_path"], str(step_02 / "CONTCAR"))

            (step_02 / "OUTCAR").unlink()
            self.assertEqual(pipe.relax.collect()["status"], "partial")

            # Step 2 running: no final block yet -> status running, energies from step 1 only
            (step_02 / "OUTCAR").write_text(OUTCAR_RUNNING)
            running = pipe.relax.collect()
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["steps_completed"], 1)
            self.assertEqual(running["steps_started"], 2)
            self.assertEqual(running["energy_final_eV"], -10.5)
            self.assertEqual(running["final_structure_path"], None)  # step 1 has no CONTCAR

    def test_potcar_hardlink_and_symlink_modes(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            for mode in ("hardlink", "symlink", "copy"):
                pipe = MechanicalPipeline(
                    inputs,
                    PipelineConfig(workdir=root / mode, potcar_mode=mode),
                    incar_policy=FreeRelaxIncarPolicy(),
                )
                calc = pipe.relax.prepare(steps=1)
                target = calc.directory / "POTCAR"
                self.assertEqual(target.read_text(), inputs.potcar.read_text())
                if mode == "symlink":
                    self.assertTrue(target.is_symlink())
                elif mode == "hardlink":
                    self.assertEqual(os.stat(target).st_ino, os.stat(inputs.potcar).st_ino)
                else:
                    self.assertFalse(target.is_symlink())
                    self.assertNotEqual(os.stat(target).st_ino, os.stat(inputs.potcar).st_ino)
            # Re-preparing over a symlinked POTCAR must not clobber the template.
            pipe = MechanicalPipeline(
                inputs, PipelineConfig(workdir=root / "symlink", potcar_mode="copy")
            )
            pipe.relax.prepare(steps=1)
            self.assertFalse((root / "symlink" / "relax" / "step_01" / "POTCAR").is_symlink())
            self.assertEqual(inputs.potcar.read_text(), "POTCAR placeholder\n")
            with self.assertRaises(ValueError):
                PipelineConfig(workdir=root, potcar_mode="move")

    def test_default_policy_still_forces_isif_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = write_basic_inputs(Path(tmp))
            pipe = MechanicalPipeline(inputs, PipelineConfig(workdir=Path(tmp) / "run"))
            self.assertIn("relax", pipe.modes)
            self.assertIsInstance(pipe.get_mode("relax"), RelaxMode)
            calc = pipe.relax.prepare()
            self.assertIn("ISIF = 2", (calc.directory / "INCAR").read_text())


class StructureHelperTests(unittest.TestCase):
    def test_find_molecular_fragments_and_count(self):
        structure = two_water_conformations()
        fragments = find_molecular_fragments(structure)
        self.assertEqual([members for members, _ in fragments], [(0, 1, 2), (3, 4, 5)])
        self.assertEqual(count_molecules(structure), 2)

    def test_find_molecular_fragments_rejects_frameworks(self):
        with self.assertRaises(ValueError):
            find_molecular_fragments(Structure(Lattice.cubic(1.5), ["C"], [[0, 0, 0]]))

    def test_describe_structure(self):
        description = describe_structure(two_water_conformations())
        self.assertEqual(description["n_molecules"], 2)
        self.assertEqual(description["n_atoms"], 6)
        self.assertEqual(description["space_group"], "Pm")  # planar molecules: mirror plane
        self.assertAlmostEqual(description["volume_A3"], 50.0**3)
        self.assertGreater(description["density_g_cm3"], 0.0)

    def test_iter_structure_files_accepts_poscar_like_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("27_POSCAR", "POSCAR", "CONTCAR_x", "b.cif", "a.vasp", "INCAR", ".hidden.cif"):
                (root / name).write_text("x")
            found = [path.name for path in iter_structure_files(root)]
            self.assertEqual(found, ["27_POSCAR", "CONTCAR_x", "POSCAR", "a.vasp", "b.cif"])


PROTOCOL_YAML = """
ENCUT: 600
PREC: Accurate
steps:
  - name: ions
    ISIF: 2
    NSW: 60
  - name: cell
    incar_file: INCAR_cell
    EDIFFG: -0.005
    LWAVE: false
"""


class RelaxProtocolTests(unittest.TestCase):
    def _write_protocol(self, root: Path, text: str = PROTOCOL_YAML) -> Path:
        (root / "INCAR_cell").write_text("ISIF = 3\nPOTIM = 0.3\nEDIFFG = -0.01\n", encoding="utf-8")
        path = root / "relax.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_load_protocol_merges_common_file_and_inline(self):
        with tempfile.TemporaryDirectory() as tmp:
            protocol = load_relax_protocol(self._write_protocol(Path(tmp)))
            self.assertEqual(protocol.n_steps, 2)
            self.assertEqual([step.name for step in protocol.steps], ["ions", "cell"])
            ions, cell = protocol.step_overrides()
            self.assertEqual(ions, {"ENCUT": 600, "PREC": "Accurate", "ISIF": 2, "NSW": 60})
            self.assertEqual(protocol.to_dict()["steps"][0], {"name": "ions", "ISIF": 2, "NSW": 60})
            self.assertEqual(cell["ISIF"], 3)
            self.assertEqual(cell["POTIM"], 0.3)
            self.assertEqual(cell["EDIFFG"], -0.005)  # inline wins over incar_file
            self.assertIs(cell["LWAVE"], False)
            self.assertEqual(cell["ENCUT"], 600)

    def test_load_protocol_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "steps: []\n": ValueError,
                "ENCUT: 600\n": ValueError,  # no steps
                "steps:\n  - incar: {ISIF: 2}\n": ValueError,  # nested mapping
                "steps:\n  - 5\n": ValueError,
                "steps:\n  - name: ''\n": ValueError,
                "steps:\n  - MAGMOM: [[1, 2]]\n": ValueError,
                "steps:\n  - incar_file: missing\n": FileNotFoundError,
            }
            for text, error in cases.items():
                path = root / "bad.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(error, msg=text):
                    load_relax_protocol(path)
            with self.assertRaises(ValueError):
                RelaxProtocol(steps=())

    def test_prepare_with_protocol_writes_per_step_incars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            protocol = load_relax_protocol(self._write_protocol(root))
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test"),
                incar_policy=FreeRelaxIncarPolicy(),
            )
            pipe.prepare_relax_chain(protocol=protocol)
            step_01 = (pipe.relax.step_directory(1) / "INCAR").read_text()
            step_02 = (pipe.relax.step_directory(2) / "INCAR").read_text()
            self.assertIn("ISIF = 2", step_01)
            self.assertIn("NSW = 60", step_01)
            self.assertIn("ENCUT = 600", step_01)
            self.assertIn("ISIF = 3", step_02)
            self.assertIn("EDIFFG = -0.005", step_02)
            self.assertIn("LWAVE = .FALSE.", step_02)
            self.assertIn("ENCUT = 600", step_02)
            self.assertEqual(pipe.relax.read_metadata(pipe.relax.step_directory(2))["step_name"], "cell")
            self.assertEqual(pipe.relax.collect()["step_names"], "ions|cell")

            with self.assertRaises(ValueError):
                RelaxMode(inputs=pipe.inputs, config=pipe.config, factory=pipe.factory).prepare(
                    steps=3, protocol=protocol
                )

    def test_prepare_with_raw_step_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run"),
                incar_policy=FreeRelaxIncarPolicy(),
            )
            pipe.relax.prepare(step_overrides=[{"ISIF": 2}, {"ISIF": 3}, {"ISIF": 3, "ENCUT": 700}])
            self.assertEqual(len(pipe.relax.prepared_steps()), 3)
            self.assertIn("ENCUT = 700", (pipe.relax.step_directory(3) / "INCAR").read_text())
            self.assertEqual(
                RelaxStep(name="x", incar_overrides={"ISIF": 2}).incar_overrides["ISIF"], 2
            )


class RelaxBatchScriptTests(unittest.TestCase):
    def test_dry_run_then_collect(self):
        import subprocess

        script = Path(__file__).resolve().parents[1] / "scripts" / "run_relax_batch.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "tmpl"
            template.mkdir()
            write_basic_inputs(template)
            structures = root / "structs"
            structures.mkdir()
            write_poscar(two_water_conformations(), structures / "10_POSCAR")
            csv_path = root / "out.csv"
            common = [
                sys.executable,
                str(script),
                "--structures-dir",
                str(structures),
                "--template-dir",
                str(template),
                "--output-csv",
                str(csv_path),
            ]
            subprocess.run(common + ["--relax-steps", "2", "--dry-run"], check=True, capture_output=True)
            run_root = template / "relax_runs" / "1000_10_POSCAR"
            self.assertTrue((run_root / "relax" / "step_02" / "job.sh").exists())

            step_01 = run_root / "relax" / "step_01"
            step_02 = run_root / "relax" / "step_02"
            (step_01 / "OUTCAR").write_text(OUTCAR_TWO_STEPS)
            (step_02 / "OUTCAR").write_text(OUTCAR_TWO_STEPS)
            write_poscar(
                scale_structure_to_volume(two_water_conformations(), volume_factor=0.5),
                step_02 / "CONTCAR",
            )
            subprocess.run(common + ["--collect-only"], check=True, capture_output=True)

            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["n_molecules_initial"], "2")
            self.assertEqual(row["n_molecules_final"], "2")
            self.assertEqual(row["energy_initial_eV"], "-10.000000")
            self.assertEqual(row["energy_final_eV"], "-10.500000")
            self.assertGreater(float(row["density_final_g_cm3"]), float(row["density_initial_g_cm3"]))
            self.assertTrue((template / "relax_runs" / "relaxed" / "1000_10_POSCAR.vasp").exists())
            self.assertTrue((template / "relax_runs" / "relaxed" / "1000_10_POSCAR.cif").exists())

    def test_protocol_flag_drives_step_count_and_incars(self):
        import subprocess

        script = Path(__file__).resolve().parents[1] / "scripts" / "run_relax_batch.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "tmpl"
            template.mkdir()
            write_basic_inputs(template)
            (template / "INCAR_cell").write_text("ISIF = 3\n", encoding="utf-8")
            (template / "relax.yaml").write_text(PROTOCOL_YAML, encoding="utf-8")
            structures = root / "structs"
            structures.mkdir()
            write_poscar(two_water_conformations(), structures / "a.vasp")
            common = [
                sys.executable,
                str(script),
                "--structures-dir",
                str(structures),
                "--template-dir",
                str(template),
                "--protocol",
                str(template / "relax.yaml"),
                "--output-csv",
                str(root / "out.csv"),
                "--dry-run",
            ]
            subprocess.run(common, check=True, capture_output=True)
            relax_root = template / "relax_runs" / "1000_a" / "relax"
            self.assertIn("ISIF = 2", (relax_root / "step_01" / "INCAR").read_text())
            self.assertIn("ISIF = 3", (relax_root / "step_02" / "INCAR").read_text())
            self.assertFalse((relax_root / "step_03").exists())
            with (root / "out.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.DictReader(handle))[0]["step_names"], "ions|cell")

            mismatch = subprocess.run(common + ["--relax-steps", "3"], capture_output=True, text=True)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("does not match", mismatch.stderr)

            # A dry-run folder is re-prepared; a submitted one is skipped.
            info_path = template / "relax_runs" / "1000_a" / "vasptools_run.json"
            info = json.loads(info_path.read_text())
            self.assertFalse(info["submitted"])
            info.update(submitted=True, job_id="4242")
            info_path.write_text(json.dumps(info))
            (relax_root / "step_01" / "INCAR").unlink()
            again = subprocess.run(common, check=True, capture_output=True, text=True)
            self.assertIn("already submitted (job 4242), skipping", again.stdout)
            self.assertFalse((relax_root / "step_01" / "INCAR").exists())
            with (root / "out.csv").open(newline="", encoding="utf-8") as handle:
                row = list(csv.DictReader(handle))[0]
            self.assertEqual(row["status"], "already_submitted")
            self.assertEqual(row["job_id"], "4242")


if __name__ == "__main__":
    unittest.main()
