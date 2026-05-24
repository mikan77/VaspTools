# VaspTools

[English](README.md) | Русский

`VaspTools` - это Python API для подготовки, запуска и постобработки VASP
workflow для расчета механических свойств. Основной сценарий - молекулярные
кристаллы, где нужно воспроизводимо получить EOS-параметры, elastic tensor и
производные механические характеристики.

Проект специально сделан как API, а не как CLI:

- нет command-line клиента;
- нет PyInstaller-бинарника;
- `KPOINTS` не создается;
- физические параметры из твоего `INCAR` не заменяются автоматически, кроме
  обязательных workflow-тегов, перечисленных ниже.

## Что Считает

Встроены две ветки workflow.

Ветка `eos`:

- создает структуры с разными объемами;
- готовит relaxation jobs;
- готовит static jobs из relaxation `CONTCAR`;
- читает энергии и объемы из static расчетов;
- фитирует third-order Birch-Murnaghan equation of state;
- возвращает и записывает `E0`, `V0`, `B0`, `B0'` и residual fit.

Ветка `elastic`:

- создает структуры с малыми деформациями;
- готовит relaxation jobs;
- готовит static jobs из relaxation `CONTCAR`;
- читает финальные stress tensors из `OUTCAR`;
- фитирует полный elastic tensor `Cij` 6x6;
- считает bulk modulus, shear modulus, Young's modulus, Poisson ratio,
  elastic anisotropy, linear compressibility и generic mechanical stability
  check.

## Структура Репозитория

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
    discovery.py          # Поиск подготовленных расчетов по metadata
  execution/
    runners.py            # CalculationRunner protocol and SbatchRunner
  structures/
    poscar.py             # POSCAR/CONTCAR parser and writer
    transforms.py         # Volume scaling and strain generation
  tests/
    test_core.py
```

Публичные импорты остались стабильными. Для обычного использования лучше:

```python
from VaspTools import MechanicalPipeline, PipelineConfig, PipelineInputs
```

Для расширения ООП-архитектуры можно импортировать внутренние слои напрямую:

```python
from VaspTools.core import VaspCalculationFactory, IncarPolicy
from VaspTools.workflows import WorkflowMode, EOSMode, ElasticMode
from VaspTools.analysis import fit_eos, fit_elastic_tensor
from VaspTools.io import discover_calculations
from VaspTools.execution import SbatchRunner
```

## Установка

### Рекомендуемый Вариант: editable install

```bash
cd /path/to/VaspTools
python -m pip install -e .
```

### Зависимости

Пакет зависит от:

- `numpy`
- `scipy`
- `pymatgen`

Чистое окружение через `venv`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Если `pymatgen` плохо ставится через `pip`, проще использовать conda:

```bash
conda create -n vasptools -c conda-forge python=3.12 numpy scipy pymatgen
conda activate vasptools
cd /path/to/VaspTools
python -m pip install -e .
```

## Входная Папка

Рекомендуемая точка входа API - `MechanicalPipeline.from_workdir(...)`.
Положи общие input-файлы прямо в одну workflow-папку:

```text
mechanics_run/
  POSCAR
  POTCAR
  INCAR
  job_template.sh
```

Назначение файлов:

- `POSCAR`: исходная relaxed структура, от которой строятся EOS и elastic
  структуры;
- `POTCAR`: копируется без изменений в каждую папку расчета;
- `INCAR`: твой шаблон с физическими параметрами: `ENCUT`, `KSPACING`, `IVDW`,
  `GGA`, `EDIFF` и т.д.;
- `job_template.sh`: SLURM template, из которого создается `job.sh` для каждой
  папки расчета.

По умолчанию `INCAR` обязан содержать `KSPACING`. `VaspTools` никогда не
создает и не копирует `KPOINTS`; управление k-point mesh должно идти из твоего
`INCAR`.

Минимальный пример `INCAR`:

```text
ENCUT = 520
KSPACING = 0.25
EDIFF = 1E-7
IVDW = 12
PREC = Accurate
LREAL = Auto
```

Минимальный пример `job_template.sh`:

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

Поддерживаемые placeholders для имени job:

- `{job_name}`
- `{JOB_NAME}`
- `{{JOB_NAME}}`
- `__JOB_NAME__`

Если placeholder нет, в template уже должна быть строка `#SBATCH --job-name=...`
или `#SBATCH -J ...`; API изменит только имя job.

## Автоматические INCAR Правила

Твой `INCAR` остается источником физических настроек. API добавляет или
переопределяет только служебные теги, необходимые для workflow.

Для всех расчетов:

```text
SYSTEM = generated calculation name
ISIF = 2
```

Для static calculations:

```text
ISIF = 2
IBRION = -1
NSW = 0
ALGO = All
ISEARCH = 1
```

Важное поведение:

- `KPOINTS` никогда не создается и не копируется;
- `KSPACING` обязателен, если явно не поставить `require_kspacing=False`;
- `ISIF` нельзя изменить на значение, отличное от `2`, через overrides;
- если целевая папка уже содержит старые VASP outputs (`OUTCAR`, `OSZICAR`,
  `CONTCAR`, `vasprun.xml`, `XDATCAR`, `CHGCAR`, `WAVECAR`), подготовка
  останавливается, чтобы не смешать старые результаты с новыми input-файлами.

## Быстрый Старт: Только EOS

Используй это, если нужны только `E0`, `V0`, `B0` и `B0'`.

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

Будут созданы папки:

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

В каждой папке будет:

```text
POSCAR
POTCAR
INCAR
job.sh
vasptools_metadata.json
```

После завершения relax jobs, когда в каждой relax-папке появился корректный
`CONTCAR`, подготовь и отправь static calculations:

```python
static_jobs = pipe.prepare_static(branch="eos")
pipe.submit(static_jobs)
```

Это создаст:

```text
mechanics_run/eos/static/V_0p9400/
mechanics_run/eos/static/V_0p9600/
...
```

После завершения static jobs, когда в каждой static-папке есть `OUTCAR` или
`OSZICAR`:

```python
results = pipe.fit(branch="eos")
eos = results["eos"]

print(eos.e0_eV)
print(eos.v0_ang3)
print(eos.b0_GPa)
print(eos.b0_prime)
```

EOS reports записываются сюда:

```text
mechanics_run/reports/eos_points.csv
mechanics_run/reports/eos_fit.json
```

## Быстрый Старт: EOS + Elastic

Используй `branch="both"`, если нужно подготовить обе ветки.

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

Для этого примера:

- EOS relax jobs: `7`;
- elastic relax jobs: `6 components x 2 amplitudes x 2 signs = 24`;
- всего relax jobs при `branch="both"`: `31`.

Если нужны только 7 EOS calculations, используй `branch="eos"`.

После завершения relaxation jobs:

```python
static_jobs = pipe.prepare_static(branch="both")
pipe.submit(static_jobs)
```

После завершения static jobs:

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

Elastic reports записываются сюда:

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

Параметры:

- `workdir`: корневая папка с общими input-файлами;
- `name`: prefix для generated SLURM job names;
- `volume_factors`: множители `V/V0` для EOS structures;
- `strain_amplitudes`: положительные amplitudes для elastic structures;
- `job_script_name`: имя generated script в каждой папке расчета;
- `input_poscar`, `input_potcar`, `input_incar`, `input_job_template`: кастомные
  имена input-файлов внутри `workdir`;
- `require_kspacing`: требовать `KSPACING` в `INCAR`;
- `vasp_kbar_to_gpa`: перевод VASP stress из kB в GPa. По умолчанию `-0.1`,
  что переводит VASP pressure-positive convention в tensile-positive stress.

### prepare_relax

```python
calculations = pipe.prepare_relax(
    branch="eos",              # "eos", "elastic" или "both"
    volume_factors=None,       # optional one-call EOS override
    strain_amplitudes=None,    # optional one-call elastic override
)
```

Возвращает список `Calculation`:

```python
for calc in calculations:
    print(calc.name)
    print(calc.stage)
    print(calc.directory)
    print(calc.job_name)
    print(calc.metadata)
```

Stages:

- `eos_relax`
- `elastic_relax`

### prepare_static

```python
static_calculations = pipe.prepare_static(
    branch="both",
    allow_unrelaxed=False,
)
```

По умолчанию static calculations создаются из relaxation `CONTCAR`. Если
`CONTCAR` отсутствует или пустой, API выбрасывает `FileNotFoundError`.

`allow_unrelaxed=True` стоит использовать только для debug/tests; тогда при
отсутствии `CONTCAR` будет использован relaxation `POSCAR`.

Stages:

- `eos_static`
- `elastic_static`

### submit

```python
submissions = pipe.submit(calculations, dry_run=False)
```

По умолчанию вызывается:

```bash
sbatch job.sh
```

в каждой папке расчета.

Для проверки без запуска SLURM:

```python
submissions = pipe.submit(calculations, dry_run=True)

for submission in submissions:
    print(submission.workdir)
    print(submission.command)
    print(submission.dry_run)
```

Поля `Submission`:

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

Для `branch="eos"`:

```python
eos = results["eos"]
```

Поля `EOSFitResult`:

- `e0_eV`;
- `v0_ang3`;
- `b0_eV_ang3`;
- `b0_GPa`;
- `b0_prime`;
- `residual_rms_eV`;
- `n_points`.

Для `branch="elastic"`:

```python
elastic = results["elastic"]
fit = elastic["fit"]
properties = elastic["properties"]
```

Поля `ElasticFitResult`:

- `Cij_GPa`;
- `intercept_stress_GPa`;
- `residual_rms_GPa`;
- `n_points`.

Ключи `properties`:

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

## Создание Pipeline Из Явных Путей

Используй это, если input-файлы не лежат прямо внутри `workdir`.

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

High-level методы делегируют работу mode objects:

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

Это удобно, если нужен прямой контроль над одной веткой.

## Поиск Подготовленных Расчетов

Каждая подготовленная папка содержит `vasptools_metadata.json`. Позже можно
найти расчеты так:

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

Любой объект с методом `submit(calculations, dry_run=False)` может заменить
стандартный `SbatchRunner`.

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

Если нужны дополнительные автоматические INCAR tags, можно унаследоваться от
`IncarPolicy`.

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

Новые режимы нужно делать через subclass `WorkflowMode` и
`self.factory.prepare(...)`.

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

Так как `stage="custom_static"` заканчивается на `_static`, static INCAR rules
применяются автоматически.

## Direct Fitting Utilities

EOS и elastic fitting можно использовать отдельно, без подготовки VASP folders.

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
    # Добавь достаточно независимых strain/stress points для стабильного fit.
]

fit = fit_elastic_tensor(points)
properties = mechanical_properties(fit.Cij_GPa)
```

`fit_elastic_tensor` требует минимум 6 strain/stress points. На практике лучше
использовать generated `+/-` strains для всех 6 Voigt components.

## Парсинг Результатов

EOS:

- energy читается из финального `TOTEN` в `OUTCAR`;
- если `OUTCAR` нет, energy читается из финального `F=` в `OSZICAR`;
- volume читается из `CONTCAR`, при отсутствии - из `POSCAR`.

Elastic:

- stress читается из финальной строки `in kB` в `OUTCAR`;
- VASP order `xx yy zz xy yz zx` переводится во внутренний Voigt order
  `xx yy zz yz xz xy`;
- default conversion factor: `-0.1` GPa per kB.

## Тесты

```bash
cd /path/to/VaspTools
python -m unittest discover -s tests
```

## Подготовка Для GitHub

В репозитории есть:

- `pyproject.toml` с package metadata;
- MIT `LICENSE`;
- `.gitignore` для Python cache, build artifacts, local environments и VASP
  generated outputs;
- GitHub Actions workflow для unit tests на Python 3.10, 3.11 и 3.12.

Не коммить generated files: `__pycache__/`, `.pytest_cache/`, `build/`,
`dist/`, `*.egg-info/`, `OUTCAR`, `CONTCAR`, `vasprun.xml`, `WAVECAR`,
`CHGCAR` и рабочие папки расчетов.

## Лицензия

MIT. См. [LICENSE](LICENSE).
