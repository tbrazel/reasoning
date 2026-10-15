"""Install the skills shipped with Reasoning without resurrecting deleted heads."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from .skills import SkillRegistry


def ensure_builtin_skill(
    registry: SkillRegistry,
    name: str,
    *,
    version: int = 1,
) -> None:
    """Seed one packaged skill once into a workbench-wide immutable registry.

    A marker is written even when the user already owns a skill with this name.
    Deleting the active head later is therefore respected across app restarts.
    """

    marker = registry.root.parent / f".builtin-{name}-seeded-v{version}"
    if marker.exists():
        return
    active = {item["skill_id"] for item in registry.list()}
    packaged = resources.files("reasoning_ledger") / "builtin_skills" / name
    with resources.as_file(packaged) as source:
        if name not in active and not (registry.root / name).exists():
            registry.import_package(
                Path(source),
                provenance={
                    "source_kind": "reasoning-builtin",
                    "source_label": "Reasoning",
                    "source_path": f"package:reasoning_ledger/builtin_skills/{name}",
                },
            )
        elif name in active:
            current = registry.get(name)
            if current.provenance.get("source_kind") == "reasoning-builtin":
                registry.import_package(
                    Path(source),
                    provenance={
                        "source_kind": "reasoning-builtin",
                        "source_label": "Reasoning",
                        "source_path": f"package:reasoning_ledger/builtin_skills/{name}",
                    },
                )
    marker.write_text("seeded\n", encoding="utf-8")
