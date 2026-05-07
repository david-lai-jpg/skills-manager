"""Render manifest-resolved skills into client skill directories."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import action_log, adapters, resolver, store, transactions


def managed_rendered_id(path: Path) -> str | None:
    if path.is_symlink():
        target = path.resolve(strict=False)
        if store.path_under(target, store.skills_root()):
            return target.name
        return None
    marker = path / ".skills-manager.json"
    if path.is_dir() and marker.exists():
        data = store.read_json(marker, {})
        if data.get("manager") == "skills-manager":
            return data.get("skill_id")
    return None


def actual_rendered(rendered_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not rendered_dir.exists():
        return out
    for child in sorted(rendered_dir.iterdir(), key=lambda p: p.name):
        managed_id = managed_rendered_id(child)
        out[child.name] = {
            "path": str(child),
            "managed_skill_id": managed_id,
            "type": "symlink" if child.is_symlink() else "dir" if child.is_dir() else "file",
        }
    return out


def diff(client: str, project: str | Path | None = None) -> dict[str, Any]:
    rendered_dir = adapters.adapter(client).rendered_dir(project)
    resolved = resolver.resolve(client, project)
    actual = actual_rendered(rendered_dir)
    desired_aliases = {item["alias"]: skill_id for skill_id, item in resolved["desired"].items()}
    creates = []
    removes = []
    conflicts = []
    for alias, skill_id in desired_aliases.items():
        current = actual.get(alias)
        if not current:
            creates.append({"alias": alias, "skill_id": skill_id})
        elif current.get("managed_skill_id") != skill_id:
            conflicts.append(
                {
                    "alias": alias,
                    "path": current["path"],
                    "actual_managed_skill_id": current.get("managed_skill_id"),
                    "desired_skill_id": skill_id,
                }
            )
    for alias, info in actual.items():
        managed_id = info.get("managed_skill_id")
        if managed_id and alias not in desired_aliases:
            removes.append({"alias": alias, "skill_id": managed_id, "path": info["path"]})
    return {
        "client": client,
        "rendered_dir": str(rendered_dir),
        "creates": creates,
        "removes": removes,
        "conflicts": conflicts,
        "desired": desired_aliases,
        "actual": actual,
    }


def plan_actions(client: str, project: str | Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = diff(client, project)
    actions: list[dict[str, Any]] = []
    for item in d["removes"]:
        source = str(store.id_to_dir(item["skill_id"]))
        actions.append({"op": "remove_rendered", "client": client, "target": item["path"], "source": source, "skill_id": item["skill_id"]})
    for item in d["creates"]:
        source = store.id_to_dir(item["skill_id"])
        target = Path(d["rendered_dir"]) / item["alias"]
        actions.append(
            {
                "op": "create_symlink",
                "client": client,
                "target": str(target),
                "source": str(source),
                "skill_id": item["skill_id"],
                "alias": item["alias"],
            }
        )
    return d, actions


def apply_action(action: dict[str, Any]) -> None:
    target = Path(action["target"])
    source = Path(action["source"])
    if action["op"] == "remove_rendered":
        transactions.remove_manager_created(target)
        return
    if action["op"] == "create_symlink":
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(source, target_is_directory=True)
        except OSError:
            shutil.copytree(source, target)
            store.write_json(target / ".skills-manager.json", store.marker_data(action["skill_id"]))
            action["op"] = "create_copy"


def materialize(client: str, project: str | Path | None = None, dry_run: bool = False, surface: str = "core") -> dict[str, Any]:
    d, actions = plan_actions(client, project)
    if d["conflicts"]:
        return {"ok": False, "client": client, "dry_run": dry_run, "diff": d, "error": "unmanaged or mismatched rendered conflicts"}
    if dry_run:
        return {"ok": True, "client": client, "dry_run": True, "diff": d, "actions": actions}
    tx = transactions.new_transaction("materialize", actions)
    try:
        for action in actions:
            apply_action(action)
        transactions.mark(tx, "committed")
        action_log.append(
            "materialize",
            surface=surface,
            client=client,
            project_path=str(Path(project).expanduser().resolve()) if project is not None else None,
            transaction_id=tx["id"],
            rendered_dir=d["rendered_dir"],
        )
        return {"ok": True, "client": client, "dry_run": False, "transaction_id": tx["id"], "actions": actions}
    except Exception as exc:
        transactions.mark(tx, "failed", str(exc))
        return {"ok": False, "client": client, "transaction_id": tx["id"], "error": str(exc), "actions": actions}
