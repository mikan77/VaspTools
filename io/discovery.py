"""Discovery helpers for prepared calculation directories."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.models import Calculation


def discover_calculations(
    root: str | Path,
    *,
    stage: str | None = None,
    branch: str | None = None,
) -> list[Calculation]:
    """Find prepared calculation directories below ``root``.

    A prepared directory is identified by ``vasptools_metadata.json``.
    """

    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(root)

    calculations = []
    for metadata_path in sorted(root.rglob("vasptools_metadata.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if stage is not None and metadata.get("stage") != stage:
            continue
        if branch is not None and metadata.get("branch") != branch:
            continue

        directory = metadata_path.parent
        calculations.append(
            Calculation(
                name=str(metadata.get("name", directory.name)),
                stage=str(metadata.get("stage", "")),
                directory=directory,
                job_name=str(metadata.get("job_name", directory.name)),
                metadata=metadata,
            )
        )
    return calculations
