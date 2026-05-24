from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from pymatgen.core import Lattice, Structure


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VaspTools.analysis.elastic import (
    StrainStressPoint,
    fit_elastic_tensor,
    mechanical_properties,
)
from VaspTools.analysis.eos import EOSPoint, birch_murnaghan_energy, fit_eos
from VaspTools.core import MechanicalPipeline, PipelineConfig, PipelineInputs
from VaspTools.io.discovery import discover_calculations
from VaspTools.io.incar import Incar, make_stage_incar, validate_kspacing_incar
from VaspTools.io.jobs import parse_sbatch_job_id, render_job_script
from VaspTools.io.results import (
    parse_energy_from_oszicar,
    parse_energy_from_outcar,
    parse_stress_from_outcar,
)
from VaspTools.structures import (
    apply_strain,
    generate_strain_vectors,
    load_poscar,
    scale_structure_to_volume,
    strain_matrix_from_voigt,
    write_poscar,
)
from VaspTools.analysis import fit_eos as package_fit_eos
from VaspTools.core import PipelineConfig as PackagePipelineConfig
from VaspTools.execution import SbatchRunner as PackageSbatchRunner
from VaspTools.io import discover_calculations as package_discover_calculations
from VaspTools.workflows import EOSMode as PackageEOSMode
from VaspTools.workflows import WorkflowMode


def simple_structure() -> Structure:
    return Structure(Lattice.cubic(5.0), ["Si"], [[0, 0, 0]])


def write_basic_inputs(folder: Path) -> PipelineInputs:
    poscar = folder / "POSCAR"
    potcar = folder / "POTCAR"
    incar = folder / "INCAR"
    job = folder / "job_template.sh"

    write_poscar(simple_structure(), poscar)
    potcar.write_text("POTCAR placeholder\n", encoding="utf-8")
    incar.write_text(
        "\n".join(
            [
                "ENCUT = 520",
                "KSPACING = 0.25",
                "ISIF = 3",
                "ALGO = Fast",
                "ISEARCH = 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    job.write_text("#!/bin/sh\n#SBATCH --job-name={job_name}\nvasp_std\n", encoding="utf-8")
    return PipelineInputs(poscar=poscar, potcar=potcar, incar=incar, job_template=job)


class IncarTests(unittest.TestCase):
    def test_stage_incar_enforces_isif_and_static_tags(self):
        template = Incar({"ENCUT": 520, "KSPACING": 0.25, "ISIF": 3, "ALGO": "Fast"})

        relax = make_stage_incar(template, system="relax", stage="eos_relax")
        static = make_stage_incar(template, system="static", stage="eos_static")

        self.assertEqual(relax["ISIF"], 2)
        self.assertEqual(relax["ENCUT"], 520)
        self.assertEqual(relax["ALGO"], "Fast")
        self.assertEqual(static["ISIF"], 2)
        self.assertEqual(static["ALGO"], "All")
        self.assertEqual(static["ISEARCH"], 1)
        self.assertEqual(static["IBRION"], -1)
        self.assertEqual(static["NSW"], 0)

    def test_kspacing_is_required(self):
        with self.assertRaises(ValueError):
            validate_kspacing_incar(Incar({"ENCUT": 520}))

    def test_extra_overrides_cannot_change_isif(self):
        with self.assertRaises(ValueError):
            make_stage_incar(
                Incar({"KSPACING": 0.25}),
                system="bad",
                stage="static",
                extra_overrides={"ISIF": 3},
            )


class JobTests(unittest.TestCase):
    def test_render_job_script_replaces_placeholder(self):
        rendered = render_job_script("#SBATCH --job-name={job_name}\n", "my calc")
        self.assertEqual(rendered, "#SBATCH --job-name=my_calc\n")

    def test_render_job_script_replaces_existing_directive(self):
        rendered = render_job_script("#!/bin/sh\n#SBATCH -J old\nrun\n", "new")
        self.assertEqual(rendered, "#!/bin/sh\n#SBATCH -J new\nrun\n")

    def test_parse_sbatch_job_id(self):
        self.assertEqual(parse_sbatch_job_id("Submitted batch job 12345\n"), "12345")
        self.assertIsNone(parse_sbatch_job_id("no job id"))


class StructureTests(unittest.TestCase):
    def test_scale_structure_to_volume(self):
        structure = simple_structure()
        scaled = scale_structure_to_volume(structure, volume_factor=1.1)
        self.assertAlmostEqual(scaled.volume, structure.volume * 1.1)

    def test_strain_matrix_uses_engineering_shear(self):
        matrix = strain_matrix_from_voigt([1, 2, 3, 4, 5, 6])
        np.testing.assert_allclose(
            matrix,
            np.array([[1, 3, 2.5], [3, 2, 2], [2.5, 2, 3]], dtype=float),
        )

    def test_apply_strain_changes_lattice_volume(self):
        strained = apply_strain(simple_structure(), [0.01, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(strained.volume, simple_structure().volume * 1.01)

    def test_generate_strain_vectors(self):
        vectors = generate_strain_vectors((0.005, 0.01))
        self.assertEqual(len(vectors), 24)
        self.assertEqual(vectors[0][0], "eps_xx_m0p005")
        self.assertEqual(vectors[1][0], "eps_xx_p0p005")


class ResultParserTests(unittest.TestCase):
    def test_parse_energy_from_outcar_returns_last_toten(self):
        text = "free  energy   TOTEN  =      -1.0 eV\nfree  energy   TOTEN  = -2.5 eV\n"
        self.assertEqual(parse_energy_from_outcar(text), -2.5)

    def test_parse_energy_from_oszicar_returns_last_f(self):
        text = "1 F= -.100E+01 E0= -1\n2 F= -.250E+01 E0= -2.5\n"
        self.assertEqual(parse_energy_from_oszicar(text), -2.5)

    def test_parse_stress_from_outcar_maps_vasp_order(self):
        text = " in kB  1 2 3 4 5 6\n"
        stress = parse_stress_from_outcar(text)
        np.testing.assert_allclose(stress, [-0.1, -0.2, -0.3, -0.5, -0.6, -0.4])


class EOSTests(unittest.TestCase):
    def test_fit_eos_recovers_synthetic_parameters(self):
        volumes = np.linspace(95, 105, 9)
        energies = birch_murnaghan_energy(volumes, -10.0, 100.0, 0.08, 4.2)
        points = [EOSPoint(float(v), float(e)) for v, e in zip(volumes, energies)]

        fit = fit_eos(points)

        self.assertAlmostEqual(fit.e0_eV, -10.0, places=6)
        self.assertAlmostEqual(fit.v0_ang3, 100.0, places=5)
        self.assertAlmostEqual(fit.b0_eV_ang3, 0.08, places=5)
        self.assertAlmostEqual(fit.b0_prime, 4.2, places=4)


class ElasticTests(unittest.TestCase):
    def test_fit_elastic_tensor_and_properties(self):
        c = np.array(
            [
                [100, 35, 30, 0, 0, 0],
                [35, 95, 25, 0, 0, 0],
                [30, 25, 90, 0, 0, 0],
                [0, 0, 0, 25, 0, 0],
                [0, 0, 0, 0, 24, 0],
                [0, 0, 0, 0, 0, 23],
            ],
            dtype=float,
        )
        intercept = np.array([1, -2, 0.5, 0.1, 0.2, -0.3])
        points = []
        for name, strain in generate_strain_vectors((0.005, 0.01)):
            stress = c @ strain + intercept
            points.append(
                StrainStressPoint(
                    name=name,
                    strain=tuple(float(value) for value in strain),
                    stress_GPa=tuple(float(value) for value in stress),
                )
            )

        fit = fit_elastic_tensor(points)
        props = mechanical_properties(fit.Cij_GPa)

        np.testing.assert_allclose(np.array(fit.Cij_GPa), c, atol=1.0e-10)
        np.testing.assert_allclose(fit.intercept_stress_GPa, intercept, atol=1.0e-10)
        self.assertTrue(props["mechanical_stability"]["stable"])
        self.assertGreater(props["bulk_modulus_hill_GPa"], 0)
        self.assertGreater(props["young_modulus_GPa"], 0)


class PipelineTests(unittest.TestCase):
    def test_oop_package_imports_are_public(self):
        self.assertIs(package_fit_eos, fit_eos)
        self.assertIs(PackagePipelineConfig, PipelineConfig)
        self.assertEqual(PackageSbatchRunner.__name__, "SbatchRunner")
        self.assertIs(package_discover_calculations, discover_calculations)
        self.assertEqual(PackageEOSMode.__name__, "EOSMode")

    def test_pipeline_prepares_eos_and_static_inputs_without_kpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(
                    workdir=root / "run",
                    name="test",
                    volume_factors=(0.98, 1.0),
                    strain_amplitudes=(0.01,),
                ),
            )

            relax_calcs = pipe.prepare_eos_relaxations()
            self.assertEqual(len(relax_calcs), 2)
            for calc in relax_calcs:
                self.assertFalse((calc.directory / "KPOINTS").exists())
                incar = Incar.from_file(calc.directory / "INCAR")
                self.assertEqual(incar["ISIF"], 2)
                self.assertIn("KSPACING", incar)

            static_calcs = pipe.prepare_eos_statics(allow_unrelaxed=True)
            self.assertEqual(len(static_calcs), 2)
            for calc in static_calcs:
                incar = Incar.from_file(calc.directory / "INCAR")
                self.assertEqual(incar["ISIF"], 2)
                self.assertEqual(incar["ALGO"], "All")
                self.assertEqual(incar["ISEARCH"], 1)
                self.assertEqual(incar["IBRION"], -1)
                self.assertEqual(incar["NSW"], 0)
                self.assertFalse((calc.directory / "KPOINTS").exists())

    def test_pipeline_prepares_elastic_and_dry_run_submits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test", strain_amplitudes=(0.01,)),
            )

            calcs = pipe.prepare_elastic_relaxations()
            submissions = pipe.submit_calculations(calcs[:2], dry_run=True)

            self.assertEqual(len(calcs), 12)
            self.assertEqual(len(submissions), 2)
            self.assertTrue(all(submission.dry_run for submission in submissions))

    def test_pipeline_collects_eos_and_writes_fit_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(
                    workdir=root / "run",
                    name="test",
                    volume_factors=(0.96, 0.98, 1.0, 1.02, 1.04),
                ),
            )

            pipe.prepare_eos_relaxations()
            pipe.prepare_eos_statics(allow_unrelaxed=True)
            for directory in (root / "run" / "eos" / "static").iterdir():
                volume = load_poscar(directory / "POSCAR").volume
                energy = float(birch_murnaghan_energy(volume, -5.0, 125.0, 0.05, 4.0))
                (directory / "OUTCAR").write_text(
                    f"free  energy   TOTEN  = {energy:.12f} eV\n",
                    encoding="utf-8",
                )

            fit = pipe.fit_eos_from_statics()

            self.assertAlmostEqual(fit.v0_ang3, 125.0, places=4)
            self.assertTrue((root / "run" / "reports" / "eos_points.csv").exists())
            self.assertTrue((root / "run" / "reports" / "eos_fit.json").exists())

    def test_pipeline_collects_elastic_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test", strain_amplitudes=(0.01,)),
            )
            c = np.diag([90, 91, 92, 20, 21, 22]).astype(float)

            pipe.prepare_elastic_relaxations()
            pipe.prepare_elastic_statics(allow_unrelaxed=True)
            for directory in (root / "run" / "elastic" / "static").iterdir():
                metadata = pipe.factory.read_metadata(directory)
                strain = np.array(metadata["strain"], dtype=float)
                stress = c @ strain
                # OUTCAR order is xx yy zz xy yz zx, while internal Voigt is
                # xx yy zz yz xz xy. Divide by -0.1 to invert the parser.
                outcar_values = [stress[0], stress[1], stress[2], stress[5], stress[3], stress[4]]
                outcar_values = [value / -0.1 for value in outcar_values]
                (directory / "OUTCAR").write_text(
                    " in kB " + " ".join(f"{value:.12f}" for value in outcar_values) + "\n",
                    encoding="utf-8",
                )

            fit, props = pipe.fit_elastic_from_statics()

            np.testing.assert_allclose(np.array(fit.Cij_GPa), c, atol=1.0e-8)
            self.assertTrue(props["mechanical_stability"]["stable"])
            self.assertTrue((root / "run" / "reports" / "elastic_tensor.json").exists())
            self.assertTrue((root / "run" / "reports" / "mechanical_properties.json").exists())

    def test_pipeline_registers_custom_mode(self):
        class StaticOnlyMode(WorkflowMode):
            branch = "static_only"

            def prepare(self):
                return [
                    self.factory.prepare(
                        directory=self.root / "static",
                        structure=simple_structure(),
                        stage="custom_static",
                        name="custom_static",
                        metadata={"branch": self.branch},
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test"),
            )
            mode = StaticOnlyMode(inputs=pipe.inputs, config=pipe.config, factory=pipe.factory)

            pipe.register_mode("static_only", mode)
            calculations = pipe.get_mode("static_only").prepare()

            self.assertIs(pipe.get_mode("static_only"), mode)
            self.assertEqual(len(calculations), 1)
            self.assertEqual(calculations[0].metadata["branch"], "static_only")
            incar = Incar.from_file(calculations[0].directory / "INCAR")
            self.assertEqual(incar["ISIF"], 2)
            self.assertEqual(incar["ALGO"], "All")
            self.assertEqual(incar["ISEARCH"], 1)

    def test_factory_refuses_stale_vasp_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_basic_inputs(root)
            pipe = MechanicalPipeline(
                inputs,
                PipelineConfig(workdir=root / "run", name="test", volume_factors=(1.0,)),
            )

            pipe.prepare_eos_relaxations()
            target = root / "run" / "eos" / "relax" / "V_1p0000"
            (target / "OUTCAR").write_text("old output\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                pipe.prepare_eos_relaxations()

    def test_pipeline_from_workdir_and_high_level_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_basic_inputs(root)
            pipe = MechanicalPipeline.from_workdir(
                root,
                name="api_test",
                volume_factors=(0.98, 1.0),
                strain_amplitudes=(0.01,),
            )

            relax_calcs = pipe.prepare_relax(branch="eos")
            submissions = pipe.submit(relax_calcs, dry_run=True)

            calculations = discover_calculations(root, branch="eos")
            self.assertEqual(len(relax_calcs), 2)
            self.assertEqual(len(submissions), 2)
            self.assertEqual(len(calculations), 2)
            self.assertTrue(all(submission.dry_run for submission in submissions))


if __name__ == "__main__":
    unittest.main()
