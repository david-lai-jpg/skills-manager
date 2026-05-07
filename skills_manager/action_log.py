"""Shared JSONL action log for applied mutations."""
from __future__ import annotations

import json
import time
from typing import Any

from . import store


def actions_path():
    return store.logs_root() / "actions.jsonl"


def append(action: str, **fields: Any) -> dict[str, Any]:
    entry = {"time": time.time(), "action": action, **fields}
    path = actions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry
