from __future__ import annotations

import json
from pathlib import Path

from .models import Project


def load_project(path: str | Path) -> Project:
    with Path(path).open("r", encoding="utf-8") as handle:
        return Project.from_dict(json.load(handle))


def save_project(project: Project, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(project.to_dict(), handle, ensure_ascii=False, indent=2)
