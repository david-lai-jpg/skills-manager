"""Thin stdlib curses TUI shell and pure state helpers."""
from __future__ import annotations

import curses
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import action_log, adapters, backup, materializer, planner, presets, resolver, scanner, store, transactions


CLIENT_MODES = ("all", "claude", "codex")
DEFAULT_ITEMS = [
    "Managed skills",
    "Presets",
    "Global configuration",
    "Project configuration",
    "Diff",
    "Materialize",
    "Doctor",
    "Backup / restore",
    "Rollback / transactions",
    "Action log",
]


@dataclass(frozen=True)
class TuiState:
    items: list[str]
    selected_index: int = 0
    filter_text: str = ""
    client_mode: str = "all"
    status: str = "q quits · / filters · tab changes client"
    mode: str = "dashboard"

    def filtered_items(self) -> list[str]:
        if not self.filter_text:
            return list(self.items)
        needle = self.filter_text.lower()
        return [item for item in self.items if needle in item.lower()]

    def selected_item(self) -> str | None:
        items = self.filtered_items()
        if not items:
            return None
        return items[min(self.selected_index, len(items) - 1)]


def _clamp(index: int, count: int) -> int:
    if count <= 0:
        return 0
    return max(0, min(index, count - 1))


def move_selection(state: TuiState, delta: int) -> TuiState:
    count = len(state.filtered_items())
    return replace(state, selected_index=_clamp(state.selected_index + delta, count))


def apply_filter(state: TuiState, text: str) -> TuiState:
    next_state = replace(state, filter_text=text, selected_index=0)
    return replace(next_state, selected_index=_clamp(next_state.selected_index, len(next_state.filtered_items())))


def cycle_client_mode(state: TuiState) -> TuiState:
    index = CLIENT_MODES.index(state.client_mode)
    return replace(state, client_mode=CLIENT_MODES[(index + 1) % len(CLIENT_MODES)])


def command_hint(item: str | None) -> str:
    hints = {
        "Managed skills": "skills-manager state --client all --json",
        "Presets": "skills-manager preset list",
        "Global configuration": "skills-manager state --client all --json",
        "Project configuration": "skills-manager state --client all --project PATH --json",
        "Diff": "skills-manager diff --client all",
        "Materialize": "skills-manager materialize --client all --dry-run",
        "Doctor": "skills-manager doctor",
        "Backup / restore": "skills-manager backup --dry-run",
        "Rollback / transactions": "skills-manager rollback <transaction-id>",
        "Action log": "cat ~/.agents/skills-store/logs/actions.jsonl",
    }
    return hints.get(item or "", "")


def is_first_run() -> bool:
    return not store.skills_root().exists() or not any(store.skills_root().iterdir())


def initial_tui_state() -> TuiState:
    if is_first_run():
        return TuiState(
            items=["First-run wizard", *DEFAULT_ITEMS],
            status="first run · scan, backup, intake, choose skills, materialize, doctor",
            mode="first_run",
        )
    return TuiState(items=DEFAULT_ITEMS, mode="dashboard")


def _selected_manifest_bucket(manifest: dict[str, Any], client: str) -> dict[str, Any]:
    if client == "all":
        return manifest
    client_bucket = manifest.get("clients", {}).get(client, {"enable": [], "disable": []})
    return {
        "enable": list(manifest.get("enable", [])) + list(client_bucket.get("enable", [])),
        "disable": list(manifest.get("disable", [])) + list(client_bucket.get("disable", [])),
    }


def _resolve_client(client: str) -> str:
    return "claude" if client == "all" else client


def _materialize_hint(client: str, project: str | Path | None) -> str:
    command = f"skills-manager materialize --client {client}"
    if project is not None:
        command += f" --project {Path(project).expanduser().resolve()}"
    return command


def build_desired_state_view(
    scope: str,
    client: str,
    project: str | Path | None = None,
    show_incompatible: bool = False,
    filter_text: str = "",
) -> dict[str, Any]:
    manifest = store.load_manifest(scope, project=project)
    direct_bucket = _selected_manifest_bucket(manifest, client)
    effective = resolver.resolve(_resolve_client(client), project=project if scope == "project" else None)
    rows = []
    needle = filter_text.lower()
    for skill_id, info in effective["skills"].items():
        if not show_incompatible and not info.get("compatible", False):
            continue
        alias = info.get("alias") or skill_id.split(".")[-1]
        if needle and needle not in alias.lower() and needle not in skill_id.lower():
            continue
        direct = "none"
        if skill_id in direct_bucket.get("enable", []):
            direct = "enable"
        if skill_id in direct_bucket.get("disable", []):
            direct = "disable"
        rows.append(
            {
                "id": skill_id,
                "alias": alias,
                "enabled": bool(info.get("enabled")),
                "compatible": bool(info.get("compatible")),
                "direct": direct,
                "reasons": list(info.get("reasons", [])),
            }
        )
    rows.sort(key=lambda row: row["alias"])
    result = {
        "scope": scope,
        "client": client,
        "rows": rows,
        "direct_entries": {
            "enable": list(direct_bucket.get("enable", [])),
            "disable": list(direct_bucket.get("disable", [])),
        },
        "needs_materialize": False,
        "materialize_hint": _materialize_hint(client, project if scope == "project" else None),
    }
    if scope == "project":
        result["project"] = str(Path(project or Path.cwd()).expanduser().resolve())
    return result


def _edit_manifest_bucket(manifest: dict[str, Any], client: str) -> dict[str, Any]:
    if client == "all":
        return manifest
    return manifest.setdefault("clients", {}).setdefault(client, {"enable": [], "disable": []})


def apply_desired_state_edit(
    scope: str,
    skill_id: str,
    action: str,
    client: str = "all",
    project: str | Path | None = None,
) -> dict[str, Any]:
    if action in {"enable", "disable"}:
        resolver.set_skill(scope, skill_id, action == "enable", client=client, project=project, surface="tui")
    elif action == "remove_override":
        manifest = store.load_manifest(scope, project=project)
        bucket = _edit_manifest_bucket(manifest, client)
        bucket["enable"] = [value for value in bucket.get("enable", []) if value != skill_id]
        bucket["disable"] = [value for value in bucket.get("disable", []) if value != skill_id]
        path = store.save_manifest(scope, manifest, project=project)
        action_log.append(
            "remove_override",
            surface="tui",
            scope=scope,
            client=client,
            skill_id=skill_id,
            manifest_path=str(path),
            project_path=str(Path(project).expanduser().resolve()) if project is not None else None,
        )
    else:
        raise ValueError(f"unknown desired-state edit action: {action}")
    view = build_desired_state_view(scope, client, project=project)
    view["needs_materialize"] = True
    return view


def build_preset_manager_view(filter_text: str = "", selected_name: str | None = None) -> dict[str, Any]:
    needle = filter_text.lower()
    names = [name for name in presets.list_presets() if not needle or needle in name.lower()]
    selected = selected_name if selected_name in names else names[0] if names else None
    details = presets.show_preset(selected) if selected else None
    command = f"skills-manager preset show {selected}" if selected else "skills-manager preset list"
    return {
        "filter": filter_text,
        "names": names,
        "selected": details,
        "cli_command": command,
    }


def confirmation_for(action: str, replace: bool = False) -> str:
    if action == "preset_delete" or (action == "preset_apply" and replace):
        return "typed"
    return "single_key"


def _quote(value: str) -> str:
    return shlex.quote(value)


def preview_preset_create(name: str, description: str = "", tags: list[str] | None = None) -> dict[str, Any]:
    tag_args = " ".join(f"--tag {_quote(tag)}" for tag in tags or [])
    command = f"skills-manager preset create {_quote(name)}"
    if description:
        command += f" --description {_quote(description)}"
    if tag_args:
        command += f" {tag_args}"
    command += " --dry-run"
    return {
        "action": "preset_create",
        "confirmation": confirmation_for("preset_create"),
        "result": presets.create_preset(name, description=description, tags=tags, dry_run=True),
        "cli_command": command,
    }


def preview_preset_capture(name: str, scope: str, project: str | Path | None = None) -> dict[str, Any]:
    command = f"skills-manager preset create {_quote(name)} --from-scope {scope}"
    if project is not None:
        command += f" --project {_quote(str(Path(project).expanduser().resolve()))}"
    command += " --dry-run"
    return {
        "action": "preset_capture",
        "confirmation": confirmation_for("preset_capture"),
        "result": presets.capture_preset(name, scope, project=project, dry_run=True),
        "cli_command": command,
    }


def preview_preset_edit(action: str, name: str, refs: list[str], mode: str = "enable") -> dict[str, Any]:
    if action not in {"add", "remove"}:
        raise ValueError(f"unknown preset edit action: {action}")
    ref_text = " ".join(_quote(ref) for ref in refs)
    command = f"skills-manager preset {action} {_quote(name)} {ref_text}"
    if mode != "enable":
        command += f" --mode {mode}"
    command += " --dry-run"
    if action == "add":
        result = presets.add_entries(name, refs, mode=mode, dry_run=True)
    else:
        result = presets.remove_entries(name, refs, mode=mode, dry_run=True)
    return {
        "action": f"preset_{action}",
        "confirmation": confirmation_for(f"preset_{action}"),
        "result": result,
        "cli_command": command,
    }


def preview_preset_apply(
    name: str,
    scope: str,
    project: str | Path | None = None,
    client: str = "all",
    replace: bool = False,
) -> dict[str, Any]:
    command = f"skills-manager preset apply {_quote(name)} --scope {scope}"
    if project is not None:
        command += f" --project {_quote(str(Path(project).expanduser().resolve()))}"
    if client != "all":
        command += f" --client {client}"
    if replace:
        command += " --replace"
    command += " --dry-run"
    return {
        "action": "preset_apply",
        "confirmation": confirmation_for("preset_apply", replace=replace),
        "result": presets.apply_preset(name, scope, project=project, client=client, replace=replace, dry_run=True),
        "cli_command": command,
    }


def preview_preset_delete(name: str) -> dict[str, Any]:
    return {
        "action": "preset_delete",
        "confirmation": confirmation_for("preset_delete"),
        "result": presets.delete_preset(name, apply=False),
        "cli_command": f"skills-manager preset delete {_quote(name)}",
    }


def preview_preset_rename(old_name: str, new_name: str) -> dict[str, Any]:
    return {
        "action": "preset_rename",
        "confirmation": confirmation_for("preset_rename"),
        "result": presets.rename_preset(old_name, new_name, apply=False),
        "cli_command": f"skills-manager preset rename {_quote(old_name)} {_quote(new_name)}",
    }


def build_render_view(client: str, project: str | Path | None = None) -> dict[str, Any]:
    diff = materializer.diff(client, project=project)
    command = f"skills-manager materialize --client {client}"
    if project is not None:
        command += f" --project {_quote(str(Path(project).expanduser().resolve()))}"
    command += " --dry-run"
    view = {
        "client": client,
        "project": str(Path(project).expanduser().resolve()) if project is not None else None,
        "rendered_dir": diff["rendered_dir"],
        "creates": diff["creates"],
        "removes": diff["removes"],
        "conflicts": diff["conflicts"],
        "desired": diff["desired"],
        "actual": diff["actual"],
        "confirmation": confirmation_for("materialize"),
        "cli_command": command,
    }
    return view


def build_doctor_view(project: str | Path | None = None) -> dict[str, Any]:
    scan = scanner.scan(project=project)
    issues = []
    for loc_name, loc in scan["locations"].items():
        for entry in loc.get("entries", []):
            if entry.get("type") in {"broken_symlink", "missing_skill_md", "error"}:
                issues.append({"location": loc_name, **entry})
    for client in adapters.CLIENTS:
        d = materializer.diff(client, project=project)
        for conflict in d["conflicts"]:
            issues.append({"location": f"{client}_rendered", "type": "conflict", **conflict})
    issues.extend(presets.validate_presets())
    return {
        "ok": not issues,
        "issues": issues,
        "store": str(store.store_root()),
        "inbox": str(store.inbox_dir()),
        "project": str(Path(project).expanduser().resolve()) if project is not None else None,
        "cli_command": "skills-manager doctor" + (f" --project {_quote(str(Path(project).expanduser().resolve()))}" if project is not None else ""),
    }


def apply_materialize_preview(preview: dict[str, Any]) -> dict[str, Any]:
    if preview.get("conflicts"):
        return {
            "ok": False,
            "error": "unmanaged or mismatched rendered conflicts",
            "preview": preview,
            "doctor": build_doctor_view(project=preview.get("project")),
        }
    client = preview["client"]
    project = preview.get("project")
    result = materializer.materialize(client, project=project, dry_run=False, surface="tui")
    result["doctor"] = build_doctor_view(project=project)
    result["note"] = "Restart Codex or start a new Codex session for skill visibility." if client == "codex" else ""
    return result


def build_rollback_view() -> dict[str, Any]:
    txs = []
    root = store.transactions_root()
    if root.exists():
        for path in sorted(root.glob("*.json"), reverse=True):
            tx = store.read_json(path, {})
            if tx.get("kind") == "materialize":
                txs.append(
                    {
                        "id": tx.get("id", path.stem),
                        "status": tx.get("status"),
                        "created_at": tx.get("created_at"),
                        "actions": tx.get("actions", []),
                    }
                )
    return {"transactions": txs, "cli_command": "skills-manager rollback <transaction-id>"}


def preview_rollback(transaction_id: str) -> dict[str, Any]:
    path = transactions.transactions_path(transaction_id)
    tx = store.read_json(path, {}) if path.exists() else None
    return {
        "action": "rollback",
        "confirmation": "typed",
        "transaction": tx,
        "cli_command": f"skills-manager rollback {_quote(transaction_id)}",
    }


def apply_rollback_preview(preview: dict[str, Any]) -> dict[str, Any]:
    tx = preview.get("transaction") or {}
    tx_id = tx.get("id")
    if not tx_id:
        return {"ok": False, "error": "transaction not found"}
    result = transactions.rollback(tx_id)
    result["doctor"] = build_doctor_view()
    return result


def build_scan_view(project: str | Path | None = None) -> dict[str, Any]:
    data = scanner.scan(project=project)
    issues = []
    for loc_name, loc in data["locations"].items():
        for entry in loc.get("entries", []):
            if entry.get("type") in {"broken_symlink", "missing_skill_md", "error"}:
                issues.append({"location": loc_name, **entry})
    command = "skills-manager scan --json"
    if project is not None:
        command += f" --project {_quote(str(Path(project).expanduser().resolve()))}"
    return {
        "locations": data["locations"],
        "issues": issues,
        "duplicates": data.get("duplicates", {}),
        "project": str(Path(project).expanduser().resolve()) if project is not None else None,
        "cli_command": command,
    }


def preview_import_inbox() -> dict[str, Any]:
    return {
        "action": "import",
        "confirmation": confirmation_for("import"),
        "result": planner.import_inbox(dry_run=True),
        "cli_command": "skills-manager import --dry-run",
        "backup_hint": "skills-manager backup --dry-run",
        "doctor_after": False,
    }


def apply_import_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = planner.import_inbox(dry_run=False)
    result["doctor"] = build_doctor_view()
    result["preview"] = preview
    return result


def preview_adopt_path(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    return {
        "action": "adopt",
        "confirmation": confirmation_for("adopt"),
        "result": planner.adopt_skill(resolved, dry_run=True),
        "cli_command": f"skills-manager adopt {_quote(str(resolved))}",
    }


def preview_migrate() -> dict[str, Any]:
    return {
        "action": "migrate",
        "confirmation": "typed",
        "result": planner.migrate_plan(),
        "cli_command": "skills-manager migrate --dry-run",
        "backup_hint": "skills-manager backup --dry-run",
        "doctor_after": True,
    }


def apply_migrate_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = planner.migrate_apply()
    result["doctor"] = build_doctor_view()
    result["preview"] = preview
    return result


def preview_backup(export_path: str | Path) -> dict[str, Any]:
    resolved = Path(export_path).expanduser().resolve()
    return {
        "action": "backup",
        "confirmation": confirmation_for("backup"),
        "result": backup.dry_run_export(resolved),
        "cli_command": f"skills-manager backup --export {_quote(str(resolved))}",
    }


def apply_backup_preview(preview: dict[str, Any]) -> dict[str, Any]:
    target = preview["result"]["target"]
    export_path = Path(target)
    if export_path.name == "agent-skills-backup":
        export_path = export_path.parent
    result = backup.export(export_path)
    result["preview"] = preview
    return result


def preview_restore(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    return {
        "action": "restore",
        "confirmation": "typed",
        "result": backup.restore(resolved, dry_run=True),
        "cli_command": f"skills-manager restore --from {_quote(str(resolved))} --dry-run",
    }


def apply_restore_preview(preview: dict[str, Any]) -> dict[str, Any]:
    backup_path = preview["result"]["plan"]["backup"]
    result = backup.restore(backup_path, dry_run=False)
    result["doctor"] = build_doctor_view()
    result["after"] = result.get("plan", {}).get("after", [])
    result["preview"] = preview
    return result


def build_first_run_wizard_state(project: str | Path | None = None) -> dict[str, Any]:
    scan_view = build_scan_view(project=project)
    import_preview = preview_import_inbox()
    migrate_preview = preview_migrate()
    has_intake = bool(import_preview["result"].get("candidates")) or bool(migrate_preview["result"].get("actions"))
    return {
        "step": "scan",
        "scan": scan_view,
        "import_preview": import_preview,
        "migrate_preview": migrate_preview,
        "backup_hint": "skills-manager backup --dry-run",
        "needs_backup": has_intake,
        "needs_materialize": False,
        "dashboard_after_completion": True,
    }


def first_run_select_global_skills(skill_ids: list[str], client: str = "all") -> dict[str, Any]:
    for skill_id in skill_ids:
        resolver.set_skill("global", skill_id, True, client=client, surface="tui")
    view = build_desired_state_view("global", client)
    view["needs_materialize"] = True
    view["step"] = "initial_selection"
    return view


def first_run_preview_preset(name: str, client: str = "all") -> dict[str, Any]:
    preview = preview_preset_apply(name, "global", client=client)
    preview["step"] = "initial_selection"
    return preview


def first_run_preview_materialize(client: str = "all", project: str | Path | None = None) -> dict[str, Any]:
    preview = build_render_view(client, project=project)
    preview["step"] = "materialize_preview"
    preview["needs_materialize"] = bool(preview["creates"] or preview["removes"])
    return preview


def first_run_complete_materialize(preview: dict[str, Any]) -> dict[str, Any]:
    materialized = apply_materialize_preview(preview)
    return {
        "step": "complete",
        "materialize": materialized,
        "doctor": materialized.get("doctor", build_doctor_view(project=preview.get("project"))),
        "next_state": TuiState(items=DEFAULT_ITEMS, mode="dashboard"),
        "needs_materialize": False,
    }


def render_lines(state: TuiState) -> list[str]:
    lines = [
        "skills-manager",
        f"client: {state.client_mode}    filter: {state.filter_text or 'none'}",
        state.status,
        "",
    ]
    items = state.filtered_items()
    if not items:
        lines.append("No matching items.")
    for index, item in enumerate(items):
        marker = ">" if index == state.selected_index else " "
        lines.append(f"{marker} {item}")
    lines.extend(["", f"CLI: {command_hint(state.selected_item())}"])
    return lines


def _draw(stdscr, state: TuiState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for row, line in enumerate(render_lines(state)[:height]):
        stdscr.addnstr(row, 0, line, max(0, width - 1))
    stdscr.refresh()


def _read_filter(stdscr, state: TuiState) -> TuiState:
    curses.echo()
    try:
        height, width = stdscr.getmaxyx()
        prompt = "filter: "
        stdscr.addnstr(height - 1, 0, prompt, max(0, width - 1))
        value = stdscr.getstr(height - 1, len(prompt), max(1, width - len(prompt) - 1)).decode("utf-8", "replace")
        return apply_filter(state, value)
    finally:
        curses.noecho()


def _main(stdscr) -> int:
    curses.curs_set(0)
    state = initial_tui_state()
    while True:
        _draw(stdscr, state)
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return 0
        if key in (curses.KEY_DOWN, ord("j")):
            state = move_selection(state, 1)
        elif key in (curses.KEY_UP, ord("k")):
            state = move_selection(state, -1)
        elif key in (ord("\t"),):
            state = cycle_client_mode(state)
        elif key == ord("/"):
            state = _read_filter(stdscr, state)


def run() -> int:
    return curses.wrapper(_main)
