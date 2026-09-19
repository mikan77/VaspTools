"""`vasptools` command-line entry point.

Sub-commands wrap the scripts in ``VaspTools/scripts``; ``relax`` reads its
defaults from a ``vasptools.yaml`` project file so day-to-day use is::

    cd my_series
    vasptools relax init          # write vasptools.yaml + relax.yaml templates
    vasptools relax submit        # (--dry-run to only prepare)
    vasptools relax collect
    vasptools legacy collect run2
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


CONFIG_NAME = "vasptools.yaml"

# Keys allowed in vasptools.yaml -> run_relax_batch argparse destinations.
RELAX_CONFIG_KEYS = (
    "structures_dir",
    "template_dir",
    "output",
    "output_csv",  # legacy alias of output
    "output_root",
    "relaxed_dir",
    "relaxed_format",
    "protocol",
    "relax_steps",
    "index_start",
    "job_script_name",
    "symprec",
    "bond_tolerance",
    "potcar_mode",
    "require_kspacing",
)
RELAX_PATH_KEYS = ("structures_dir", "template_dir", "output", "output_csv", "output_root", "relaxed_dir", "protocol")

CONFIG_TEMPLATE = """\
# vasptools.yaml — project settings for `vasptools relax submit|collect`.
# Paths are relative to this file. Command-line flags override these values.

structures_dir: structs        # POSCAR / 27_POSCAR / *.vasp / *.cif files to relax
template_dir: tmpl             # POTCAR, INCAR, job_template.sh
output: summary.xlsx           # .xlsx = Excel, .csv = plain CSV

# Relaxation protocol: per-step INCAR tags (see relax.yaml). Comment out to run
# a single step with the template INCAR as is, or set relax_steps: 2 for
# identical repeated steps.
protocol: relax.yaml
# relax_steps: 1

# Optional:
# output_root: /scratch/user/series1_runs   # default: <template_dir>/relax_runs
# relaxed_dir: relaxed                      # default: <output_root>/relaxed
# relaxed_format: both                      # vasp | cif | both
# index_start: 1000
# potcar_mode: hardlink                     # hardlink | copy | symlink
# symprec: 0.05
# bond_tolerance: 1.2
"""

PROTOCOL_TEMPLATE = """\
# relax.yaml — one list item per relaxation step; keys are INCAR tags.
# Tags not listed here are inherited from tmpl/INCAR.
steps:
  - ISIF: 2          # step 1: ions only
    NSW: 60
    EDIFFG: -0.02
  - ISIF: 3          # step 2: ions + cell
    NSW: 100
    EDIFFG: -0.005
"""


def load_config(path: Path) -> dict[str, object]:
    """Read vasptools.yaml; paths become absolute relative to the file."""

    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: must be a mapping of settings.")
    unknown = sorted(set(data) - set(RELAX_CONFIG_KEYS))
    if unknown:
        raise SystemExit(f"{path}: unknown key(s) {unknown}. Allowed: {', '.join(RELAX_CONFIG_KEYS)}")
    base = path.parent
    config: dict[str, object] = {}
    for key, value in data.items():
        if value is None:
            continue
        if key in RELAX_PATH_KEYS:
            candidate = Path(str(value)).expanduser()
            value = str(candidate if candidate.is_absolute() else base / candidate)
        if key == "output_csv":
            key = "output"
        config[key] = value
    return config


def find_config(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"Config file not found: {path}")
        return path
    candidate = Path.cwd() / CONFIG_NAME
    return candidate if candidate.is_file() else None


def cmd_relax_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in ((CONFIG_NAME, CONFIG_TEMPLATE), ("relax.yaml", PROTOCOL_TEMPLATE)):
        path = target_dir / name
        if path.exists() and not args.force:
            print(f"{path} exists, keeping it (use --force to overwrite)")
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)
    for path in written:
        print(f"wrote {path}")
    print(
        "\nNext: put structures into structs/, POTCAR + INCAR + job_template.sh into tmpl/,\n"
        "edit vasptools.yaml / relax.yaml, then run `vasptools relax submit --dry-run`."
    )
    return 0


def cmd_relax(args: argparse.Namespace, passthrough: list[str], *, collect: bool) -> int:
    from .scripts import run_relax_batch

    config_path = find_config(args.config)
    defaults = load_config(config_path) if config_path else {}
    if config_path:
        print(f"using {config_path}")
    if collect:
        passthrough = [*passthrough, "--collect-only"]
    run_relax_batch.main(passthrough, config_defaults=defaults)
    return 0


def cmd_legacy_collect(args: argparse.Namespace, passthrough: list[str]) -> int:
    from .scripts import collect_legacy_relax

    argv = ["--runs-dir", args.runs_dir, *passthrough]
    collect_legacy_relax.main(argv)
    return 0


def cmd_passthrough(module_name: str, passthrough: list[str]) -> int:
    import importlib

    module = importlib.import_module(f"VaspTools.scripts.{module_name}")
    module.main(passthrough)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vasptools",
        description="VaspTools command line. Run `vasptools <command> <subcommand> --help` for options.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    relax = commands.add_parser("relax", help="Batch relaxation driven by vasptools.yaml.")
    relax_sub = relax.add_subparsers(dest="subcommand", required=True)

    init = relax_sub.add_parser("init", help="Write vasptools.yaml and relax.yaml templates.")
    init.add_argument("directory", nargs="?", default=".", help="Project folder (default: current).")
    init.add_argument("--force", action="store_true", help="Overwrite existing template files.")

    for name, help_text in (
        ("submit", "Prepare and submit one SLURM job per structure (add --dry-run to only prepare)."),
        ("collect", "Collect finished runs: CSV table + relaxed structures."),
    ):
        sub = relax_sub.add_parser(
            name,
            help=help_text,
            epilog="Any run_relax_batch.py flag can follow and overrides vasptools.yaml.",
        )
        sub.add_argument("--config", default=None, help=f"Settings file (default: ./{CONFIG_NAME}).")

    legacy = commands.add_parser("legacy", help="Harvest results of the old step_N_final pipeline.")
    legacy_sub = legacy.add_subparsers(dest="subcommand", required=True)
    legacy_collect = legacy_sub.add_parser(
        "collect",
        help="Build XLSX/CSV and collect last CONTCARs from run2-style folders.",
        epilog="Other collect_legacy_relax.py flags (--xlsx, --relaxed-dir, --include-unfinished) can follow.",
    )
    legacy_collect.add_argument("runs_dir", help="Folder with calc_XXXXX directories.")

    commands.add_parser("multi", help="EOS/elastic for many structures (args of run_multi_scan.py).", add_help=False)
    commands.add_parser("scan", help="INCAR parameter scan for one structure (args of run_param_scan.py).", add_help=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sys.argv[0] = "vasptools"  # so the wrapped scripts print `vasptools: error: ...`
    parser = build_parser()

    # `multi` and `scan` forward everything verbatim to the underlying script.
    if argv and argv[0] in {"multi", "scan"}:
        module = {"multi": "run_multi_scan", "scan": "run_param_scan"}[argv[0]]
        return cmd_passthrough(module, argv[1:])

    args, passthrough = parser.parse_known_args(argv)
    if args.command == "relax":
        if args.subcommand == "init":
            return cmd_relax_init(args)
        return cmd_relax(args, passthrough, collect=args.subcommand == "collect")
    if args.command == "legacy":
        return cmd_legacy_collect(args, passthrough)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
