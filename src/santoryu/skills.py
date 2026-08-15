"""Register the skills this package ships with Claude Code.

Installation is a plain copy. The skill markdown names the console commands
this package puts on PATH (`santoryu`, `query-json`), so nothing here writes a
filesystem path into the copied file — which is exactly what lets an install
outlive the checkout it was built from being moved, renamed, or deleted.

Skills are read out of package data rather than from a directory next to this
module: after a pip/pipx install there is no checkout to sit next to.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

SKILL_FILE = "SKILL.md"


def claude_skills_dir() -> Path:
    """The Claude Code skills directory, honouring CLAUDE_CONFIG_DIR."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(base) / "skills"


def packaged_skills() -> list[tuple[str, str]]:
    """Every skill shipped in package data, as (name, markdown) pairs."""
    root = files("santoryu") / "data" / "skills"
    found = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        skill = entry / SKILL_FILE
        if skill.is_file():
            found.append((entry.name, skill.read_text(encoding="utf-8")))
    return sorted(found)


def install_skills() -> list[Path]:
    """Copy every packaged skill into the Claude skills dir, overwriting stale
    copies. Idempotent: a second run writes the same bytes."""
    skills = packaged_skills()
    if not skills:
        raise RuntimeError("no packaged skills found under santoryu/data/skills")

    target_root = claude_skills_dir()
    written = []
    for name, text in skills:
        target_dir = target_root / name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / SKILL_FILE
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written
