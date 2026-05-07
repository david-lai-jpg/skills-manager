"""Portable backup and restore for the managed skill system."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from . import adapters, materializer, store


def backup_root(export_path: str | Path) -> Path:
    path = Path(export_path).expanduser().resolve()
    return path if path.name == "agent-skills-backup" else path / "agent-skills-backup"


def rendered_list(client: str) -> list[dict[str, Any]]:
    rendered_dir = adapters.adapter(client).global_dir()
    return list(materializer.actual_rendered(rendered_dir).values())


def dry_run_export(export_path: str | Path | None = None) -> dict[str, Any]:
    target = str(backup_root(export_path or "./agent-skills-backup"))
    return {
        "target": target,
        "include": [
            str(store.skills_root()),
            str(store.manifests_root()),
            str(store.transactions_root()),
            str(store.presets_root()),
            str(store.logs_root()),
            str(store.inbox_dir()),
        ],
        "rendered_metadata_only": {"claude": rendered_list("claude"), "codex": rendered_list("codex")},
    }


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def export(export_path: str | Path) -> dict[str, Any]:
    root = backup_root(export_path)
    root.mkdir(parents=True, exist_ok=True)
    copy_if_exists(store.skills_root(), root / "skills-store" / "skills")
    copy_if_exists(store.manifests_root(), root / "skills-store" / "manifests")
    copy_if_exists(store.transactions_root(), root / "skills-store" / "transactions")
    copy_if_exists(store.presets_root(), root / "skills-store" / "presets")
    copy_if_exists(store.logs_root(), root / "skills-store" / "logs")
    copy_if_exists(store.inbox_dir(), root / "inbox" / "agents-skills")
    store.write_json(root / "rendered" / "claude-skills-list.json", rendered_list("claude"))
    store.write_json(root / "rendered" / "codex-skills-list.json", rendered_list("codex"))
    manifest = {"version": store.VERSION, "created_at": time.time(), "kind": "agent-skills-backup"}
    store.write_json(root / "manifest.json", manifest)
    return {"ok": True, "backup": str(root), "manifest": manifest}


def normalize_backup(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if (root / "manifest.json").exists():
        return root
    if (root / "agent-skills-backup" / "manifest.json").exists():
        return root / "agent-skills-backup"
    return root


def restore_plan(path: str | Path) -> dict[str, Any]:
    root = normalize_backup(path)
    return {
        "backup": str(root),
        "exists": (root / "manifest.json").exists(),
        "copies": [
            {"from": str(root / "skills-store" / "skills"), "to": str(store.skills_root())},
            {"from": str(root / "skills-store" / "manifests"), "to": str(store.manifests_root())},
            {"from": str(root / "skills-store" / "transactions"), "to": str(store.transactions_root())},
            {"from": str(root / "skills-store" / "presets"), "to": str(store.presets_root())},
            {"from": str(root / "skills-store" / "logs"), "to": str(store.logs_root())},
            {"from": str(root / "inbox" / "agents-skills"), "to": str(store.inbox_dir())},
        ],
        "after": ["skills-manager materialize --client all", "skills-manager doctor"],
    }


def restore(path: str | Path, dry_run: bool = True) -> dict[str, Any]:
    plan = restore_plan(path)
    if not plan["exists"]:
        return {"ok": False, "error": f"backup manifest not found: {plan['backup']}", "plan": plan}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}
    for copy in plan["copies"]:
        copy_if_exists(Path(copy["from"]), Path(copy["to"]))
    return {"ok": True, "dry_run": False, "plan": plan, "message": "restore copied store/inbox data; run materialize next"}
