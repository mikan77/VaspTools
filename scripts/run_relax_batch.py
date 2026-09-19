#!/usr/bin/env python3
"""Relax many structures (POSCAR or CIF) and summarize before/after properties.

Example:
  python run_relax_batch.py \
    --structures-dir /path/to/structures \
    --template-dir /path/to/template \
    --protocol /path/to/template/relax.yaml \
    --output /path/to/relax_summary.xlsx

  # after the jobs finished
  python run_relax_batch.py \
    --structures-dir /path/to/structures \
    --template-dir /path/to/template \
    --collect-only \
    --output /path/to/relax_summary.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PARENT = ROOT.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from VaspTools import FreeRelaxIncarPolicy, MechanicalPipeline, PipelineConfig, PipelineInputs
from VaspTools.io.jobs import resolve_job_template_path
from VaspTools.io.tables import write_table
from VaspTools.workflows.relax import RelaxProtocol, load_relax_protocol
from VaspTools.structures import load_poscar, write_poscar

from VaspTools.scripts._scan_utils import (
    describe_structure,
    iter_structure_files,
    load_structure,
    write_poscar_copy,
)


RUN_INFO_NAME = "vasptools_run.json"

CSV_COLUMNS = (
    "structure_file",
    "run_dir",
    "job_id",
    "status",
    "converged",
    "steps_completed",
    "step_names",
    "energy_initial_eV",
    "energy_final_eV",
    "delta_energy_eV",
    "n_atoms",
    "formula",
    "n_molecules_initial",
    "n_molecules_final",
    "space_group_initial",
    "space_group_final",
    "volume_initial_A3",
    "volume_final_A3",
    "density_initial_g_cm3",
    "density_final_g_cm3",
    "runtime_sec",
    "relaxed_path",
)


REQUIRED_SETTINGS = ("structures_dir", "template_dir", "output")


def parse_args(
    argv: Sequence[str] | None = None,
    *,
    config_defaults: Mapping[str, object] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Relax many structures on SLURM and build a before/after summary table."
    )
    parser.add_argument(
        "--structures-dir",
        help="Directory with POSCAR-like files (POSCAR, 27_POSCAR, *.vasp) and/or CIF files.",
    )
    parser.add_argument(
        "--template-dir",
        help="Directory with POTCAR, INCAR (ISIF/IBRION/NSW as you want them) and a .sh job template.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory for per-structure run folders. Defaults to <template-dir>/relax_runs.",
    )
    parser.add_argument(
        "--relaxed-dir",
        default=None,
        help="Directory to collect final relaxed structures. Defaults to <output-root>/relaxed.",
    )
    parser.add_argument(
        "--relaxed-format",
        choices=("vasp", "cif", "both"),
        default="both",
        help="Format of the collected relaxed structures (default: both).",
    )
    parser.add_argument(
        "--protocol",
        default=None,
        help=(
            "YAML file with per-step INCAR settings: a 'steps' list where each item holds "
            "INCAR tags plus optional 'name'/'incar_file'; top-level tags apply to all steps. "
            "Defines the number of steps; incar_file paths are relative to --template-dir."
        ),
    )
    parser.add_argument(
        "--relax-steps",
        type=int,
        default=None,
        help=(
            "Number of chained relaxations (CONTCAR -> POSCAR) inside one SLURM job. "
            "Default: number of steps in --protocol, otherwise 1."
        ),
    )
    parser.add_argument(
        "--index-start",
        type=int,
        default=1000,
        help="Starting numeric index for run folders (default: 1000).",
    )
    parser.add_argument(
        "--job-script-name",
        default="job.sh",
        help="Job script name inside each calculation directory (default: job.sh).",
    )
    parser.add_argument(
        "--symprec",
        type=float,
        default=0.05,
        help="Symmetry tolerance in Angstrom for space-group detection (default: 0.05).",
    )
    parser.add_argument(
        "--bond-tolerance",
        type=float,
        default=1.20,
        help="Covalent-radius bond tolerance factor for molecule counting (default: 1.20).",
    )
    parser.add_argument(
        "--potcar-mode",
        choices=("hardlink", "copy", "symlink"),
        default="hardlink",
        help=(
            "How POTCAR is placed in every step directory (default: hardlink = no extra "
            "disk space, falls back to copy across filesystems)."
        ),
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Do not prepare/submit; collect results from existing run folders.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare only; do not submit to sbatch.",
    )
    parser.add_argument(
        "--output",
        "--output-csv",
        dest="output",
        help="Summary table path; .xlsx writes Excel (needs openpyxl), any other suffix writes CSV.",
    )
    parser.add_argument(
        "--require-kspacing",
        action="store_true",
        default=True,
        help="Require KSPACING in INCAR (default).",
    )
    parser.add_argument(
        "--no-require-kspacing",
        action="store_false",
        dest="require_kspacing",
        help="Allow jobs without explicit KSPACING in INCAR.",
    )
    if config_defaults:
        # Values from vasptools.yaml act as defaults; explicit flags still win.
        parser.set_defaults(**dict(config_defaults))
    args = parser.parse_args(argv)
    missing = [name for name in REQUIRED_SETTINGS if getattr(args, name) in (None, "")]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        parser.error(f"missing {flags} (pass the flag or set it in vasptools.yaml)")
    return args


def write_summary(rows: list[dict[str, object]], output: Path) -> None:
    write_table(rows, CSV_COLUMNS, output, sheet="relaxations")


def make_pipeline(
    run_root: Path,
    *,
    template_dir: Path,
    job_template: Path,
    job_script_name: str,
    require_kspacing: bool,
    potcar_mode: str = "copy",
) -> MechanicalPipeline:
    inputs = PipelineInputs(
        poscar=run_root / "POSCAR",
        potcar=template_dir / "POTCAR",
        incar=template_dir / "INCAR",
        job_template=job_template,
    )
    config = PipelineConfig(
        workdir=run_root,
        name=run_root.name,
        job_script_name=job_script_name,
        require_kspacing=require_kspacing,
        potcar_mode=potcar_mode,
    )
    return MechanicalPipeline(
        inputs,
        config,
        incar_policy=FreeRelaxIncarPolicy(require_kspacing=require_kspacing),
    )


def prefixed(prefix: str, description: dict[str, object]) -> dict[str, object]:
    return {
        f"n_molecules_{prefix}": description["n_molecules"],
        f"space_group_{prefix}": description["space_group"],
        f"volume_{prefix}_A3": description["volume_A3"],
        f"density_{prefix}_g_cm3": description["density_g_cm3"],
    }


def save_relaxed_structure(structure, relaxed_dir: Path, run_name: str, fmt: str) -> Path:
    """Write the relaxed structure in the requested format(s); return the primary path."""

    relaxed_dir.mkdir(parents=True, exist_ok=True)
    primary: Path | None = None
    if fmt in {"vasp", "both"}:
        path = relaxed_dir / f"{run_name}.vasp"
        write_poscar(structure, path)
        primary = path
    if fmt in {"cif", "both"}:
        from pymatgen.io.cif import CifWriter

        path = relaxed_dir / f"{run_name}.cif"
        CifWriter(structure).write_file(str(path))
        primary = primary or path
    assert primary is not None
    return primary


def collect_run(
    run_root: Path,
    *,
    template_dir: Path,
    job_template: Path,
    args: argparse.Namespace,
    relaxed_dir: Path,
) -> dict[str, object]:
    info = json.loads((run_root / RUN_INFO_NAME).read_text(encoding="utf-8"))
    row: dict[str, object] = {
        "structure_file": info.get("source_file", ""),
        "run_dir": str(run_root),
        "job_id": info.get("job_id"),
    }

    initial = load_poscar(run_root / "POSCAR")
    initial_description = describe_structure(
        initial,
        symprec=args.symprec,
        bond_tolerance_factor=args.bond_tolerance,
    )
    row["n_atoms"] = initial_description["n_atoms"]
    row["formula"] = initial_description["formula"]
    row.update(prefixed("initial", initial_description))

    pipeline = make_pipeline(
        run_root,
        template_dir=template_dir,
        job_template=job_template,
        job_script_name=args.job_script_name,
        require_kspacing=args.require_kspacing,
    )
    result = pipeline.relax.collect()
    row["status"] = result["status"]
    row["converged"] = result["converged"]
    row["steps_completed"] = result["steps_completed"]
    row["step_names"] = result["step_names"]
    row["energy_initial_eV"] = result["energy_initial_eV"]
    row["energy_final_eV"] = result["energy_final_eV"]
    row["runtime_sec"] = result["runtime_sec"]
    if result["energy_initial_eV"] is not None and result["energy_final_eV"] is not None:
        row["delta_energy_eV"] = float(result["energy_final_eV"]) - float(result["energy_initial_eV"])

    final_path = result.get("final_structure_path")
    if final_path:
        final = load_poscar(final_path)
        final_description = describe_structure(
            final,
            symprec=args.symprec,
            bond_tolerance_factor=args.bond_tolerance,
        )
        row.update(prefixed("final", final_description))
        # Only fully finished chains go to the relaxed-structures folder;
        # partial runs keep their metrics in the table but are not exported.
        if result["status"] == "completed":
            row["relaxed_path"] = str(
                save_relaxed_structure(final, relaxed_dir, run_root.name, args.relaxed_format)
            )
    return row


def main(
    argv: Sequence[str] | None = None,
    *,
    config_defaults: Mapping[str, object] | None = None,
) -> None:
    args = parse_args(argv, config_defaults=config_defaults)
    structures_dir = Path(args.structures_dir).resolve()
    template_dir = Path(args.template_dir).resolve()

    protocol: RelaxProtocol | None = None
    if args.protocol:
        protocol = load_relax_protocol(Path(args.protocol).resolve(), template_dir=template_dir)
        if args.relax_steps is not None and args.relax_steps != protocol.n_steps:
            raise SystemExit(
                f"Error: --relax-steps {args.relax_steps} does not match the {protocol.n_steps} "
                f"step(s) defined in {args.protocol}. Drop --relax-steps or edit the protocol."
            )
        relax_steps = protocol.n_steps
    else:
        relax_steps = 1 if args.relax_steps is None else args.relax_steps
    if relax_steps < 1:
        raise SystemExit("Error: --relax-steps must be >= 1.")
    step_names = [step.name for step in protocol.steps] if protocol else [f"step{i}" for i in range(1, relax_steps + 1)]
    output_root = (
        Path(args.output_root).resolve() if args.output_root else template_dir / "relax_runs"
    )
    relaxed_dir = Path(args.relaxed_dir).resolve() if args.relaxed_dir else output_root / "relaxed"
    output = Path(args.output).resolve()
    job_template = resolve_job_template_path(template_dir)

    rows: list[dict[str, object]] = []

    if args.collect_only:
        run_roots = sorted(
            path.parent for path in output_root.glob(f"*/{RUN_INFO_NAME}") if path.parent.is_dir()
        )
        if not run_roots:
            raise SystemExit(f"No prepared runs ({RUN_INFO_NAME}) found under {output_root}.")
        for run_root in run_roots:
            rows.append(
                collect_run(
                    run_root,
                    template_dir=template_dir,
                    job_template=job_template,
                    args=args,
                    relaxed_dir=relaxed_dir,
                )
            )
        write_summary(rows, output)
        completed = sum(1 for row in rows if row.get("status") == "completed")
        print(f"Collected {len(rows)} runs ({completed} completed); wrote {output}")
        print(f"Relaxed structures: {relaxed_dir}")
        return

    structure_files = iter_structure_files(structures_dir)
    if not structure_files:
        raise FileNotFoundError(f"No supported structure files in {structures_dir}")
    output_root.mkdir(parents=True, exist_ok=True)

    skipped = 0
    failed = 0
    for offset, source in enumerate(structure_files, start=args.index_start):
        run_name = f"{offset:04d}_{source.stem}"
        run_root = output_root / run_name
        info_path = run_root / RUN_INFO_NAME

        # Resumable: a folder that was already submitted is left alone, so the
        # script can be re-run after adding structures without double-submitting.
        if info_path.exists():
            previous = json.loads(info_path.read_text(encoding="utf-8"))
            if previous.get("submitted"):
                skipped += 1
                rows.append(
                    {
                        "structure_file": source.name,
                        "run_dir": str(run_root),
                        "job_id": previous.get("job_id"),
                        "status": "already_submitted",
                        "step_names": "|".join(step_names),
                    }
                )
                print(f"{run_name}: already submitted (job {previous.get('job_id') or '?'}), skipping")
                continue

        run_root.mkdir(parents=True, exist_ok=True)
        structure = load_structure(source)
        write_poscar_copy(structure, run_root / "POSCAR")
        info = {
            "source_file": source.name,
            "source_path": str(source),
            "run_name": run_name,
            "relax_steps": relax_steps,
            "protocol_path": str(Path(args.protocol).resolve()) if args.protocol else None,
            "protocol": protocol.to_dict() if protocol else None,
            "submitted": False,
            "job_id": None,
        }
        info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

        pipeline = make_pipeline(
            run_root,
            template_dir=template_dir,
            job_template=job_template,
            job_script_name=args.job_script_name,
            require_kspacing=args.require_kspacing,
            potcar_mode=args.potcar_mode,
        )
        calculation = pipeline.relax.prepare(steps=relax_steps, protocol=protocol)

        job_id = None
        status = "prepared"
        if not args.dry_run:
            try:
                submissions = pipeline.submit([calculation])
                job_id = submissions[0].job_id if submissions else None
                status = "submitted"
                info.update(submitted=True, job_id=job_id)
                info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
            except RuntimeError as exc:
                # sbatch failed (quota, bad directive, ...): record it and go on
                # with the remaining structures instead of aborting the batch.
                failed += 1
                status = "submit_failed"
                print(f"{run_name}: sbatch failed: {exc}", file=sys.stderr)

        description = describe_structure(
            structure,
            symprec=args.symprec,
            bond_tolerance_factor=args.bond_tolerance,
        )
        row: dict[str, object] = {
            "structure_file": source.name,
            "run_dir": str(run_root),
            "job_id": job_id,
            "status": status,
            "steps_completed": 0,
            "step_names": "|".join(step_names),
            "n_atoms": description["n_atoms"],
            "formula": description["formula"],
        }
        row.update(prefixed("initial", description))
        rows.append(row)
        if status != "submit_failed":
            label = "dry-run" if args.dry_run else f"job {job_id or '?'}"
            print(f"{run_name}: {relax_steps} step(s) [{', '.join(step_names)}], {label}")

    write_summary(rows, output)
    prepared = len(structure_files) - skipped
    summary = f"Prepared {prepared} structures"
    if skipped:
        summary += f", skipped {skipped} already submitted"
    if failed:
        summary += f", {failed} submission(s) FAILED"
    print(f"{summary}; wrote {len(rows)} rows to {output}")
    if not args.dry_run:
        print("Run again with --collect-only after the jobs finish.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
