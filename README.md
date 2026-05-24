# VaspTools

English | [Русский](README.ru.md)

`VaspTools` is a Python API for preparing, submitting, and post-processing
VASP mechanical-property workflows. It is designed for molecular crystals where
you want to calculate equation-of-state parameters, elastic constants, and
derived mechanical properties from a reproducible folder tree.

The project is intentionally API-first:

- no command-line client;
- no PyInstaller binary;
- no generated `KPOINTS`;
- no automatic replacement of your physical VASP settings except the workflow
  control tags described below.

## What It Calculates

The API has two built-in workflow branches.

`eos` branch:

- creates scaled-volume structures;
- prepares relaxation jobs;
- prepares static jobs from relaxation `CONTCAR` files;
- reads static energies and volumes;
- fits a third-order Birch-Murnaghan equation of state;
- returns and writes `E0`, `V0`, `B0`, `B0'`, and fit residuals.

`elastic` branch:

- creates strained structures for finite-strain stress calculations;
- prepares relaxation jobs;
- prepares static jobs from relaxation `CONTCAR` files;
- reads final stress tensors from `OUTCAR`;
- fits the full 6x6 elastic tensor `Cij`;
- calculates bulk modulus, shear modulus, Young's modulus, Poisson ratio,
  universal elastic anisotropy, linear compressibility, and a generic
  mechanical stability check.

## Repository Layout

```text
VaspTools/
  __init__.py             # Public lazy API: from VaspTools import ...
  core/
    pipeline.py           # MechanicalPipeline high-level coordinator
    factory.py            # VaspCalculationFactory
    models.py             # PipelineInputs, PipelineConfig, Calculation
    policies.py           # IncarPolicy
  workflows/
    base.py               # WorkflowMode extension point
    eos.py                # EOSMode
    elastic.py            # ElasticMode
  analysis/
    eos.py                # Birch-Murnaghan EOS fitting
    elastic.py            # Elastic tensor fitting and mechanical properties
  io/
    incar.py              # Lightweight INCAR parser/writer and stage rules
    jobs.py               # SLURM job script rendering and sbatch helpers
    results.py            # OUTCAR/OSZICAR energy and stress parsers
    discovery.py          # Find prepared calculations by metadata
  execution/
    runners.py            # CalculationRunner protocol and SbatchRunner
  structures/
    poscar.py             # POSCAR/CONTCAR parser and writer
    transforms.py         # Volume scaling and strain generation
  tests/
    test_core.py
```

Public imports stay stable. Prefer the high-level API:

```python
from VaspTools import MechanicalPipeline, PipelineConfig, PipelineInputs
```

Internal OOP extension imports are also available:

```python
from VaspTools.core import VaspCalculationFactory, IncarPolicy
from VaspTools.workflows import WorkflowMode, EOSMode, ElasticMode
from VaspTools.analysis import fit_eos, fit_elastic_tensor
from VaspTools.io import discover_calculations
from VaspTools.execution import SbatchRunner
```

## Installation

### Recommended: editable install

```bash
cd /path/to/VaspTools
python -m pip install -e .
```

### Dependencies

The package depends on:

- `numpy`
- `scipy`
- `pymatgen`

For a clean environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

If `pymatgen` is difficult to install with `pip`, use a conda environment:

```bash
conda create -n vasptools -c conda-forge python=3.12 numpy scipy pymatgen
conda activate vasptools
cd /path/to/VaspTools
python -m pip install -e .
```

## Required Input Directory

The recommended API entry point is `MechanicalPipeline.from_workdir(...)`.
Put the shared input files directly in one workflow directory:

```text
mechanics_run/
  POSCAR
  POTCAR
  INCAR
  job_template.sh
```

Input file meaning:

- `POSCAR`: initial relaxed crystal structure used as the reference structure;
- `POTCAR`: copied unchanged into every prepared calculation directory;
- `INCAR`: your template with physical parameters such as `ENCUT`, `KSPACING`,
  `IVDW`, `GGA`, `EDIFF`, etc.;
- `job_template.sh`: SLURM template used to create `job.sh` in each calculation
  directory.

`INCAR` must contain `KSPACING` by default. `VaspTools` never creates or copies
`KPOINTS`; all k-point control is expected to come from your `INCAR`.

Minimal `INCAR` example:

```text
ENCUT = 520
KSPACING = 0.25
EDIFF = 1E-7
IVDW = 12
PREC = Accurate
LREAL = Auto
```

Minimal `job_template.sh` example:

```bash
#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --time=24:00:00

module purge
module load vasp

srun vasp_std
```

Supported job-name placeholders:

- `{job_name}`
- `{JOB_NAME}`
- `{{JOB_NAME}}`
- `__JOB_NAME__`

If no placeholder exists, the template must already contain either
`#SBATCH --job-name=...` or `#SBATCH -J ...`; only that job-name line is changed.

## Automatic INCAR Rules

Your `INCAR` is the source of the physical settings. The API only adds or
overrides stage-control tags required by this workflow.

For every calculation:

```text
SYSTEM = generated calculation name
ISIF = 2
```

For static calculations:

```text
ISIF = 2
IBRION = -1
NSW = 0
ALGO = All
ISEARCH = 1
```

Important behavior:

- `KPOINTS` is never generated or copied;
- `KSPACING` is required unless `require_kspacing=False` is set explicitly;
- `ISIF` cannot be changed to anything other than `2` through overrides;
- target folders containing old VASP outputs such as `OUTCAR`, `OSZICAR`,
  `CONTCAR`, `vasprun.xml`, `XDATCAR`, `CHGCAR`, or `WAVECAR` are rejected to
  avoid mixing stale results with new inputs.

## Quick Start: EOS Only

Use this when you only need `E0`, `V0`, `B0`, and `B0'`.

```python
from VaspTools import MechanicalPipeline

pipe = MechanicalPipeline.from_workdir(
    "/path/to/mechanics_run",
    name="camphecene_nam",
    volume_factors=(0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06),
)

relax_jobs = pipe.prepare_relax(branch="eos")
pipe.submit(relax_jobs)
```

This creates:

```text
mechanics_run/
  eos/
    relax/
      V_0p9400/
      V_0p9600/
      V_0p9800/
      V_1p0000/
      V_1p0200/
      V_1p0400/
      V_1p0600/
```

Each directory contains:

```text
POSCAR
POTCAR
INCAR
job.sh
vasptools_metadata.json
```

After all relaxation jobs finish and each relaxation directory has a valid
`CONTCAR`, prepare and submit static calculations:

```python
static_jobs = pipe.prepare_static(branch="eos")
pipe.submit(static_jobs)
```

This creates:

```text
mechanics_run/eos/static/V_0p9400/
mechanics_run/eos/static/V_0p9600/
...
```

After all static jobs finish and `OUTCAR` or `OSZICAR` exists in every static
directory:

```python
results = pipe.fit(branch="eos")
eos = results["eos"]

print(eos.e0_eV)
print(eos.v0_ang3)
print(eos.b0_GPa)
print(eos.b0_prime)
```

EOS reports are written to:

```text
mechanics_run/reports/eos_points.csv
mechanics_run/reports/eos_fit.json
```

## Quick Start: EOS and Elastic

Use `branch="both"` when you want EOS and elastic branches in one workflow.

```python
from VaspTools import MechanicalPipeline

pipe = MechanicalPipeline.from_workdir(
    "/path/to/mechanics_run",
    name="camphecene_nam",
    volume_factors=(0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06),
    strain_amplitudes=(0.005, 0.01),
)

relax_jobs = pipe.prepare_relax(branch="both")
pipe.submit(relax_jobs)
```

For the example above:

- EOS relaxation jobs: `7`;
- elastic relaxation jobs: `6 components x 2 amplitudes x 2 signs = 24`;
- total relaxation jobs for `branch="both"`: `31`.

If you want only the 7 EOS jobs, use `branch="eos"`.

After the relaxation jobs finish:

```python
static_jobs = pipe.prepare_static(branch="both")
pipe.submit(static_jobs)
```

After the static jobs finish:

```python
results = pipe.fit(branch="both")

eos = results["eos"]
elastic_fit = results["elastic"]["fit"]
properties = results["elastic"]["properties"]

print(eos.b0_GPa)
print(elastic_fit.Cij_GPa)
print(properties["bulk_modulus_hill_GPa"])
print(properties["shear_modulus_hill_GPa"])
print(properties["young_modulus_GPa"])
print(properties["poisson_ratio"])
print(properties["universal_anisotropy_index"])
print(properties["linear_compressibility_a_1_per_GPa"])
print(properties["mechanical_stability"])
```

Elastic reports are written to:

```text
mechanics_run/reports/elastic_tensor.json
mechanics_run/reports/mechanical_properties.json
```

## API Reference

### MechanicalPipeline.from_workdir

```python
pipe = MechanicalPipeline.from_workdir(
    workdir,
    name="vasp_mechanics",
    volume_factors=(0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06),
    strain_amplitudes=(0.005, 0.01),
    job_script_name="job.sh",
    input_poscar="POSCAR",
    input_potcar="POTCAR",
    input_incar="INCAR",
    input_job_template="job_template.sh",
    require_kspacing=True,
    vasp_kbar_to_gpa=-0.1,
)
```

Parameters:

- `workdir`: root directory containing the shared input files;
- `name`: prefix used in generated SLURM job names;
- `volume_factors`: `V/V0` factors for EOS structures;
- `strain_amplitudes`: positive strain amplitudes used for elastic structures;
- `job_script_name`: generated script name inside each calculation directory;
- `input_poscar`, `input_potcar`, `input_incar`, `input_job_template`: custom
  names for the shared input files inside `workdir`;
- `require_kspacing`: require `KSPACING` in `INCAR`;
- `vasp_kbar_to_gpa`: conversion from VASP stress in kB to GPa. The default
  `-0.1` changes VASP's pressure-positive convention to tensile-positive stress.

### prepare_relax

```python
calculations = pipe.prepare_relax(
    branch="eos",              # "eos", "elastic", or "both"
    volume_factors=None,       # optional one-call EOS override
    strain_amplitudes=None,    # optional one-call elastic override
)
```

Returns a list of `Calculation` objects:

```python
for calc in calculations:
    print(calc.name)
    print(calc.stage)
    print(calc.directory)
    print(calc.job_name)
    print(calc.metadata)
```

Stages are:

- `eos_relax`
- `elastic_relax`

### prepare_static

```python
static_calculations = pipe.prepare_static(
    branch="both",
    allow_unrelaxed=False,
)
```

By default static calculations are created from relaxation `CONTCAR` files.
If a `CONTCAR` is missing or empty, the API raises `FileNotFoundError`.

Set `allow_unrelaxed=True` only for debugging or tests; then the relaxation
`POSCAR` is reused if `CONTCAR` is missing.

Stages are:

- `eos_static`
- `elastic_static`

### submit

```python
submissions = pipe.submit(calculations, dry_run=False)
```

By default this calls:

```bash
sbatch job.sh
```

inside every calculation directory.

Use `dry_run=True` to check what would be submitted without calling SLURM:

```python
submissions = pipe.submit(calculations, dry_run=True)

for submission in submissions:
    print(submission.workdir)
    print(submission.command)
    print(submission.dry_run)
```

`Submission` fields:

- `workdir`;
- `script`;
- `command`;
- `job_id`;
- `stdout`;
- `stderr`;
- `dry_run`.

### fit

```python
results = pipe.fit(branch="both")
```

For `branch="eos"`:

```python
eos = results["eos"]
```

`EOSFitResult` fields:

- `e0_eV`;
- `v0_ang3`;
- `b0_eV_ang3`;
- `b0_GPa`;
- `b0_prime`;
- `residual_rms_eV`;
- `n_points`.

For `branch="elastic"`:

```python
elastic = results["elastic"]
fit = elastic["fit"]
properties = elastic["properties"]
```

`ElasticFitResult` fields:

- `Cij_GPa`;
- `intercept_stress_GPa`;
- `residual_rms_GPa`;
- `n_points`.

Mechanical property keys:

- `bulk_modulus_voigt_GPa`;
- `bulk_modulus_reuss_GPa`;
- `bulk_modulus_hill_GPa`;
- `shear_modulus_voigt_GPa`;
- `shear_modulus_reuss_GPa`;
- `shear_modulus_hill_GPa`;
- `young_modulus_GPa`;
- `poisson_ratio`;
- `universal_anisotropy_index`;
- `linear_compressibility_a_1_per_GPa`;
- `linear_compressibility_b_1_per_GPa`;
- `linear_compressibility_c_1_per_GPa`;
- `mechanical_stability`.

## Building From Explicit Paths

Use this when your input files are not stored directly in `workdir`.

```python
from pathlib import Path
from VaspTools import MechanicalPipeline, PipelineConfig, PipelineInputs

inputs = PipelineInputs(
    poscar=Path("/path/to/POSCAR"),
    potcar=Path("/path/to/POTCAR"),
    incar=Path("/path/to/INCAR"),
    job_template=Path("/path/to/job_template.sh"),
)

config = PipelineConfig(
    workdir=Path("/path/to/mechanics_run"),
    name="camphecene_nam",
    volume_factors=(0.94, 0.96, 0.98, 1.0, 1.02, 1.04, 1.06),
    strain_amplitudes=(0.005, 0.01),
)

pipe = MechanicalPipeline(inputs, config)
```

## Lower-Level Mode API

The high-level methods delegate to mode objects:

```python
eos_relax = pipe.eos.prepare_relaxations()
eos_static = pipe.eos.prepare_statics()
eos_points = pipe.eos.collect_points()
eos_fit = pipe.eos.fit_from_statics()

elastic_relax = pipe.elastic.prepare_relaxations()
elastic_static = pipe.elastic.prepare_statics()
elastic_points = pipe.elastic.collect_points()
elastic_fit, properties = pipe.elastic.fit_from_statics()
```

Use this when you want direct control over one branch.

## Finding Prepared Calculations

Every prepared directory contains `vasptools_metadata.json`. You can discover
prepared calculations later:

```python
from VaspTools import discover_calculations

all_calcs = discover_calculations("/path/to/mechanics_run")
eos_static = discover_calculations(
    "/path/to/mechanics_run",
    branch="eos",
    stage="eos_static",
)

for calc in eos_static:
    print(calc.directory)
```

## Custom Runner

Any object with a `submit(calculations, dry_run=False)` method can replace the
default `SbatchRunner`.

```python
from VaspTools import MechanicalPipeline


class PrintRunner:
    def submit(self, calculations, *, dry_run=False):
        for calc in calculations:
            print(calc.directory)
        return []


pipe = MechanicalPipeline.from_workdir(
    "/path/to/mechanics_run",
    name="camphecene_nam",
    runner=PrintRunner(),
)
```

## Custom INCAR Policy

Subclass `IncarPolicy` if you want extra automatic tags while keeping the same
factory and workflow modes.

```python
from VaspTools import IncarPolicy, MechanicalPipeline


class MyIncarPolicy(IncarPolicy):
    def make_incar(self, template, *, system, stage, extra_overrides=None):
        incar = super().make_incar(
            template,
            system=system,
            stage=stage,
            extra_overrides=extra_overrides,
        )
        incar["LWAVE"] = False
        incar["LCHARG"] = False
        return incar


pipe = MechanicalPipeline.from_workdir(
    "/path/to/mechanics_run",
    name="camphecene_nam",
    incar_policy=MyIncarPolicy(),
)
```

## Custom Workflow Mode

New modes should subclass `WorkflowMode` and use `self.factory.prepare(...)`.

```python
from VaspTools import WorkflowMode
from VaspTools.structures import load_poscar


class StaticOnlyMode(WorkflowMode):
    branch = "static_only"

    def prepare(self):
        structure = load_poscar(self.inputs.poscar)
        return [
            self.factory.prepare(
                directory=self.root / "static",
                structure=structure,
                stage="custom_static",
                name="custom_static",
                metadata={"branch": self.branch},
            )
        ]


mode = StaticOnlyMode(inputs=pipe.inputs, config=pipe.config, factory=pipe.factory)
pipe.register_mode("static_only", mode)

calculations = pipe.get_mode("static_only").prepare()
pipe.submit(calculations)
```

Because `stage="custom_static"` ends with `_static`, static INCAR rules are
applied automatically.

## Direct Fitting Utilities

You can use EOS and elastic fitting without preparing VASP folders.

```python
from VaspTools import EOSPoint, fit_eos

points = [
    EOSPoint(volume=950.0, energy=-120.10),
    EOSPoint(volume=970.0, energy=-120.25),
    EOSPoint(volume=990.0, energy=-120.30),
    EOSPoint(volume=1010.0, energy=-120.27),
    EOSPoint(volume=1030.0, energy=-120.15),
]

fit = fit_eos(points)
print(fit.b0_GPa)
```

```python
from VaspTools import StrainStressPoint, fit_elastic_tensor, mechanical_properties

points = [
    StrainStressPoint(
        name="eps_xx_p0p01",
        strain=(0.01, 0, 0, 0, 0, 0),
        stress_GPa=(1.0, 0.3, 0.2, 0, 0, 0),
    ),
    # Add enough independent strain/stress points for a stable fit.
]

fit = fit_elastic_tensor(points)
properties = mechanical_properties(fit.Cij_GPa)
```

`fit_elastic_tensor` requires at least 6 strain/stress points. In practice,
use the generated `+/-` strains for all 6 Voigt components.

## Output Parsing

EOS:

- energy is read from `OUTCAR` final `TOTEN`;
- if `OUTCAR` is absent, energy is read from final `F=` in `OSZICAR`;
- volume is read from `CONTCAR`, falling back to `POSCAR`.

Elastic:

- stress is read from the final `in kB` stress line in `OUTCAR`;
- VASP order `xx yy zz xy yz zx` is converted to internal Voigt order
  `xx yy zz yz xz xy`;
- the default conversion factor is `-0.1` GPa per kB.

## Tests

```bash
cd /path/to/VaspTools
python -m unittest discover -s tests
```

## GitHub Notes

The repository is prepared with:

- `pyproject.toml` package metadata;
- MIT `LICENSE`;
- `.gitignore` for Python caches, build artifacts, local environments, and VASP
  generated outputs;
- GitHub Actions workflow for unit tests on Python 3.10, 3.11, and 3.12.

Generated files such as `__pycache__/`, `.pytest_cache/`, `build/`, `dist/`,
`*.egg-info/`, `OUTCAR`, `CONTCAR`, `vasprun.xml`, `WAVECAR`, `CHGCAR`, and
workflow output directories should not be committed.

## License

MIT. See [LICENSE](LICENSE).
