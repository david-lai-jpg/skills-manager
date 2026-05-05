"""Path, manifest, metadata, and hashing helpers for skills-manager."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

VERSION = 1
OWNER_PREFIX = "skill.davidl"
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules", ".venv", "venv"}
SKIP_FILES = {".DS_Store", "skill.json", ".skills-manager.json"}


def home() -> Path:
    return Path(os.environ.get("SKILLS_MANAGER_HOME") or os.environ.get("HOME", "~")).expanduser()


def agents_dir() -> Path:
    return home() / ".agents"


def inbox_dir() -> Path:
    return agents_dir() / "skills"


def store_root() -> Path:
    return Path(os.environ.get("SKILLS_MANAGER_STORE") or agents_dir() / "skills-store").expanduser()


def skills_root() -> Path:
    return store_root() / "skills"


def manifests_root() -> Path:
    return store_root() / "manifests"


def transactions_root() -> Path:
    return store_root() / "transactions"


def ensure_store() -> None:
    for path in (skills_root(), manifests_root(), transactions_root()):
        path.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "unnamed"


def stable_id(slug: str, suffix: str | None = None) -> str:
    parts = [OWNER_PREFIX, slugify(slug)]
    if suffix:
        parts.append(slugify(suffix))
    return ".".join(parts)


def id_to_dir(skill_id: str) -> Path:
    return skills_root() / skill_id


def is_skill_dir(path: Path) -> bool:
    try:
        return path.is_dir() and (path / "SKILL.md").is_file()
    except OSError:
        return False


def iter_hash_files(root: Path) -> Iterable[Path]:
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for filename in sorted(filenames):
            if filename in SKIP_FILES:
                continue
            path = Path(dirpath) / filename
            if path.is_symlink():
                continue
            yield path


def content_hash(root: Path) -> str:
    h = hashlib.sha256()
    root_resolved = root.resolve()
    for path in iter_hash_files(root_resolved):
        rel = path.relative_to(root_resolved).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()


def copy_skill_tree(src: Path, dst: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SKIP_DIRS or name == ".DS_Store"}

    shutil.copytree(src, dst, ignore=ignore)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def manifest_template(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": VERSION,
        "inherit": True,
        "enable": [],
        "disable": [],
        "clients": {
            "claude": {"enable": [], "disable": []},
            "codex": {"enable": [], "disable": []},
        },
    }
    data.update(extra)
    return data


def project_key(project: str | Path) -> str:
    resolved = str(Path(project).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def manifest_path(
    scope: str,
    project: str | Path | None = None,
    profile: str | None = None,
    session: str | None = None,
) -> Path:
    root = manifests_root()
    if scope == "global":
        return root / "global.json"
    if scope == "profile":
        name = slugify(profile or os.environ.get("SKILLS_MANAGER_PROFILE") or "default")
        return root / "profiles" / f"{name}.json"
    if scope == "project":
        base = Path(project or os.getcwd()).expanduser().resolve()
        return root / "projects" / f"{project_key(base)}.json"
    if scope == "session":
        name = slugify(session or os.environ.get("SKILLS_MANAGER_SESSION") or "default")
        return root / "sessions" / f"{name}.json"
    raise ValueError(f"unknown scope: {scope}")


def load_manifest(
    scope: str,
    project: str | Path | None = None,
    profile: str | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    path = manifest_path(scope, project=project, profile=profile, session=session)
    data = read_json(path, manifest_template())
    base = manifest_template()
    base.update({k: v for k, v in data.items() if k != "clients"})
    for client in ("claude", "codex"):
        client_data = data.get("clients", {}).get(client, {})
        base["clients"][client]["enable"] = list(client_data.get("enable", []))
        base["clients"][client]["disable"] = list(client_data.get("disable", []))
    if scope == "project":
        base.setdefault("project_path", str(Path(project or os.getcwd()).expanduser().resolve()))
    return base


def save_manifest(
    scope: str,
    manifest: dict[str, Any],
    project: str | Path | None = None,
    profile: str | None = None,
    session: str | None = None,
) -> Path:
    path = manifest_path(scope, project=project, profile=profile, session=session)
    write_json(path, manifest)
    return path


def load_skill_meta(skill_id: str) -> dict[str, Any] | None:
    path = id_to_dir(skill_id) / "skill.json"
    if not path.exists():
        return None
    return read_json(path, {})


def all_skills() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = skills_root()
    if not root.exists():
        return result
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        meta = read_json(child / "skill.json", {}) if (child / "skill.json").exists() else {}
        if not meta:
            meta = {
                "id": child.name,
                "aliases": {"claude": child.name, "codex": child.name},
                "compatibility": {"claude": True, "codex": True},
                "source_paths": [],
                "content_hash": content_hash(child) if is_skill_dir(child) else "",
            }
        result[meta.get("id", child.name)] = meta
    return result


def write_skill_meta(skill_id: str, meta: dict[str, Any]) -> None:
    write_json(id_to_dir(skill_id) / "skill.json", meta)


def marker_data(skill_id: str) -> dict[str, Any]:
    return {"manager": "skills-manager", "version": VERSION, "skill_id": skill_id}


def path_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False
