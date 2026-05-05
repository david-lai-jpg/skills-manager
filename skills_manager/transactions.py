"""Transaction journaling and rollback."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from . import store


def transactions_path(tx_id: str) -> Path:
    return store.transactions_root() / f"{tx_id}.json"


def new_transaction(kind: str, actions: list[dict[str, Any]]) -> dict[str, Any]:
    store.ensure_store()
    tx_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    tx = {"id": tx_id, "kind": kind, "status": "planned", "created_at": time.time(), "actions": actions}
    store.write_json(transactions_path(tx_id), tx)
    return tx


def mark(tx: dict[str, Any], status: str, error: str | None = None) -> None:
    tx["status"] = status
    tx["updated_at"] = time.time()
    if error:
        tx["error"] = error
    store.write_json(transactions_path(tx["id"]), tx)


def is_manager_created(path: Path) -> bool:
    if path.is_symlink():
        return store.path_under(path.resolve(strict=False), store.skills_root())
    marker = path / ".skills-manager.json"
    if path.is_dir() and marker.exists():
        data = store.read_json(marker, {})
        return data.get("manager") == "skills-manager"
    return False


def remove_manager_created(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not is_manager_created(path):
        raise RuntimeError(f"refusing to remove unmanaged path: {path}")
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def restore_link(action: dict[str, Any]) -> None:
    target = Path(action["target"])
    source = Path(action["source"])
    if target.exists() or target.is_symlink():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)


def rollback(tx_id: str) -> dict[str, Any]:
    path = transactions_path(tx_id)
    if not path.exists():
        return {"ok": False, "error": f"transaction not found: {tx_id}"}
    tx = store.read_json(path, {})
    actions = list(reversed(tx.get("actions", [])))
    results = []
    for action in actions:
        op = action.get("op")
        target = Path(action.get("target", ""))
        try:
            if op in {"create_symlink", "create_copy"}:
                remove_manager_created(target)
                results.append({"op": "removed", "target": str(target)})
            elif op == "remove_rendered":
                restore_link(action)
                results.append({"op": "restored", "target": str(target)})
        except Exception as exc:
            results.append({"op": "error", "target": str(target), "error": str(exc)})
    tx["status"] = "rolled_back"
    tx["rolled_back_at"] = time.time()
    store.write_json(path, tx)
    return {"ok": True, "transaction": tx_id, "results": results}
