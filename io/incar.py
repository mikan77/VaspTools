"""INCAR handling for the mechanical-property workflow.

The user-provided INCAR is the source of physical settings. This module only
adds stage-control tags that are required by the workflow rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, MutableMapping


STATIC_REQUIRED_TAGS = {
    "ISIF": 2,
    "IBRION": -1,
    "NSW": 0,
    "ALGO": "All",
    "ISEARCH": 1,
}

ALL_STAGE_REQUIRED_TAGS = {
    "ISIF": 2,
}


class Incar(dict):
    """Small INCAR mapping with pymatgen-like from_file/write_file helpers."""

    @classmethod
    def from_file(cls, path: str | Path) -> "Incar":
        incar = cls()
        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].split("!", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            incar[key.strip().upper()] = _parse_incar_value(value.strip())
        return incar

    def write_file(self, path: str | Path) -> None:
        lines = [f"{key} = {_format_incar_value(value)}" for key, value in self.items()]
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_incar_value(value: str) -> object:
    stripped = value.strip()
    upper = stripped.upper()
    if upper in {".TRUE.", "TRUE", "T"}:
        return True
    if upper in {".FALSE.", "FALSE", "F"}:
        return False

    parts = stripped.split()
    if len(parts) > 1:
        return [_parse_incar_value(part) for part in parts]

    try:
        return int(stripped)
    except ValueError:
        pass

    try:
        return float(stripped)
    except ValueError:
        return stripped


def _format_incar_value(value: object) -> str:
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, (list, tuple)):
        return " ".join(_format_incar_value(item) for item in value)
    return str(value)


def _matching_key(mapping: Mapping[str, object], tag: str) -> str | None:
    tag_upper = tag.upper()
    for key in mapping:
        if key.upper() == tag_upper:
            return key
    return None


def set_incar_tag(incar: MutableMapping[str, object], tag: str, value: object) -> None:
    """Set a VASP INCAR tag case-insensitively.

    If the same tag already exists with different capitalization, it is removed
    before the canonical upper-case tag is inserted.
    """

    existing = _matching_key(incar, tag)
    if existing is not None and existing != tag.upper():
        del incar[existing]
    incar[tag.upper()] = value


def has_incar_tag(incar: Mapping[str, object], tag: str) -> bool:
    """Return True if an INCAR tag exists, case-insensitively."""

    return _matching_key(incar, tag) is not None


def get_incar_tag(incar: Mapping[str, object], tag: str) -> object:
    """Return an INCAR tag value, case-insensitively."""

    existing = _matching_key(incar, tag)
    if existing is None:
        raise KeyError(tag)
    return incar[existing]


def load_incar(path: str | Path) -> Incar:
    """Load an INCAR file."""

    return Incar.from_file(str(path))


def validate_kspacing_incar(incar: Mapping[str, object]) -> None:
    """Require KSPACING so the workflow never needs to create KPOINTS."""

    if not has_incar_tag(incar, "KSPACING"):
        raise ValueError(
            "INCAR must contain KSPACING. This pipeline does not create or copy KPOINTS."
        )


def make_stage_incar(
    template: Mapping[str, object],
    *,
    system: str,
    stage: str,
    require_kspacing: bool = True,
    extra_overrides: Mapping[str, object] | None = None,
) -> Incar:
    """Create a stage-specific INCAR from the user template.

    Rules enforced here:
    - ISIF=2 for every calculation.
    - Static calculations also force IBRION=-1, NSW=0, ALGO=All, ISEARCH=1.
    - KPOINTS are never generated, so KSPACING must be present unless disabled.
    """

    incar = Incar(dict(template))
    if require_kspacing:
        validate_kspacing_incar(incar)

    set_incar_tag(incar, "SYSTEM", system)
    for tag, value in ALL_STAGE_REQUIRED_TAGS.items():
        set_incar_tag(incar, tag, value)

    if stage.endswith("_static") or stage == "static":
        for tag, value in STATIC_REQUIRED_TAGS.items():
            set_incar_tag(incar, tag, value)

    if extra_overrides:
        for tag, value in extra_overrides.items():
            if tag.upper() == "ISIF" and int(value) != 2:
                raise ValueError("ISIF must be 2 for every calculation in this workflow.")
            set_incar_tag(incar, tag, value)

    return incar


def write_incar(incar: Mapping[str, object], path: str | Path) -> None:
    """Write an INCAR file."""

    Incar(dict(incar)).write_file(str(path))
