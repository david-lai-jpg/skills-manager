"""Import, adopt, and migration planning."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import adapters, scanner, store


def _meta(skill_id: str, alias: str, compatibility: dict[str, bool], source_paths: list[str], content_hash: str) -> dict[str, Any]:
    return {
        "id": skill_id,
        "aliases": {"claude": alias, "codex": alias},
        "compatibility": compatibility,
        "source_paths": source_paths,
        "content_hash": content_hash,
    }


def choose_skill_id(alias: str, digest: str, preferred_suffix: str | None = None) -> str:
    base = store.stable_id(alias, preferred_suffix)
    existing = store.load_skill_meta(base)
    if not existing:
        return base
    if existing.get("content_hash") == digest:
        return base
    if preferred_suffix:
        return store.stable_id(alias, f"{preferred_suffix}-{digest[:8]}")
    return store.stable_id(alias, f"fork{digest[:8]}")


def adopt_skill(
    src: str | Path,
    dry_run: bool = False,
    suffix: str | None = None,
    compatibility: dict[str, bool] | None = None,
) -> dict[str, Any]:
    store.ensure_store()
    src_path = Path(src).expanduser().resolve()
    if not store.is_skill_dir(src_path):
        return {"ok": False, "error": f"not a skill directory with SKILL.md: {src_path}"}
    alias = store.slugify(src_path.name)
    digest = store.content_hash(src_path)
    skill_id = choose_skill_id(alias, digest, suffix)
    dst = store.id_to_dir(skill_id)
    comp = compatibility or {"claude": True, "codex": True}
    meta = _meta(skill_id, alias, comp, [str(src_path)], digest)
    result = {
        "ok": True,
        "skill_id": skill_id,
        "alias": alias,
        "source": str(src_path),
        "target": str(dst),
        "content_hash": digest,
        "dry_run": dry_run,
    }
    if dry_run:
        result["would_copy"] = not dst.exists()
        return result
    if dst.exists():
        existing_hash = store.content_hash(dst) if store.is_skill_dir(dst) else ""
        if existing_hash != digest:
            return {"ok": False, "error": f"target exists with different content: {dst}"}
    else:
        store.copy_skill_tree(src_path, dst)
    existing_meta = store.load_skill_meta(skill_id) or meta
    source_paths = sorted(set(existing_meta.get("source_paths", []) + [str(src_path)]))
    existing_meta.update(meta)
    existing_meta["source_paths"] = source_paths
    store.write_skill_meta(skill_id, existing_meta)
    return result


def import_inbox(dry_run: bool = True) -> dict[str, Any]:
    entries = scanner.scan_dir(store.inbox_dir()).get("entries", [])
    managed_hashes = {m.get("content_hash") for m in store.all_skills().values()}
    candidates = []
    for entry in entries:
        if entry.get("type") not in {"skill_dir", "symlink_skill"}:
            continue
        if entry.get("content_hash") in managed_hashes:
            continue
        candidates.append(entry)
    if dry_run:
        return {"dry_run": True, "candidates": candidates, "message": f"{len(candidates)} unmanaged inbox skill(s) detected"}
    adopted = [adopt_skill(c["path"], dry_run=False) for c in candidates]
    return {"dry_run": False, "adopted": adopted}


def migrate_plan() -> dict[str, Any]:
    locations = {
        "claude": adapters.adapter("claude").global_dir(),
        "codex": adapters.adapter("codex").global_dir(),
    }
    found: dict[str, list[dict[str, Any]]] = {}
    for client, path in locations.items():
        for entry in scanner.scan_dir(path).get("entries", []):
            if entry.get("type") in {"skill_dir", "symlink_skill"}:
                e = dict(entry)
                e["client"] = client
                e["alias"] = store.slugify(entry["name"])
                found.setdefault(e["alias"], []).append(e)
    actions = []
    for alias, entries in sorted(found.items()):
        hashes = {e["content_hash"] for e in entries}
        if len(hashes) == 1:
            digest = next(iter(hashes))
            clients = {e["client"] for e in entries}
            actions.append(
                {
                    "kind": "merge" if len(entries) > 1 else "copy",
                    "alias": alias,
                    "skill_id": choose_skill_id(alias, digest),
                    "sources": [e["path"] for e in entries],
                    "compatibility": {"claude": "claude" in clients, "codex": "codex" in clients},
                    "content_hash": digest,
                }
            )
        else:
            for e in entries:
                actions.append(
                    {
                        "kind": "fork",
                        "alias": alias,
                        "skill_id": choose_skill_id(alias, e["content_hash"], e["client"]),
                        "sources": [e["path"]],
                        "compatibility": {"claude": e["client"] == "claude", "codex": e["client"] == "codex"},
                        "content_hash": e["content_hash"],
                        "client": e["client"],
                    }
                )
    return {"actions": actions}


def migrate_apply() -> dict[str, Any]:
    plan = migrate_plan()
    results = []
    for action in plan["actions"]:
        src = action["sources"][0]
        suffix = action.get("client") if action["kind"] == "fork" else None
        result = adopt_skill(src, dry_run=False, suffix=suffix, compatibility=action["compatibility"])
        results.append(result)
        skill_id = result.get("skill_id") or action["skill_id"]
        meta = store.load_skill_meta(skill_id)
        if meta:
            meta["source_paths"] = sorted(set(meta.get("source_paths", []) + action["sources"]))
            meta["compatibility"] = action["compatibility"]
            store.write_skill_meta(skill_id, meta)
    return {"applied": results, "plan": plan}
