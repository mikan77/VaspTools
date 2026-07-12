#!/usr/bin/env python3
"""Run EOS or elastic workflow for many structures (POSCAR or CIF)."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PARENT = ROOT.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from VaspTools.io.jobs import resolve_job_template_path
from VaspTools import PipelineInputs

from VaspTools.scripts._scan_utils import (
    aggregate_runtime_for_static_and_relax,
    iter_structure_files,
    load_structure,
    make_workdir_pipeline,
    read_result_row,
    write_poscar_copy,
)


def parse_floats(raw: str) -> tuple[float, ...]:
    values = [float(value.strip()) for value in raw.split(",") if value.strip()]
    if not values:
        raise ValueError(f"Empty numeric list from '{raw}'")
    return tuple(values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one workflow for many structures.")
    parser.add_argument(
        "--structures-dir",
        required=True,
        help="Directory with POSCAR/CIF files.",
    )
    parser.add_argument(
        "--template-dir",
        required=True,
        help="Directory with POTCAR and INCAR (and optional .sh job template).",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory to store per-structure run folders. Defaults to <template-dir>/multi_runs.",
    )
    parser.add_argument(
        "--mode",
        choices=("eos", "elastic", "both"),
        default="eos",
        help="Workflow mode to run (default: eos).",
    )
    parser.add_argument(
        "--index-start",
        type=int,
        default=1000,
        help="Starting numeric index for run folders.",
    )
    parser.add_argument(
        "--volume-factors",
        default="0.94,0.96,0.98,1.0,1.02,1.04,1.06",
        help="Comma-separated EOS scale factors.",
    )
    parser.add_argument(
        "--strain-amplitudes",
        default="0.005,0.01",
        help="Comma-separated elastic strain amplitudes.",
    )
    parser.add_argument(
        "--job-script-name",
        default="job.sh",
        help="Job script name inside each calculation directory.",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Do not prepare/submit; only collect existing results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare only; do not submit to sbatch.",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help="Path to output CSV summary.",
    )
    parser.add_argument(
        "--require-kspacing",
        action="store_true",
        default=True,
        help="Require KSPACING in INCAR (default true).",
    )
    parser.add_argument(
        "--no-require-kspacing",
        action="store_false",
        dest="require_kspacing",
        help="Allow jobs without explicit KSPACING in INCAR.",
    )
    return parser.parse_args()


def write_csv(rows: list[dict[str, object]], output_csv: Path) -> None:
    if not rows:
        output_csv.write_text("structure_file,branch,status\n", encoding="utf-8")
        return

    header: list[str] = [
        "structure_file",
        "branch",
        "path",
        "status",
        "runtime_sec",
        "initial_volume",
        "final_volume",
        "final_energy",
        "stage",
    ]
    extra: set[str] = set()
    for row in rows:
        for key in row:
            if key not in header:
                extra.add(key)
    header.extend(sorted(extra))

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})


def collect_rows_for_branch(workdir: Path, structure_name: str, branch: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    run_poscar_volume: float | None = None
    run_root_poscar = workdir / "POSCAR"
    if run_root_poscar.exists():
        try:
            run_poscar_volume = float(load_structure(run_root_poscar).volume)
        except Exception:
            run_poscar_volume = None

    static_root = workdir / branch / "static"
    if not static_root.exists():
        return rows

    relax_root = workdir / branch / "relax"
    for static_dir in sorted(static_dir for static_dir in static_root.iterdir() if static_dir.is_dir()):
        row = read_result_row(
            static_dir,
            structure_name=structure_name,
            branch=branch,
            fallback_initial_volume=run_poscar_volume,
        )
        relax_dir = relax_root / static_dir.name if relax_root.exists() else None
        row["runtime_sec"] = aggregate_runtime_for_static_and_relax(
            static_dir=static_dir,
            relax_dir=relax_dir,
        )
        # Add a clear branch label per point
        row["branch"] = branch
        rows.append(row)
    return rows


def main() -> None:
    args = parse_args()
    structures_dir = Path(args.structures_dir).resolve()
    template_dir = Path(args.template_dir).resolve()
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else template_dir / "multi_runs"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    structure_files = iter_structure_files(structures_dir)
    if not structure_files:
        raise FileNotFoundError(f"No supported structure files in {structures_dir}")

    shared_inputs = PipelineInputs(
        poscar=template_dir / "POSCAR",  # placeholder, replaced per structure
        potcar=template_dir / "POTCAR",
        incar=template_dir / "INCAR",
        job_template=resolve_job_template_path(template_dir),
    )

    volume_factors = parse_floats(args.volume_factors)
    strain_amplitudes = parse_floats(args.strain_amplitudes)

    rows: list[dict[str, object]] = []

    for offset, source in enumerate(structure_files, start=args.index_start):
        run_name = f"{offset:04d}_{source.stem}"
        run_root = output_root / run_name

        if args.collect_only:
            if args.mode in {"eos", "both"}:
                rows.extend(collect_rows_for_branch(run_root, source.name, "eos"))
            if args.mode in {"elastic", "both"}:
                rows.extend(collect_rows_for_branch(run_root, source.name, "elastic"))
            continue

        structure = load_structure(source)

        run_root.mkdir(parents=True, exist_ok=True)
        poscar_path = run_root / "POSCAR"
        write_poscar_copy(structure, poscar_path)
        shared_inputs_for_run = PipelineInputs(
            poscar=poscar_path,
            potcar=shared_inputs.potcar,
            incar=shared_inputs.incar,
            job_template=shared_inputs.job_template,
        )

        pipeline = make_workdir_pipeline(
            structure_poscar=poscar_path,
            workdir=run_root,
            shared_inputs=shared_inputs_for_run,
            name=run_name,
            volume_factors=volume_factors,
            strain_amplitudes=strain_amplitudes,
            job_script_name=args.job_script_name,
            require_kspacing=args.require_kspacing,
        )

        if args.mode == "eos":
            planned_calcs = pipeline.prepare_eos_relaxations()
            pipeline.submit(planned_calcs, dry_run=args.dry_run)
        elif args.mode == "elastic":
            planned_calcs = pipeline.prepare_elastic_relaxations()
            pipeline.submit(planned_calcs, dry_run=args.dry_run)
        else:
            planned_calcs = (
                pipeline.prepare_eos_relaxations()
                + pipeline.prepare_elastic_relaxations()
            )
            pipeline.submit(planned_calcs, dry_run=args.dry_run)

        if args.mode in {"eos", "both"}:
            rows.extend(collect_rows_for_branch(run_root, source.name, "eos"))
        if args.mode in {"elastic", "both"}:
            rows.extend(collect_rows_for_branch(run_root, source.name, "elastic"))

    output_csv = Path(args.output_csv).resolve()
    write_csv(rows, output_csv)
    print(f"Prepared {len(structure_files)} structures; wrote {len(rows)} rows to {output_csv}")


if __name__ == "__main__":
    main()
