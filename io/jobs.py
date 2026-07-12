"""SLURM job-script helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess


JOB_NAME_PLACEHOLDERS = (
    "{job_name}",
    "{JOB_NAME}",
    "{{JOB_NAME}}",
    "__JOB_NAME__",
)

DEFAULT_JOB_TEMPLATE_NAMES = (
    "job_template.sh",
    "job.sh",
)


@dataclass(frozen=True)
class Submission:
    """Result of an sbatch submission."""

    workdir: Path
    script: str
    command: tuple[str, ...]
    job_id: str | None
    stdout: str
    stderr: str
    dry_run: bool = False


def sanitize_job_name(name: str, max_length: int = 128) -> str:
    """Return a SLURM-friendly job name."""

    clean = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name.strip())
    clean = clean.strip("_")
    return (clean or "vasp_job")[:max_length]


def render_job_script(template: str, job_name: str) -> str:
    """Render a job script by changing only the job name.

    The template must contain a supported placeholder or an existing SBATCH
    job-name directive. Other lines are preserved.
    """

    safe_name = sanitize_job_name(job_name)
    rendered = template
    replaced = False
    for placeholder in JOB_NAME_PLACEHOLDERS:
        if placeholder in rendered:
            rendered = rendered.replace(placeholder, safe_name)
            replaced = True

    if replaced:
        return rendered

    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^\s*#SBATCH\s+-J(\s+|=)", line):
            prefix = re.match(r"^(\s*#SBATCH\s+-J(?:\s+|=)).*$", line).group(1)
            lines[index] = f"{prefix}{safe_name}"
            return "\n".join(lines) + ("\n" if rendered.endswith("\n") else "")

        if re.match(r"^\s*#SBATCH\s+--job-name(?:\s+|=)", line):
            prefix = re.match(r"^(\s*#SBATCH\s+--job-name(?:\s+|=)).*$", line).group(1)
            lines[index] = f"{prefix}{safe_name}"
            return "\n".join(lines) + ("\n" if rendered.endswith("\n") else "")

    raise ValueError(
        "Job template must contain a job-name placeholder or an existing #SBATCH job-name line."
    )


def write_job_script(
    template_path: str | Path,
    output_path: str | Path,
    *,
    job_name: str,
) -> None:
    """Write a rendered job script."""

    template = Path(template_path).read_text(encoding="utf-8")
    rendered = render_job_script(template, job_name)
    Path(output_path).write_text(rendered, encoding="utf-8")


def render_two_stage_job_script(
    template: str,
    job_name: str,
    *,
    static_directory: str,
    relax_stage_script: str = ".vasptools_relax_stage.sh",
    static_stage_script: str = "job.sh",
) -> str:
    """Render one SLURM driver that runs relaxation and static stages.

    The original template is executed as a regular shell script for each
    stage. Only its shebang and ``#SBATCH`` directives are copied to the
    driver, so the allocation is requested once and the VASP command runs
    twice inside that same allocation.
    """

    rendered = render_job_script(template, job_name)
    lines = rendered.splitlines()
    directives = [line for line in lines if line.lstrip().startswith("#SBATCH")]

    static_token = shlex.quote(static_directory)
    relax_script_token = shlex.quote(relax_stage_script)
    static_script_token = shlex.quote(static_stage_script)
    driver = [
        "#!/bin/bash",
        *directives,
        "",
        "set -euo pipefail",
        'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        f'STATIC_DIR="$SCRIPT_DIR"/{static_token}',
        f'RELAX_STAGE="$SCRIPT_DIR"/{relax_script_token}',
        f'STATIC_STAGE="$STATIC_DIR"/{static_script_token}',
        "",
        '(cd "$SCRIPT_DIR" && bash -e "$RELAX_STAGE")',
        'test -s "$SCRIPT_DIR/CONTCAR"',
        'test -d "$STATIC_DIR"',
        'cp "$SCRIPT_DIR/CONTCAR" "$STATIC_DIR/POSCAR"',
        '(cd "$STATIC_DIR" && bash -e "$STATIC_STAGE")',
        "",
    ]
    return "\n".join(driver)


def resolve_job_template_path(
    workdir: str | Path,
    requested_name: str | None = None,
) -> Path:
    """Resolve the shared shell script used as a job template.

    If requested_name is provided, that exact file is used. Otherwise the
    resolver prefers job_template.sh, then job.sh, then a single unambiguous
    *.sh file in the workdir.
    """

    workdir_path = Path(workdir)
    if requested_name:
        return workdir_path / requested_name

    for filename in DEFAULT_JOB_TEMPLATE_NAMES:
        candidate = workdir_path / filename
        if candidate.is_file():
            return candidate

    shell_scripts = sorted(path for path in workdir_path.glob("*.sh") if path.is_file())
    if len(shell_scripts) == 1:
        return shell_scripts[0]
    if not shell_scripts:
        raise FileNotFoundError(
            f"No shell job template found in {workdir_path}. Expected job_template.sh, "
            "job.sh, or one *.sh file."
        )

    choices = ", ".join(path.name for path in shell_scripts)
    raise ValueError(
        "Multiple *.sh job templates found in "
        f"{workdir_path}: {choices}. Pass input_job_template explicitly."
    )


def parse_sbatch_job_id(output: str) -> str | None:
    """Extract a SLURM job id from sbatch output."""

    match = re.search(r"Submitted batch job\s+(\S+)", output)
    return match.group(1) if match else None


def submit_sbatch(
    workdir: str | Path,
    *,
    script_name: str = "job.sh",
    dry_run: bool = False,
) -> Submission:
    """Submit a calculation directory through sbatch."""

    workdir_path = Path(workdir)
    command = ("sbatch", script_name)
    if dry_run:
        return Submission(
            workdir=workdir_path,
            script=script_name,
            command=command,
            job_id=None,
            stdout="",
            stderr="",
            dry_run=True,
        )

    completed = subprocess.run(
        command,
        cwd=str(workdir_path),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"sbatch failed in {workdir_path}: {completed.stderr or completed.stdout}"
        )

    return Submission(
        workdir=workdir_path,
        script=script_name,
        command=command,
        job_id=parse_sbatch_job_id(completed.stdout),
        stdout=completed.stdout,
        stderr=completed.stderr,
        dry_run=False,
    )
