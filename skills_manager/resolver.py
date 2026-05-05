"""Manifest resolution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store

SCOPES = ("global", "profile", "project", "session")


def _manifest_sequence(project: str | Path | None = None) -> list[tuple[str, dict[str, Any]]]:
    seq = []
    for scope in SCOPES:
        if scope == "project" and project is None:
            continue
        seq.append((scope, store.load_manifest(scope, project=project)))
    return seq


def _apply_list(effective: set[str], values: list[str], op: str, reasons: dict[str, list[str]], reason: str) -> None:
    for value in values:
        if op == "enable":
            effective.add(value)
        else:
            effective.discard(value)
        reasons.setdefault(value, []).append(reason)


def resolve(client: str, project: str | Path | None = None) -> dict[str, Any]:
    managed = store.all_skills()
    effective: set[str] = set()
    reasons: dict[str, list[str]] = {}
    for scope, manifest in _manifest_sequence(project):
        _apply_list(effective, list(manifest.get("enable", [])), "enable", reasons, f"{scope}:enable")
        _apply_list(
            effective,
            list(manifest.get("clients", {}).get(client, {}).get("enable", [])),
            "enable",
            reasons,
            f"{scope}:{client}:enable",
        )
        _apply_list(effective, list(manifest.get("disable", [])), "disable", reasons, f"{scope}:disable")
        _apply_list(
            effective,
            list(manifest.get("clients", {}).get(client, {}).get("disable", [])),
            "disable",
            reasons,
            f"{scope}:{client}:disable",
        )
    desired: dict[str, dict[str, Any]] = {}
    unknown = sorted(skill_id for skill_id in effective if skill_id not in managed)
    for skill_id in sorted(effective):
        meta = managed.get(skill_id)
        if not meta:
            continue
        if not meta.get("compatibility", {}).get(client, False):
            reasons.setdefault(skill_id, []).append(f"{client}:incompatible")
            continue
        alias = meta.get("aliases", {}).get(client) or skill_id.split(".")[-1]
        desired[skill_id] = {"id": skill_id, "alias": alias, "meta": meta, "reasons": reasons.get(skill_id, [])}
    by_skill = {}
    for skill_id, meta in managed.items():
        by_skill[skill_id] = {
            "enabled": skill_id in desired,
            "alias": meta.get("aliases", {}).get(client),
            "compatible": meta.get("compatibility", {}).get(client, False),
            "reasons": reasons.get(skill_id, []),
        }
    return {"client": client, "desired": desired, "skills": by_skill, "unknown_enabled_ids": unknown}


def set_skill(scope: str, skill_id: str, enabled: bool, client: str = "all", project: str | Path | None = None) -> Path:
    manifest = store.load_manifest(scope, project=project)
    if client == "all":
        target = manifest
    else:
        target = manifest.setdefault("clients", {}).setdefault(client, {"enable": [], "disable": []})
    add_key, remove_key = ("enable", "disable") if enabled else ("disable", "enable")
    target.setdefault(add_key, [])
    target.setdefault(remove_key, [])
    if skill_id not in target[add_key]:
        target[add_key].append(skill_id)
    target[remove_key] = [x for x in target[remove_key] if x != skill_id]
    return store.save_manifest(scope, manifest, project=project)
