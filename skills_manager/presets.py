"""Preset storage and inspection helpers."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import action_log, store


def preset_path(name: str) -> Path:
    return store.presets_root() / f"{store.slugify(name)}.json"


def list_presets() -> list[str]:
    root = store.presets_root()
    if not root.exists():
        return []
    return sorted(path.stem for path in root.glob("*.json") if path.is_file())


def _normalize_entries(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    entries = []
    for value in values:
        if isinstance(value, str):
            entries.append({"id": value, "alias": ""})
        elif isinstance(value, dict) and isinstance(value.get("id"), str):
            entries.append({"id": value["id"], "alias": str(value.get("alias") or "")})
    return entries


def normalize_preset(data: dict[str, Any], name: str) -> dict[str, Any]:
    clients = data.get("clients", {}) if isinstance(data.get("clients"), dict) else {}
    claude = clients.get("claude", {}) if isinstance(clients.get("claude"), dict) else {}
    codex = clients.get("codex", {}) if isinstance(clients.get("codex"), dict) else {}
    normalized = {
        "version": data.get("version", store.VERSION),
        "name": str(data.get("name") or name),
        "description": str(data.get("description") or ""),
        "tags": list(data.get("tags", [])) if isinstance(data.get("tags"), list) else [],
        "enable": _normalize_entries(data.get("enable", [])),
        "disable": _normalize_entries(data.get("disable", [])),
        "clients": {
            "claude": {
                "enable": _normalize_entries(claude.get("enable", [])),
                "disable": _normalize_entries(claude.get("disable", [])),
            },
            "codex": {
                "enable": _normalize_entries(codex.get("enable", [])),
                "disable": _normalize_entries(codex.get("disable", [])),
            },
        },
    }
    return normalized


def empty_preset(name: str, description: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    return normalize_preset(
        {
            "version": store.VERSION,
            "name": store.slugify(name),
            "description": description,
            "tags": tags or [],
        },
        store.slugify(name),
    )


def write_preset(name: str, preset: dict[str, Any]) -> Path:
    path = preset_path(name)
    store.write_json(path, preset)
    return path


def create_preset(
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    dry_run: bool = False,
    surface: str = "core",
) -> dict[str, Any]:
    preset = empty_preset(name, description=description, tags=tags)
    path = preset_path(name)
    result = {"ok": True, "dry_run": dry_run, "preset": preset, "path": str(path)}
    if dry_run:
        result["would_write"] = True
        return result
    write_preset(name, preset)
    action_log.append("preset_create", surface=surface, preset_name=preset["name"], target_path=str(path))
    result["written"] = True
    return result


def _preset_entry(skill_id: str, managed: dict[str, dict[str, Any]]) -> dict[str, str]:
    meta = managed.get(skill_id, {})
    aliases = meta.get("aliases", {}) if isinstance(meta, dict) else {}
    alias = aliases.get("claude") or aliases.get("codex") or skill_id.split(".")[-1]
    return {"id": skill_id, "alias": alias}


def resolve_skill_refs(refs: list[str]) -> dict[str, Any]:
    managed = store.all_skills()
    resolved: list[dict[str, str]] = []
    errors = []
    for ref in refs:
        if ref in managed:
            resolved.append(_preset_entry(ref, managed))
            continue
        matches = [
            skill_id
            for skill_id, meta in managed.items()
            if ref == skill_id.split(".")[-1] or ref in set(meta.get("aliases", {}).values())
        ]
        if len(matches) == 1:
            resolved.append(_preset_entry(matches[0], managed))
        elif len(matches) > 1:
            errors.append({"ref": ref, "error": "ambiguous", "candidates": sorted(matches)})
        else:
            errors.append({"ref": ref, "error": "unknown"})
    if errors:
        return {"ok": False, "errors": errors}
    return {"ok": True, "entries": resolved}


def capture_preset(
    name: str,
    scope: str,
    project: str | Path | None = None,
    description: str = "",
    tags: list[str] | None = None,
    dry_run: bool = False,
    surface: str = "core",
) -> dict[str, Any]:
    manifest = store.load_manifest(scope, project=project)
    managed = store.all_skills()
    preset = empty_preset(name, description=description, tags=tags)
    preset["enable"] = [_preset_entry(skill_id, managed) for skill_id in manifest.get("enable", [])]
    preset["disable"] = [_preset_entry(skill_id, managed) for skill_id in manifest.get("disable", [])]
    for client in ("claude", "codex"):
        client_data = manifest.get("clients", {}).get(client, {})
        preset["clients"][client]["enable"] = [_preset_entry(skill_id, managed) for skill_id in client_data.get("enable", [])]
        preset["clients"][client]["disable"] = [_preset_entry(skill_id, managed) for skill_id in client_data.get("disable", [])]
    path = preset_path(name)
    result = {"ok": True, "dry_run": dry_run, "preset": preset, "path": str(path), "source_scope": scope}
    if project is not None:
        result["project"] = str(Path(project).expanduser().resolve())
    if dry_run:
        result["would_write"] = True
        return result
    write_preset(name, preset)
    action_log.append(
        "preset_capture",
        surface=surface,
        preset_name=preset["name"],
        scope=scope,
        target_path=str(path),
        project_path=result.get("project"),
    )
    result["written"] = True
    return result


def _bucket_for(preset: dict[str, Any], mode: str) -> list[dict[str, str]]:
    return preset[mode]


def add_entries(
    name: str,
    refs: list[str],
    mode: str = "enable",
    dry_run: bool = False,
    surface: str = "core",
) -> dict[str, Any]:
    resolved = resolve_skill_refs(refs)
    path = preset_path(name)
    if not resolved["ok"]:
        return {"ok": False, "dry_run": dry_run, "path": str(path), "errors": resolved["errors"]}
    preset = load_preset(name)
    bucket = _bucket_for(preset, mode)
    existing_ids = {entry["id"] for entry in bucket}
    for entry in resolved["entries"]:
        if entry["id"] not in existing_ids:
            bucket.append(entry)
            existing_ids.add(entry["id"])
    result = {"ok": True, "dry_run": dry_run, "preset": preset, "path": str(path), "added": resolved["entries"]}
    if dry_run:
        result["would_write"] = True
        return result
    write_preset(name, preset)
    action_log.append(
        "preset_add",
        surface=surface,
        preset_name=preset["name"],
        mode=mode,
        target_path=str(path),
        skill_ids=[entry["id"] for entry in resolved["entries"]],
    )
    result["written"] = True
    return result


def remove_entries(
    name: str,
    refs: list[str],
    mode: str = "enable",
    dry_run: bool = False,
    surface: str = "core",
) -> dict[str, Any]:
    resolved = resolve_skill_refs(refs)
    path = preset_path(name)
    if not resolved["ok"]:
        return {"ok": False, "dry_run": dry_run, "path": str(path), "errors": resolved["errors"]}
    preset = load_preset(name)
    bucket = _bucket_for(preset, mode)
    remove_ids = {entry["id"] for entry in resolved["entries"]}
    preset[mode] = [entry for entry in bucket if entry["id"] not in remove_ids]
    result = {"ok": True, "dry_run": dry_run, "preset": preset, "path": str(path), "removed": sorted(remove_ids)}
    if dry_run:
        result["would_write"] = True
        return result
    write_preset(name, preset)
    action_log.append(
        "preset_remove",
        surface=surface,
        preset_name=preset["name"],
        mode=mode,
        target_path=str(path),
        skill_ids=sorted(remove_ids),
    )
    result["written"] = True
    return result


def rename_preset(old_name: str, new_name: str, apply: bool = False, surface: str = "core") -> dict[str, Any]:
    old_path = preset_path(old_name)
    new_path = preset_path(new_name)
    result = {
        "ok": True,
        "dry_run": not apply,
        "from": str(old_path),
        "to": str(new_path),
    }
    if not old_path.exists():
        return {**result, "ok": False, "error": f"preset not found: {old_name}"}
    if new_path.exists():
        return {**result, "ok": False, "error": f"preset already exists: {new_name}"}
    if not apply:
        result["would_rename"] = True
        return result
    preset = load_preset(old_name)
    preset["name"] = store.slugify(new_name)
    write_preset(new_name, preset)
    old_path.unlink()
    action_log.append(
        "preset_rename",
        surface=surface,
        preset_name=preset["name"],
        old_name=store.slugify(old_name),
        target_path=str(new_path),
    )
    result["renamed"] = True
    return result


def delete_preset(name: str, apply: bool = False, surface: str = "core") -> dict[str, Any]:
    path = preset_path(name)
    result = {"ok": True, "dry_run": not apply, "path": str(path)}
    if not path.exists():
        return {**result, "ok": False, "error": f"preset not found: {name}"}
    if not apply:
        result["would_delete"] = True
        return result
    path.unlink()
    action_log.append("preset_delete", surface=surface, preset_name=store.slugify(name), target_path=str(path))
    result["deleted"] = True
    return result


def _all_preset_ids(preset: dict[str, Any]) -> list[str]:
    ids = []
    for _bucket_name, entries in _iter_buckets(preset):
        ids.extend(entry["id"] for entry in entries)
    return ids


def _entry_ids(entries: list[dict[str, str]]) -> list[str]:
    return [entry["id"] for entry in entries]


def _stamp_bucket(
    bucket: dict[str, Any],
    enable_ids: list[str],
    disable_ids: list[str],
    replace: bool,
    changes: list[dict[str, Any]],
    bucket_name: str,
) -> None:
    bucket.setdefault("enable", [])
    bucket.setdefault("disable", [])
    if replace:
        cleared = {"enable": list(bucket["enable"]), "disable": list(bucket["disable"])}
        bucket["enable"] = []
        bucket["disable"] = []
        changes.append({"bucket": bucket_name, "op": "clear", "before": cleared})
    for skill_id in enable_ids:
        if skill_id not in bucket["enable"]:
            bucket["enable"].append(skill_id)
            changes.append({"bucket": bucket_name, "op": "add_enable", "skill_id": skill_id})
        if skill_id in bucket["disable"]:
            bucket["disable"] = [value for value in bucket["disable"] if value != skill_id]
            changes.append({"bucket": bucket_name, "op": "remove_disable", "skill_id": skill_id})
    for skill_id in disable_ids:
        if skill_id not in bucket["disable"]:
            bucket["disable"].append(skill_id)
            changes.append({"bucket": bucket_name, "op": "add_disable", "skill_id": skill_id})
        if skill_id in bucket["enable"]:
            bucket["enable"] = [value for value in bucket["enable"] if value != skill_id]
            changes.append({"bucket": bucket_name, "op": "remove_enable", "skill_id": skill_id})


def apply_preset(
    name: str,
    scope: str,
    project: str | Path | None = None,
    client: str = "all",
    replace: bool = False,
    dry_run: bool = False,
    surface: str = "core",
) -> dict[str, Any]:
    preset = load_preset(name)
    managed = store.all_skills()
    unknown = sorted({skill_id for skill_id in _all_preset_ids(preset) if skill_id not in managed})
    manifest = store.load_manifest(scope, project=project)
    before = deepcopy(manifest)
    path = store.manifest_path(scope, project=project)
    result: dict[str, Any] = {
        "ok": not unknown,
        "dry_run": dry_run,
        "scope": scope,
        "client": client,
        "replace": replace,
        "preset": name,
        "manifest": str(path),
        "before": before,
    }
    if scope == "project":
        result["project"] = str(Path(project or Path.cwd()).expanduser().resolve())
    if unknown:
        result["errors"] = [{"type": "unknown_id", "skill_id": skill_id} for skill_id in unknown]
        result["after"] = before
        result["changes"] = []
        return result

    after = deepcopy(manifest)
    changes: list[dict[str, Any]] = []
    if client == "all":
        _stamp_bucket(after, _entry_ids(preset["enable"]), _entry_ids(preset["disable"]), replace, changes, "all")
        for specific_client in ("claude", "codex"):
            _stamp_bucket(
                after["clients"][specific_client],
                _entry_ids(preset["clients"][specific_client]["enable"]),
                _entry_ids(preset["clients"][specific_client]["disable"]),
                replace,
                changes,
                specific_client,
            )
    else:
        _stamp_bucket(
            after["clients"][client],
            _entry_ids(preset["enable"]) + _entry_ids(preset["clients"][client]["enable"]),
            _entry_ids(preset["disable"]) + _entry_ids(preset["clients"][client]["disable"]),
            replace,
            changes,
            client,
        )
    result["after"] = after
    result["changes"] = changes
    if dry_run:
        result["would_write"] = True
        return result
    store.save_manifest(scope, after, project=project)
    action_log.append(
        "preset_apply",
        surface=surface,
        preset_name=name,
        scope=scope,
        client=client,
        manifest_path=str(path),
        project_path=result.get("project"),
        replace=replace,
    )
    result["written"] = True
    return result


def load_preset(name: str) -> dict[str, Any]:
    path = preset_path(name)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("preset must be a JSON object")
    return normalize_preset(data, path.stem)


def _enrich_entry(entry: dict[str, str], managed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    skill_id = entry["id"]
    stored_alias = entry.get("alias") or ""
    meta = managed.get(skill_id)
    issues = []
    current_aliases = dict(meta.get("aliases", {})) if meta else {}
    if not meta:
        issues.append("unknown_id")
    elif stored_alias and stored_alias not in set(current_aliases.values()):
        issues.append("alias_drift")
    return {
        "id": skill_id,
        "stored_alias": stored_alias,
        "exists": bool(meta),
        "current_aliases": current_aliases,
        "issues": issues,
    }


def show_preset(name: str) -> dict[str, Any]:
    preset = load_preset(name)
    managed = store.all_skills()
    result: dict[str, Any] = {
        "version": preset["version"],
        "name": preset["name"],
        "description": preset["description"],
        "tags": preset["tags"],
        "enable": [_enrich_entry(entry, managed) for entry in preset["enable"]],
        "disable": [_enrich_entry(entry, managed) for entry in preset["disable"]],
        "clients": {},
        "issues": [],
    }
    for client in ("claude", "codex"):
        result["clients"][client] = {
            "enable": [_enrich_entry(entry, managed) for entry in preset["clients"][client]["enable"]],
            "disable": [_enrich_entry(entry, managed) for entry in preset["clients"][client]["disable"]],
        }
    for bucket in (result["enable"], result["disable"]):
        for entry in bucket:
            result["issues"].extend(entry["issues"])
    for client in ("claude", "codex"):
        for mode in ("enable", "disable"):
            for entry in result["clients"][client][mode]:
                result["issues"].extend(entry["issues"])
    result["issues"] = sorted(set(result["issues"]))
    return result


def _raw_bucket(data: dict[str, Any], client: str | None, mode: str) -> Any:
    if client is None:
        return data.get(mode, [])
    clients = data.get("clients", {})
    if not isinstance(clients, dict):
        return []
    client_data = clients.get(client, {})
    if not isinstance(client_data, dict):
        return []
    return client_data.get(mode, [])


def _iter_buckets(preset: dict[str, Any]) -> list[tuple[str, list[dict[str, str]]]]:
    buckets = [("enable", preset["enable"]), ("disable", preset["disable"])]
    for client in ("claude", "codex"):
        buckets.append((f"{client}:enable", preset["clients"][client]["enable"]))
        buckets.append((f"{client}:disable", preset["clients"][client]["disable"]))
    return buckets


def _schema_issues(path: Path, data: Any) -> list[dict[str, Any]]:
    issues = []
    location = f"preset:{path.stem}"
    if not isinstance(data, dict):
        return [
            {
                "location": location,
                "type": "preset_malformed",
                "path": str(path),
                "message": "preset must be a JSON object",
            }
        ]
    for client in (None, "claude", "codex"):
        for mode in ("enable", "disable"):
            bucket = _raw_bucket(data, client, mode)
            bucket_name = mode if client is None else f"{client}:{mode}"
            if not isinstance(bucket, list):
                issues.append(
                    {
                        "location": location,
                        "type": "preset_malformed",
                        "path": str(path),
                        "bucket": bucket_name,
                        "message": "bucket must be a list",
                    }
                )
                continue
            for index, entry in enumerate(bucket):
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    issues.append(
                        {
                            "location": location,
                            "type": "preset_malformed",
                            "path": str(path),
                            "bucket": bucket_name,
                            "index": index,
                            "message": "entry must be an object with string id",
                        }
                    )
    return issues


def validate_presets() -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    managed = store.all_skills()
    root = store.presets_root()
    if not root.exists():
        return issues
    for path in sorted(root.glob("*.json")):
        location = f"preset:{path.stem}"
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:
            issues.append({"location": location, "type": "preset_malformed", "path": str(path), "message": str(exc)})
            continue
        schema_issues = _schema_issues(path, raw)
        if schema_issues:
            issues.extend(schema_issues)
            continue
        preset = normalize_preset(raw, path.stem)
        for bucket_name, entries in _iter_buckets(preset):
            seen: set[str] = set()
            for entry in entries:
                skill_id = entry["id"]
                if skill_id in seen:
                    issues.append(
                        {
                            "location": location,
                            "type": "preset_duplicate_entry",
                            "path": str(path),
                            "bucket": bucket_name,
                            "skill_id": skill_id,
                        }
                    )
                seen.add(skill_id)
                meta = managed.get(skill_id)
                if not meta:
                    issues.append(
                        {
                            "location": location,
                            "type": "preset_unknown_id",
                            "path": str(path),
                            "bucket": bucket_name,
                            "skill_id": skill_id,
                        }
                    )
                    continue
                stored_alias = entry.get("alias") or ""
                current_aliases = set(meta.get("aliases", {}).values())
                if stored_alias and stored_alias not in current_aliases:
                    issues.append(
                        {
                            "location": location,
                            "type": "preset_alias_drift",
                            "path": str(path),
                            "bucket": bucket_name,
                            "skill_id": skill_id,
                            "stored_alias": stored_alias,
                            "current_aliases": sorted(current_aliases),
                        }
                    )
    return issues
