"""Client-specific skill directory adapters."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import store

CLIENTS = ("claude", "codex")


@dataclass(frozen=True)
class ClientAdapter:
    client: str

    def global_dir(self) -> Path:
        if self.client == "claude":
            return store.home() / ".claude" / "skills"
        if self.client == "codex":
            codex_home = os.environ.get("CODEX_HOME")
            base = Path(codex_home).expanduser() if codex_home else store.home() / ".codex"
            return base / "skills"
        raise ValueError(f"unknown client: {self.client}")

    def project_dir(self, project: str | Path) -> Path:
        base = Path(project).expanduser().resolve()
        if self.client == "claude":
            return base / ".claude" / "skills"
        if self.client == "codex":
            return base / ".codex" / "skills"
        raise ValueError(f"unknown client: {self.client}")

    def rendered_dir(self, project: str | Path | None = None) -> Path:
        return self.project_dir(project) if project else self.global_dir()


def expand_clients(client: str) -> list[str]:
    if client == "all":
        return list(CLIENTS)
    if client not in CLIENTS:
        raise ValueError(f"unknown client: {client}")
    return [client]


def adapter(client: str) -> ClientAdapter:
    return ClientAdapter(client)
