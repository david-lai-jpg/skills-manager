"""Skill filesystem scanner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import adapters, store


def classify_entry(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "name": path.name}
    try:
        if path.is_symlink():
            target = Path(path.readlink())
            item["link_target"] = str(target)
            resolved = path.resolve(strict=False)
            if not path.exists():
                item["type"] = "broken_symlink"
                return item
            item["resolved"] = str(resolved)
            item["type"] = "symlink_skill" if store.is_skill_dir(path) else "symlink_non_skill"
        elif path.is_dir():
            item["type"] = "skill_dir" if (path / "SKILL.md").is_file() else "missing_skill_md"
        else:
            item["type"] = "file"
        if item["type"] in {"skill_dir", "symlink_skill"}:
            item["content_hash"] = store.content_hash(path)
    except OSError as exc:
        item["type"] = "error"
        item["error"] = str(exc)
    return item


def scan_dir(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists(), "entries": []}
    if not path.exists():
        return result
    if not path.is_dir():
        result["error"] = "not a directory"
        return result
    result["entries"] = [classify_entry(p) for p in sorted(path.iterdir(), key=lambda x: x.name)]
    return result


def scan(project: str | Path | None = None) -> dict[str, Any]:
    locations: dict[str, Path] = {
        "inbox": store.inbox_dir(),
        "store": store.skills_root(),
        "claude_global": adapters.adapter("claude").global_dir(),
        "codex_global": adapters.adapter("codex").global_dir(),
    }
    if project:
        locations["claude_project"] = adapters.adapter("claude").project_dir(project)
        locations["codex_project"] = adapters.adapter("codex").project_dir(project)
    out: dict[str, Any] = {"locations": {name: scan_dir(path) for name, path in locations.items()}}
    names: dict[str, list[str]] = {}
    hashes: dict[str, list[str]] = {}
    for loc_name, loc in out["locations"].items():
        for entry in loc.get("entries", []):
            if entry.get("type") in {"skill_dir", "symlink_skill"}:
                names.setdefault(entry["name"], []).append(f"{loc_name}:{entry['path']}")
                if entry.get("content_hash"):
                    hashes.setdefault(entry["content_hash"], []).append(f"{loc_name}:{entry['path']}")
    out["duplicates"] = {
        "names": {k: v for k, v in names.items() if len(v) > 1},
        "content_hashes": {k: v for k, v in hashes.items() if len(v) > 1},
    }
    return out
