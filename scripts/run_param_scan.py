#!/usr/bin/env python3
"""Run a parameter scan for one structure via INCAR overrides.

Example:
  python run_param_scan.py \
    --workdir /path/to/run_root \
    --scan Zab=-1:-3:-0.1 \
    --output-csv /path/to/scan_results.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PARENT = ROOT.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from VaspTools import MechanicalPipeline, PipelineInputs, PipelineConfig
from VaspTools.io.jobs import resolve_job_template_path

from VaspTools.scripts._scan_utils import parse_scan_specs, read_result_row, flatten_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run INCAR parameter scan for one structure.")
    parser.add_argument(
        "--workdir",
        required=True,
        help="Directory with POSCAR/POTCAR/INCAR and optional job template (.sh).",
    )
    parser.add_argument(
        "--input-poscar",
        default="POSCAR",
        help="Input POSCAR filename inside workdir (default: POSCAR).",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help="Path to output CSV summary.",
    )
    parser.add_argument(
        "--scan",
        action="append",
        help=(
            "Scan spec in format `TAG=start:stop:step` or `TAG=v1,v2,v3`. "
            "Repeat this flag for multiple INCAR tags."
        ),
    )
    parser.add_argument(
        "--stage",
        default="single_static",
        help="Stage tag passed to factory.make_stage_incar (default: single_static).",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Do not prepare/submit calculations; only collect from existing scan directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not submit to sbatch, only print submission commands.",
    )
    parser.add_argument(
        "--job-script-name",
        default="job.sh",
        help="Job script name to write in each calc directory.",
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
    return parser.parse_args()


def write_csv(rows: list[dict[str, object]], output_csv: Path) -> None:
    if not rows:
        output_csv.write_text("structure_file,branch,status\n", encoding="utf-8")
        return

    header, normalized = flatten_rows(rows)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in normalized:
            writer.writerow({key: row.get(key, "") for key in header})


def main() -> None:
    args = parse_args()
    workdir = Path(args.workdir).resolve()
    output_csv = Path(args.output_csv).resolve()

    if not args.collect_only and not args.scan:
        raise SystemExit("Error: --scan is required unless --collect-only is set.")

    scan_parameters = parse_scan_specs(args.scan or [])
    inputs = PipelineInputs(
        poscar=workdir / args.input_poscar,
        potcar=workdir / "POTCAR",
        incar=workdir / "INCAR",
        job_template=resolve_job_template_path(workdir),
    )
    # Create a base pipeline to reuse input config and parameter validation.
    pipe = MechanicalPipeline(
        inputs,
        PipelineConfig(
            workdir=workdir,
            name=workdir.name,
            job_script_name=args.job_script_name,
            require_kspacing=args.require_kspacing,
        ),
    )
    mode = pipe.get_mode("param_scan")

    if args.collect_only:
        print("Collect-only mode: reading existing scan calculations from disk.")
        rows_raw = mode.collect_results()
        rows = []
        for row in rows_raw:
            scan_dir = Path(row["path"])
            detail = read_result_row(
                scan_dir,
                structure_name=inputs.poscar.name,
                branch="param_scan",
                fallback_initial_volume=row.get("initial_volume"),
            )
            detail["path"] = str(scan_dir)
            if "status" in row:
                detail["status"] = row.get("status", detail.get("status"))
            rows.append(detail)
        if not rows and scan_parameters:
            print(
                "No prepared param-scan calculations found under "
                f"{pipe.param_scan.root}. Ensure this path contains scan subdirectories."
            )
    else:
        calculations = mode.prepare_calculations(scan_parameters, stage=args.stage)
        pipe.submit(calculations, dry_run=args.dry_run)
        rows = []
        for calc in calculations:
            row = read_result_row(
                calc.directory,
                structure_name=inputs.poscar.name,
                branch="param_scan",
            )
            rows.append(row)

    write_csv(rows, output_csv)

    print(f"Wrote {len(rows)} rows to {output_csv}")
    if rows and not args.dry_run:
        # quick summary
        completed = sum(1 for row in rows if row.get("status") == "completed")
        print(f"Completed: {completed}/{len(rows)}")


if __name__ == "__main__":
    main()
