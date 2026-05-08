"""Thin stdlib curses TUI shell and pure state helpers."""
from __future__ import annotations

import curses
import contextlib
import io
import json
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

ENTER_KEYS = (10, 13, curses.KEY_ENTER)
BACK_KEYS = (8, 127, curses.KEY_BACKSPACE)

PAIR_NORMAL = 1
PAIR_HIGHLIGHT = 2
PAIR_MUTED = 3

CUSTOM_BG = 16
CUSTOM_FG = 17
CUSTOM_ACCENT = 18
CUSTOM_MUTED = 19

THEME_BG = (0x27, 0x29, 0x32)
THEME_FG = (0x37, 0xEB, 0xF3)
THEME_ACCENT = (0xFD, 0xF5, 0x00)
THEME_MUTED = (0x8B, 0xE9, 0xFE)


@dataclass(frozen=True)
class TuiState:
    items: list[str]
    selected_index: int = 0
    filter_text: str = ""
    client_mode: str = "all"
    status: str = "q quits · enter opens · : runs CLI command · b/backspace returns · / filters · tab changes client"
    mode: str = "dashboard"
    detail_item: str | None = None
    detail_lines: tuple[str, ...] = ()
    pending_action: dict[str, Any] | None = None

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
        "First-run wizard": "skills-manager scan --json",
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


def detail_lines_for_item(item: str | None, client: str = "all") -> list[str]:
    descriptions = {
        "First-run wizard": "Scan existing skill locations, preview intake/migration, choose initial skills, then materialize.",
        "Managed skills": "Inspect canonical managed skills and effective visibility for Claude/Codex.",
        "Presets": "List, inspect, preview, create, rename, delete, and apply reusable preset templates.",
        "Global configuration": "Edit global desired-state manifests. Enabling here does not render until materialize.",
        "Project configuration": "Edit project desired-state manifests. Project disables can mask global enables.",
        "Diff": "Compare desired state with rendered client skill directories without mutating files.",
        "Materialize": "Preview then render desired state into manager-owned Claude/Codex skill directories.",
        "Doctor": "Audit broken links, missing SKILL.md files, conflicts, unsafe targets, and preset validity.",
        "Backup / restore": "Preview/export managed store backups, or preview/restore from a backup directory.",
        "Rollback / transactions": "Inspect materialization journals and roll back manager-created render changes.",
        "Action log": "Inspect the append-only JSONL log for applied CLI/TUI mutations.",
    }
    if not item:
        return ["No item selected."]
    command = command_hint(item)
    lines = [
        descriptions.get(item, "Open this section."),
        "",
        f"Client mode: {client}",
    ]
    if command:
        lines.extend(["", "Equivalent CLI:", f"  {command}"])
    lines.extend(["", "This screen is read-only until a preview/confirmation flow is shown."])
    return lines


def _limit_lines(lines: list[str], limit: int = 40) -> list[str]:
    if len(lines) <= limit:
        return lines
    return [*lines[: limit - 1], f"... {len(lines) - limit + 1} more lines"]


ACTION_KEYS = "123456789abcdefghijklmnopqrstuvwxyz"

TUI_ACTIONS: dict[str, dict[str, Any]] = {
    "scan": {"section": "First-run wizard", "label": "Scan all skill locations", "fields": [{"name": "project", "label": "Project path (blank for none)", "default": ""}]},
    "import_inbox": {"section": "First-run wizard", "label": "Import inbox skills", "fields": []},
    "adopt_path": {"section": "First-run wizard", "label": "Adopt a skill from a path", "fields": [{"name": "path", "label": "Skill directory path", "required": True}]},
    "migrate": {"section": "First-run wizard", "label": "Migrate existing Claude/Codex skills", "fields": []},
    "state": {"section": "Managed skills", "label": "Inspect effective state", "fields": [{"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path (blank for none)", "default": ""}]},
    "enable": {"section": "Managed skills", "label": "Enable a managed skill", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "scope", "label": "Scope", "choices": ["global", "project", "session"], "default": "global"}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path for project scope", "default": ""}]},
    "disable": {"section": "Managed skills", "label": "Disable/mask a managed skill", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "scope", "label": "Scope", "choices": ["global", "project", "session"], "default": "global"}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path for project scope", "default": ""}]},
    "remove_override": {"section": "Managed skills", "label": "Remove direct enable/disable override", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "scope", "label": "Scope", "choices": ["global", "project", "session"], "default": "global"}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path for project scope", "default": ""}]},
    "global_state": {"section": "Global configuration", "label": "Inspect global direct/effective state", "fields": [{"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "global_enable": {"section": "Global configuration", "label": "Enable globally", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "global_disable": {"section": "Global configuration", "label": "Disable globally", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "project_state": {"section": "Project configuration", "label": "Inspect project direct/effective state", "fields": [{"name": "project", "label": "Project path", "default": "."}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "project_enable": {"section": "Project configuration", "label": "Enable for project", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "project", "label": "Project path", "default": "."}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "project_disable": {"section": "Project configuration", "label": "Disable/mask for project", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "project", "label": "Project path", "default": "."}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "project_remove_override": {"section": "Project configuration", "label": "Remove project override", "fields": [{"name": "skill", "label": "Skill ID or alias", "required": True}, {"name": "project", "label": "Project path", "default": "."}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}]},
    "preset_list": {"section": "Presets", "label": "List presets", "fields": []},
    "preset_show": {"section": "Presets", "label": "Show preset details", "fields": [{"name": "name", "label": "Preset name", "required": True}]},
    "preset_create": {"section": "Presets", "label": "Create preset", "fields": [{"name": "name", "label": "Preset name", "required": True}, {"name": "description", "label": "Description", "default": ""}, {"name": "tags", "label": "Tags (space-separated)", "default": ""}]},
    "preset_capture": {"section": "Presets", "label": "Capture preset from scope", "fields": [{"name": "name", "label": "Preset name", "required": True}, {"name": "scope", "label": "Scope", "choices": ["global", "project"], "default": "global"}, {"name": "project", "label": "Project path for project scope", "default": ""}]},
    "preset_add": {"section": "Presets", "label": "Add skills to preset", "fields": [{"name": "name", "label": "Preset name", "required": True}, {"name": "skills", "label": "Skill IDs/aliases (space-separated)", "required": True}, {"name": "mode", "label": "Mode", "choices": ["enable", "disable"], "default": "enable"}]},
    "preset_remove": {"section": "Presets", "label": "Remove skills from preset", "fields": [{"name": "name", "label": "Preset name", "required": True}, {"name": "skills", "label": "Skill IDs/aliases (space-separated)", "required": True}, {"name": "mode", "label": "Mode", "choices": ["enable", "disable"], "default": "enable"}]},
    "preset_rename": {"section": "Presets", "label": "Rename preset", "fields": [{"name": "old_name", "label": "Current preset name", "required": True}, {"name": "new_name", "label": "New preset name", "required": True}]},
    "preset_delete": {"section": "Presets", "label": "Delete preset", "fields": [{"name": "name", "label": "Preset name", "required": True}]},
    "preset_apply": {"section": "Presets", "label": "Apply preset to manifest", "fields": [{"name": "name", "label": "Preset name", "required": True}, {"name": "scope", "label": "Scope", "choices": ["global", "project"], "default": "global"}, {"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path for project scope", "default": ""}, {"name": "replace", "label": "Replace existing entries? (y/N)", "default": "n"}]},
    "diff": {"section": "Diff", "label": "Preview desired vs rendered diff", "fields": [{"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path (blank for global)", "default": ""}]},
    "materialize": {"section": "Materialize", "label": "Materialize desired state", "fields": [{"name": "client", "label": "Client", "choices": ["all", "claude", "codex"], "default": "all"}, {"name": "project", "label": "Project path (blank for global)", "default": ""}]},
    "doctor": {"section": "Doctor", "label": "Run doctor audit", "fields": [{"name": "project", "label": "Project path (blank for none)", "default": ""}]},
    "backup": {"section": "Backup / restore", "label": "Export backup", "fields": [{"name": "export_path", "label": "Export directory", "default": "agent-skills-backup"}]},
    "restore": {"section": "Backup / restore", "label": "Restore from backup", "fields": [{"name": "path", "label": "Backup path", "required": True}]},
    "rollback": {"section": "Rollback / transactions", "label": "Rollback materialization transaction", "fields": [{"name": "transaction_id", "label": "Transaction ID", "required": True}]},
    "action_log": {"section": "Action log", "label": "Show recent action log", "fields": []},
}

CLI_CAPABILITY_ACTIONS = {
    "scan": ("scan",),
    "import": ("import_inbox",),
    "adopt": ("adopt_path",),
    "migrate": ("migrate",),
    "state": ("state", "global_state", "project_state"),
    "enable": ("enable", "global_enable", "project_enable"),
    "disable": ("disable", "global_disable", "project_disable"),
    "materialize": ("materialize",),
    "diff": ("diff",),
    "doctor": ("doctor",),
    "rollback": ("rollback",),
    "backup": ("backup",),
    "restore": ("restore",),
    "preset list": ("preset_list",),
    "preset show": ("preset_show",),
    "preset create": ("preset_create", "preset_capture"),
    "preset add": ("preset_add",),
    "preset remove": ("preset_remove",),
    "preset rename": ("preset_rename",),
    "preset delete": ("preset_delete",),
    "preset apply": ("preset_apply",),
}


def actions_for_section(section: str | None) -> list[tuple[str, dict[str, Any]]]:
    if not section:
        return []
    return [(action_id, action) for action_id, action in TUI_ACTIONS.items() if action["section"] == section]


def action_key_for_index(index: int) -> str:
    return ACTION_KEYS[index] if index < len(ACTION_KEYS) else "?"


def action_id_for_key(section: str | None, key: int) -> str | None:
    try:
        pressed = chr(key)
    except ValueError:
        return None
    for index, (action_id, _action) in enumerate(actions_for_section(section)):
        if pressed == action_key_for_index(index):
            return action_id
    return None


def section_action_menu_lines(section: str | None) -> list[str]:
    actions = actions_for_section(section)
    if not actions:
        return []
    lines = ["", "Actions:"]
    for index, (_action_id, action) in enumerate(actions):
        lines.append(f"  {action_key_for_index(index)}  {action['label']}")
    return lines


def execute_cli_command(command: str) -> dict[str, Any]:
    text = command.strip()
    if text.startswith("skills-manager "):
        text = text.removeprefix("skills-manager ").strip()
    if not text:
        return {"ok": False, "exit_code": 2, "stdout": "", "stderr": "empty command"}
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        return {"ok": False, "exit_code": 2, "stdout": "", "stderr": str(exc)}
    safety = cli_command_safety(argv)
    if not safety["safe"]:
        return {
            "ok": False,
            "exit_code": 2,
            "stdout": "",
            "stderr": f"{safety['reason']}\nUse the TUI action menu for mutating operations so preview/confirmation gates cannot be bypassed.",
            "argv": argv,
        }
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        from . import cli

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(argv)
    except SystemExit as exc:
        exit_code = int(exc.code or 0) if isinstance(exc.code, int) else 1
    except Exception as exc:
        exit_code = 1
        stderr.write(f"{type(exc).__name__}: {exc}\n")
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "argv": argv,
    }


def cli_command_safety(argv: list[str]) -> dict[str, Any]:
    if not argv:
        return {"safe": False, "reason": "empty command"}
    command = argv[0]
    if command in {"scan", "state", "diff", "doctor"}:
        return {"safe": True}
    if command == "import":
        return {"safe": _has_option(argv, "--dry-run"), "reason": "import without --dry-run mutates the managed store"}
    if command == "migrate":
        return {"safe": _has_option(argv, "--dry-run") and not _has_option(argv, "--apply"), "reason": "migrate --apply mutates the managed store"}
    if command == "materialize":
        return {"safe": _has_option(argv, "--dry-run"), "reason": "materialize without --dry-run mutates rendered skill directories"}
    if command == "backup":
        return {"safe": _has_option(argv, "--dry-run") or not _has_option(argv, "--export"), "reason": "backup --export writes files"}
    if command == "restore":
        return {"safe": _has_option(argv, "--dry-run") and not _has_option(argv, "--apply"), "reason": "restore --apply mutates managed store state"}
    if command in {"adopt", "enable", "disable", "rollback"}:
        return {"safe": False, "reason": f"{command} mutates state"}
    if command == "preset":
        if len(argv) < 2:
            return {"safe": True}
        subcommand = argv[1]
        if subcommand in {"list", "show"}:
            return {"safe": True}
        if subcommand in {"create", "add", "remove", "apply"}:
            return {"safe": _has_option(argv, "--dry-run"), "reason": f"preset {subcommand} without --dry-run mutates state"}
        if subcommand in {"rename", "delete"}:
            return {"safe": not _has_option(argv, "--apply"), "reason": f"preset {subcommand} --apply mutates state"}
    return {"safe": False, "reason": "unknown command cannot be safety-classified"}


def _has_option(argv: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in argv)


def cli_result_lines(command: str, result: dict[str, Any]) -> list[str]:
    lines = [
        "Command result",
        f"$ skills-manager {command.strip()}",
        f"exit: {result['exit_code']}",
        "",
    ]
    if result.get("stdout"):
        lines.append("stdout:")
        lines.extend(f"  {line}" for line in result["stdout"].rstrip().splitlines()[:30])
    if result.get("stderr"):
        lines.append("stderr:")
        lines.extend(f"  {line}" for line in result["stderr"].rstrip().splitlines()[:20])
    if not result.get("stdout") and not result.get("stderr"):
        lines.append("(no output)")
    lines.extend(["", ": run another command · b/backspace: back · q: quit"])
    return _limit_lines(lines, limit=60)


def _format_skill_rows(rows: list[dict[str, Any]], empty: str) -> list[str]:
    if not rows:
        return [empty]
    lines = []
    for row in rows[:12]:
        enabled = "on " if row.get("enabled") else "off"
        direct = row.get("direct", "none")
        lines.append(f"{enabled}  {row.get('alias')}  {row.get('id')}  direct:{direct}")
    if len(rows) > 12:
        lines.append(f"... {len(rows) - 12} more skills")
    return lines


def _format_action_log_tail(limit: int = 10) -> list[str]:
    path = action_log.actions_path()
    if not path.exists():
        return ["No action log yet."]
    entries = path.read_text(encoding="utf-8").splitlines()[-limit:]
    lines = []
    for raw in entries:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            lines.append("unparseable action-log entry")
            continue
        action = entry.get("action", "unknown")
        surface = entry.get("surface", "")
        scope = entry.get("scope", "")
        client_value = entry.get("client", "")
        parts = [str(action)]
        if surface:
            parts.append(f"surface:{surface}")
        if scope:
            parts.append(f"scope:{scope}")
        if client_value:
            parts.append(f"client:{client_value}")
        lines.append("  ".join(parts))
    return lines or ["No action log entries."]


def section_lines_for_item(item: str | None, client: str = "all") -> list[str]:
    if not item:
        return ["No item selected."]
    try:
        if item == "First-run wizard":
            view = build_first_run_wizard_state()
            locations = view["scan"].get("locations", {})
            lines = [
                "First-run wizard status",
                f"Needs backup before intake: {view['needs_backup']}",
                "",
                "Detected locations:",
            ]
            for name, loc in locations.items():
                lines.append(f"  {name}: {len(loc.get('entries', []))} entries")
            lines.extend(
                [
                    "",
                    f"Inbox import candidates: {len(view['import_preview']['result'].get('candidates', []))}",
                    f"Migration actions: {len(view['migrate_preview']['result'].get('actions', []))}",
                    "",
                ]
            )
            lines.extend(section_action_menu_lines(item))
        elif item in {"Managed skills", "Global configuration"}:
            view = build_desired_state_view("global", client)
            lines = [
                "Global desired state",
                f"Client: {client}",
                f"Direct enables: {len(view['direct_entries']['enable'])}",
                f"Direct disables: {len(view['direct_entries']['disable'])}",
                "",
                "Skills:",
                *_format_skill_rows(view["rows"], "No managed skills yet."),
            ]
        elif item == "Project configuration":
            view = build_desired_state_view("project", client, project=Path.cwd())
            lines = [
                "Project desired state",
                f"Project: {view['project']}",
                f"Client: {client}",
                f"Direct enables: {len(view['direct_entries']['enable'])}",
                f"Direct disables: {len(view['direct_entries']['disable'])}",
                "",
                "Skills:",
                *_format_skill_rows(view["rows"], "No managed skills visible for this project."),
            ]
        elif item == "Presets":
            view = build_preset_manager_view()
            names = view["names"]
            lines = ["Presets", f"Count: {len(names)}", "", *(f"  {name}" for name in names[:20])]
            if len(names) > 20:
                lines.append(f"... {len(names) - 20} more presets")
            if not names:
                lines.append("No presets yet.")
        elif item in {"Diff", "Materialize"}:
            view = build_render_view(client)
            lines = [
                f"{item} preview",
                f"Client: {client}",
                f"Rendered dir: {view['rendered_dir']}",
                f"Creates: {len(view['creates'])}",
                f"Removes: {len(view['removes'])}",
                f"Conflicts: {len(view['conflicts'])}",
            ]
            for conflict in view["conflicts"][:8]:
                lines.append(f"  conflict: {conflict}")
            if item == "Materialize":
                lines.extend(["", "This is a dry-run preview. Materialization still requires explicit confirmation."])
        elif item == "Doctor":
            view = build_doctor_view()
            lines = ["Doctor", f"OK: {view['ok']}", f"Issues: {len(view['issues'])}", ""]
            lines.extend(f"  {issue}" for issue in view["issues"][:12])
            if not view["issues"]:
                lines.append("No issues found.")
        elif item == "Backup / restore":
            target = Path.cwd() / "agent-skills-backup"
            view = preview_backup(target)
            result = view["result"]
            lines = [
                "Backup preview",
                f"Target: {result.get('target')}",
                "Includes:",
                *(f"  {value}" for value in result.get("includes", [])),
            ]
        elif item == "Rollback / transactions":
            view = build_rollback_view()
            txs = view["transactions"]
            lines = ["Rollback / transactions", f"Transactions: {len(txs)}", ""]
            lines.extend(f"  {tx.get('id')}  {tx.get('status')}  actions:{len(tx.get('actions', []))}" for tx in txs[:12])
            if not txs:
                lines.append("No materialization transactions yet.")
        elif item == "Action log":
            lines = ["Action log", f"Path: {action_log.actions_path()}", "", *_format_action_log_tail()]
        else:
            lines = detail_lines_for_item(item, client)
    except Exception as exc:  # Defensive: a broken section should not kill curses.
        lines = [f"Could not load {item}.", f"{type(exc).__name__}: {exc}"]
    if item != "First-run wizard":
        lines.extend(section_action_menu_lines(item))
    command = command_hint(item)
    if command:
        lines.extend(["", "Equivalent CLI:", f"  {command}"])
    lines.extend(["", "enter: refresh · b/backspace: back · q: quit"])
    return _limit_lines(lines)


def _summarize_import_preview(preview: dict[str, Any]) -> list[str]:
    candidates = preview["result"].get("candidates", [])
    lines = ["Inbox import preview", f"Candidates: {len(candidates)}", ""]
    lines.extend(f"  {item.get('name')}  {item.get('path')}" for item in candidates[:12])
    if len(candidates) > 12:
        lines.append(f"... {len(candidates) - 12} more candidates")
    if candidates:
        lines.extend(["", "Press y to copy these skills into the managed store. Import does not enable or render them."])
    else:
        lines.append("Nothing to import.")
    return lines


def _summarize_migrate_preview(preview: dict[str, Any]) -> list[str]:
    actions = preview["result"].get("actions", [])
    lines = ["Migration preview", f"Actions: {len(actions)}", ""]
    lines.extend(f"  {item.get('kind')}  {item.get('alias')}  {item.get('skill_id')}" for item in actions[:12])
    if len(actions) > 12:
        lines.append(f"... {len(actions) - 12} more actions")
    if actions:
        lines.extend(["", "Type MIGRATE to copy these rendered skills into the managed store. Originals are not removed."])
    else:
        lines.append("Nothing to migrate.")
    return lines


def _summarize_materialize_preview(preview: dict[str, Any]) -> list[str]:
    lines = [
        "Materialize preview",
        f"Client: {preview['client']}",
        f"Creates: {len(preview['creates'])}",
        f"Removes: {len(preview['removes'])}",
        f"Conflicts: {len(preview['conflicts'])}",
        "",
    ]
    for item in preview["creates"][:8]:
        lines.append(f"  create {item.get('client', preview['client'])}:{item.get('alias')} -> {item.get('skill_id')}")
    for item in preview["removes"][:8]:
        lines.append(f"  remove {item.get('client', preview['client'])}:{item.get('alias')} -> {item.get('skill_id')}")
    for item in preview["conflicts"][:8]:
        lines.append(f"  conflict {item.get('client', preview['client'])}:{item.get('alias')} {item.get('path')}")
    if preview["conflicts"]:
        lines.extend(["", "Conflicts block materialization. Run doctor and resolve unmanaged rendered entries first."])
    elif preview["creates"] or preview["removes"]:
        lines.extend(["", "Press y to apply this materialization preview."])
    else:
        lines.append("Rendered output already matches desired state.")
    return lines


def _summarize_apply_result(title: str, result: dict[str, Any]) -> list[str]:
    lines = [title, f"OK: {result.get('ok', True)}", ""]
    if "adopted" in result:
        lines.append(f"Adopted: {len(result.get('adopted', []))}")
    if "applied" in result:
        lines.append(f"Applied: {len(result.get('applied', []))}")
    if "results" in result:
        for client, item in result["results"].items():
            lines.append(f"{client}: ok={item.get('ok')} actions={len(item.get('actions', []))}")
    elif "actions" in result:
        lines.append(f"Actions: {len(result.get('actions', []))}")
    if result.get("transaction_id"):
        lines.append(f"Transaction: {result['transaction_id']}")
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    lines.extend(["", "enter: refresh · b/backspace: back · q: quit"])
    return lines


def begin_first_run_action(state: TuiState, action: str) -> TuiState:
    if action == "import":
        preview = preview_import_inbox()
        lines = _summarize_import_preview(preview)
        pending = {"kind": "import", "confirmation": "single_key", "preview": preview}
        status = "Import preview · press y to apply · b/backspace cancels"
    elif action == "migrate":
        preview = preview_migrate()
        lines = _summarize_migrate_preview(preview)
        pending = {"kind": "migrate", "confirmation": "typed", "preview": preview}
        status = "Migration preview · type MIGRATE to apply · b/backspace cancels"
    elif action == "materialize":
        preview = first_run_preview_materialize(state.client_mode)
        lines = _summarize_materialize_preview(preview)
        pending = {"kind": "materialize", "confirmation": "single_key", "preview": preview}
        status = "Materialize preview · press y to apply · b/backspace cancels"
    elif action == "doctor":
        lines = section_lines_for_item("Doctor", state.client_mode)
        pending = None
        status = "Doctor refreshed · b/backspace returns · q quits"
    else:
        return replace(state, status=f"Unknown first-run action: {action}")
    lines.extend(["", "Equivalent CLI:", f"  {pending['preview']['cli_command']}" if pending else "  skills-manager doctor"])
    return replace(state, detail_lines=tuple(_limit_lines(lines)), pending_action=pending, status=status)


def confirm_pending_action(state: TuiState, typed_confirmation: str | None = None) -> TuiState:
    pending = state.pending_action
    if not pending:
        return replace(state, status="No pending action to confirm.")
    if pending.get("kind") == "tui_action":
        preview = pending["preview"]
        action_id = preview["action_id"]
        confirmation = pending.get("confirmation")
        if confirmation == "typed":
            expected = {
                "migrate": "MIGRATE",
                "restore": "RESTORE",
                "rollback": "ROLLBACK",
                "preset_delete": "DELETE",
                "preset_apply": "APPLY",
            }.get(action_id, action_id.upper())
            if typed_confirmation != expected:
                return replace(state, status="Confirmation did not match; action cancelled.", pending_action=None)
        result = apply_tui_action(preview)
        return replace(
            state,
            detail_lines=tuple(tui_action_apply_lines(action_id, result)),
            pending_action=None,
            status=f"{preview['label']} applied · b/backspace returns · q quits",
        )
    if pending.get("confirmation") == "typed" and typed_confirmation != "MIGRATE":
        return replace(state, status="Confirmation did not match; action cancelled.", pending_action=None)
    kind = pending["kind"]
    preview = pending["preview"]
    if kind == "import":
        result = apply_import_preview(preview)
        lines = _summarize_apply_result("Inbox import applied", result)
    elif kind == "migrate":
        result = apply_migrate_preview(preview)
        lines = _summarize_apply_result("Migration applied", result)
    elif kind == "materialize":
        result = apply_materialize_preview(preview)
        lines = _summarize_apply_result("Materialize applied", result)
    else:
        lines = [f"Unknown pending action: {kind}"]
    return replace(state, detail_lines=tuple(_limit_lines(lines)), pending_action=None, status=f"{kind} applied · enter refreshes · b/backspace returns")


def is_enter_key(key: int) -> bool:
    return key in ENTER_KEYS


def is_back_key(key: int) -> bool:
    return key in BACK_KEYS or key in (ord("b"), ord("h"), curses.KEY_LEFT)


def open_selected_item(state: TuiState) -> TuiState:
    item = state.selected_item()
    if item is None:
        return replace(state, status="No matching item to open.")
    return replace(
        state,
        mode="detail",
        detail_item=item,
        detail_lines=tuple(section_lines_for_item(item, state.client_mode)),
        status=f"{item} · enter refreshes · b/backspace returns · q quits",
    )


def refresh_detail(state: TuiState) -> TuiState:
    if state.mode != "detail" or not state.detail_item:
        return state
    return replace(
        state,
        detail_lines=tuple(section_lines_for_item(state.detail_item, state.client_mode)),
        status=f"{state.detail_item} · refreshed · enter refreshes · b/backspace returns · q quits",
    )


def close_detail(state: TuiState) -> TuiState:
    next_mode = "first_run" if state.items and state.items[0] == "First-run wizard" else "dashboard"
    return replace(
        state,
        mode=next_mode,
        detail_item=None,
        detail_lines=(),
        pending_action=None,
        status="q quits · enter opens · : runs CLI command · b/backspace returns · / filters · tab changes client",
    )


def show_cli_result(state: TuiState, command: str, result: dict[str, Any]) -> TuiState:
    return replace(
        state,
        mode="detail",
        detail_item="Command",
        detail_lines=tuple(cli_result_lines(command, result)),
        pending_action=None,
        status=f"Command exited {result['exit_code']} · : runs another · b/backspace returns · q quits",
    )


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
    command = f"skills-manager materialize --client {client}"
    if project is not None:
        command += f" --project {_quote(str(Path(project).expanduser().resolve()))}"
    command += " --dry-run"
    if client == "all":
        diffs = {name: materializer.diff(name, project=project) for name in adapters.CLIENTS}
        creates = [{**item, "client": name} for name, d in diffs.items() for item in d["creates"]]
        removes = [{**item, "client": name} for name, d in diffs.items() for item in d["removes"]]
        conflicts = [{**item, "client": name} for name, d in diffs.items() for item in d["conflicts"]]
        return {
            "client": client,
            "project": str(Path(project).expanduser().resolve()) if project is not None else None,
            "rendered_dir": "multiple",
            "creates": creates,
            "removes": removes,
            "conflicts": conflicts,
            "desired": {name: d["desired"] for name, d in diffs.items()},
            "actual": {name: d["actual"] for name, d in diffs.items()},
            "confirmation": confirmation_for("materialize"),
            "cli_command": command,
            "clients": diffs,
        }
    diff = materializer.diff(client, project=project)
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
    if client == "all":
        results = {
            name: materializer.materialize(name, project=project, dry_run=False, surface="tui")
            for name in adapters.CLIENTS
        }
        return {
            "ok": all(result.get("ok") for result in results.values()),
            "client": client,
            "results": results,
            "doctor": build_doctor_view(project=project),
            "note": "Restart Codex or start a new Codex session for skill visibility.",
        }
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
    result = {"ok": bool(tx), "transaction_id": transaction_id}
    if not tx:
        result["error"] = "transaction not found"
    return {
        "action": "rollback",
        "confirmation": "typed",
        "transaction": tx,
        "result": result,
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


def _blank_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bool_from_form(value: Any) -> bool:
    return str(value or "").strip().lower() in {"y", "yes", "true", "1", "replace"}


def _refs_from_form(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        return shlex.split(str(value or ""))
    except ValueError:
        return [part for part in str(value or "").split() if part]


def resolve_managed_skill_ref(ref: str) -> dict[str, Any]:
    managed = store.all_skills()
    if ref in managed:
        return {"ok": True, "skill_id": ref}
    matches = [
        skill_id
        for skill_id, meta in managed.items()
        if ref == skill_id.split(".")[-1] or ref in set(meta.get("aliases", {}).values())
    ]
    if len(matches) == 1:
        return {"ok": True, "skill_id": matches[0]}
    if len(matches) > 1:
        return {"ok": False, "error": "ambiguous skill reference", "candidates": sorted(matches)}
    return {"ok": False, "error": "unknown skill reference"}


def _preview_desired_edit(values: dict[str, Any], action: str) -> dict[str, Any]:
    resolved = resolve_managed_skill_ref(str(values.get("skill", "")))
    scope = str(values.get("scope") or "global")
    client = str(values.get("client") or "all")
    project = _blank_to_none(values.get("project"))
    return {
        "action": action,
        "confirmation": confirmation_for(action),
        "result": {**resolved, "scope": scope, "client": client, "project": project, "will": action},
        "cli_command": _desired_cli_command(action, values),
    }


def _desired_cli_command(action: str, values: dict[str, Any]) -> str:
    skill = _quote(str(values.get("skill") or "SKILL"))
    scope = str(values.get("scope") or "global")
    client = str(values.get("client") or "all")
    project = _blank_to_none(values.get("project"))
    if action == "remove_override":
        command = f"skills-manager state --client {client}"
    else:
        command = f"skills-manager {action} {skill} --scope {scope} --client {client}"
    if project:
        command += f" --project {_quote(project)}"
    return command


def _apply_desired_edit_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = preview["result"]
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error"), "candidates": result.get("candidates", [])}
    view = apply_desired_state_edit(
        str(result["scope"]),
        str(result["skill_id"]),
        str(result["will"]),
        client=str(result["client"]),
        project=result.get("project"),
    )
    return {"ok": True, "view": view, "needs_materialize": True}


def apply_adopt_preview(preview: dict[str, Any]) -> dict[str, Any]:
    source = preview["result"].get("source") or preview["result"].get("path") or preview["result"].get("source_path")
    if not source:
        source = preview["cli_command"].removeprefix("skills-manager adopt ").strip()
    result = planner.adopt_skill(source, dry_run=False)
    result["doctor"] = build_doctor_view()
    result["preview"] = preview
    return result


def _apply_preset_create_preview(preview: dict[str, Any]) -> dict[str, Any]:
    preset = preview["result"]["preset"]
    return presets.create_preset(
        preset["name"],
        description=preset.get("description", ""),
        tags=preset.get("tags", []),
        dry_run=False,
        surface="tui",
    )


def _apply_preset_capture_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = preview["result"]
    preset = result["preset"]
    return presets.capture_preset(
        preset["name"],
        result["source_scope"],
        project=result.get("project"),
        description=preset.get("description", ""),
        tags=preset.get("tags", []),
        dry_run=False,
        surface="tui",
    )


def _apply_preset_edit_preview(preview: dict[str, Any]) -> dict[str, Any]:
    action = preview["action"]
    result = preview["result"]
    name = result["preset"]["name"]
    mode = "disable" if "--mode disable" in preview["cli_command"] else "enable"
    refs = [entry["id"] for entry in result.get("added", [])] or list(result.get("removed", []))
    if action == "preset_add":
        return presets.add_entries(name, refs, mode=mode, dry_run=False, surface="tui")
    return presets.remove_entries(name, refs, mode=mode, dry_run=False, surface="tui")


def _apply_preset_rename_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return presets.rename_preset(Path(preview["result"]["from"]).stem, Path(preview["result"]["to"]).stem, apply=True, surface="tui")


def _apply_preset_delete_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return presets.delete_preset(Path(preview["result"]["path"]).stem, apply=True, surface="tui")


def _apply_preset_apply_preview(preview: dict[str, Any]) -> dict[str, Any]:
    result = preview["result"]
    return presets.apply_preset(
        result["preset"],
        result["scope"],
        project=result.get("project"),
        client=result["client"],
        replace=bool(result["replace"]),
        dry_run=False,
        surface="tui",
    )


def _tui_action_command(action_id: str, values: dict[str, Any]) -> str:
    project = _blank_to_none(values.get("project"))
    client = str(values.get("client") or "all")
    if action_id == "scan":
        command = "skills-manager scan --json"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id == "import_inbox":
        return "skills-manager import --dry-run"
    if action_id == "adopt_path":
        return f"skills-manager adopt {_quote(str(values.get('path') or 'PATH'))}"
    if action_id == "migrate":
        return "skills-manager migrate --dry-run"
    if action_id == "state":
        command = f"skills-manager state --client {client} --json"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id in {"global_state", "project_state"}:
        scope_project = _blank_to_none(values.get("project")) if action_id == "project_state" else None
        command = f"skills-manager state --client {client} --json"
        if scope_project:
            command += f" --project {_quote(scope_project)}"
        return command
    if action_id in {"enable", "disable", "remove_override"}:
        return _desired_cli_command(action_id, values)
    if action_id in {"global_enable", "global_disable"}:
        return _desired_cli_command("enable" if action_id.endswith("enable") else "disable", {**values, "scope": "global"})
    if action_id in {"project_enable", "project_disable", "project_remove_override"}:
        action = "remove_override" if action_id.endswith("remove_override") else "enable" if action_id.endswith("enable") else "disable"
        return _desired_cli_command(action, {**values, "scope": "project"})
    if action_id == "diff":
        command = f"skills-manager diff --client {client}"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id == "materialize":
        command = f"skills-manager materialize --client {client} --dry-run"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id == "doctor":
        command = "skills-manager doctor"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id == "backup":
        return f"skills-manager backup --export {_quote(str(values.get('export_path') or 'agent-skills-backup'))}"
    if action_id == "restore":
        return f"skills-manager restore --from {_quote(str(values.get('path') or 'PATH'))} --dry-run"
    if action_id == "rollback":
        return f"skills-manager rollback {_quote(str(values.get('transaction_id') or 'TRANSACTION'))}"
    if action_id == "preset_list":
        return "skills-manager preset list"
    if action_id == "preset_show":
        return f"skills-manager preset show {_quote(str(values.get('name') or 'NAME'))}"
    if action_id == "preset_create":
        return f"skills-manager preset create {_quote(str(values.get('name') or 'NAME'))} --dry-run"
    if action_id == "preset_capture":
        command = f"skills-manager preset create {_quote(str(values.get('name') or 'NAME'))} --from-scope {values.get('scope') or 'global'} --dry-run"
        if project:
            command += f" --project {_quote(project)}"
        return command
    if action_id in {"preset_add", "preset_remove"}:
        return f"skills-manager preset {action_id.removeprefix('preset_')} {_quote(str(values.get('name') or 'NAME'))} {values.get('skills') or 'SKILL'} --mode {values.get('mode') or 'enable'} --dry-run"
    if action_id == "preset_rename":
        return f"skills-manager preset rename {_quote(str(values.get('old_name') or 'OLD'))} {_quote(str(values.get('new_name') or 'NEW'))}"
    if action_id == "preset_delete":
        return f"skills-manager preset delete {_quote(str(values.get('name') or 'NAME'))}"
    if action_id == "preset_apply":
        command = f"skills-manager preset apply {_quote(str(values.get('name') or 'NAME'))} --scope {values.get('scope') or 'global'} --client {client} --dry-run"
        if project:
            command += f" --project {_quote(project)}"
        if _bool_from_form(values.get("replace")):
            command += " --replace"
        return command
    return f"skills-manager {action_id}"


def preview_tui_action(action_id: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    values = dict(values or {})
    if action_id not in TUI_ACTIONS:
        return {"ok": False, "action_id": action_id, "error": f"unknown TUI action: {action_id}"}
    action = TUI_ACTIONS[action_id]
    command = _tui_action_command(action_id, values)
    project = _blank_to_none(values.get("project"))
    client = str(values.get("client") or "all")
    try:
        if action_id == "scan":
            payload = {"action": "scan", "confirmation": "none", "result": build_scan_view(project=project), "cli_command": command}
        elif action_id == "import_inbox":
            payload = preview_import_inbox()
        elif action_id == "adopt_path":
            payload = preview_adopt_path(str(values.get("path") or ""))
        elif action_id == "migrate":
            payload = preview_migrate()
        elif action_id in {"state", "global_state", "project_state"}:
            target_project = project if action_id in {"state", "project_state"} else None
            clients = adapters.expand_clients(client)
            result: Any = {name: resolver.resolve(name, project=target_project) for name in clients}
            if client != "all":
                result = result[client]
            payload = {"action": "state", "confirmation": "none", "result": result, "cli_command": command}
        elif action_id in {"enable", "disable", "remove_override"}:
            payload = _preview_desired_edit(values, action_id)
        elif action_id in {"global_enable", "global_disable"}:
            payload = _preview_desired_edit({**values, "scope": "global"}, "enable" if action_id.endswith("enable") else "disable")
        elif action_id in {"project_enable", "project_disable", "project_remove_override"}:
            desired_action = "remove_override" if action_id.endswith("remove_override") else "enable" if action_id.endswith("enable") else "disable"
            payload = _preview_desired_edit({**values, "scope": "project"}, desired_action)
        elif action_id == "diff":
            payload = {"action": "diff", "confirmation": "none", "result": build_render_view(client, project=project), "cli_command": command}
        elif action_id == "materialize":
            payload = {"action": "materialize", "confirmation": confirmation_for("materialize"), "result": build_render_view(client, project=project), "cli_command": command}
        elif action_id == "doctor":
            payload = {"action": "doctor", "confirmation": "none", "result": build_doctor_view(project=project), "cli_command": command}
        elif action_id == "backup":
            payload = preview_backup(values.get("export_path") or "agent-skills-backup")
        elif action_id == "restore":
            payload = preview_restore(str(values.get("path") or ""))
        elif action_id == "rollback":
            payload = preview_rollback(str(values.get("transaction_id") or ""))
        elif action_id == "preset_list":
            payload = {"action": "preset_list", "confirmation": "none", "result": presets.list_presets(), "cli_command": command}
        elif action_id == "preset_show":
            payload = {"action": "preset_show", "confirmation": "none", "result": presets.show_preset(str(values.get("name") or "")), "cli_command": command}
        elif action_id == "preset_create":
            payload = preview_preset_create(str(values.get("name") or ""), description=str(values.get("description") or ""), tags=_refs_from_form(values.get("tags")))
        elif action_id == "preset_capture":
            payload = preview_preset_capture(str(values.get("name") or ""), str(values.get("scope") or "global"), project=project)
        elif action_id in {"preset_add", "preset_remove"}:
            payload = preview_preset_edit(action_id.removeprefix("preset_"), str(values.get("name") or ""), _refs_from_form(values.get("skills")), mode=str(values.get("mode") or "enable"))
        elif action_id == "preset_rename":
            payload = preview_preset_rename(str(values.get("old_name") or ""), str(values.get("new_name") or ""))
        elif action_id == "preset_delete":
            payload = preview_preset_delete(str(values.get("name") or ""))
        elif action_id == "preset_apply":
            payload = preview_preset_apply(
                str(values.get("name") or ""),
                str(values.get("scope") or "global"),
                project=project,
                client=client,
                replace=_bool_from_form(values.get("replace")),
            )
        elif action_id == "action_log":
            payload = {"action": "action_log", "confirmation": "none", "result": _format_action_log_tail(), "cli_command": "cat ~/.agents/skills-store/logs/actions.jsonl"}
        else:
            payload = {"ok": False, "error": f"unimplemented action: {action_id}"}
    except Exception as exc:
        payload = {
            "action": action_id,
            "confirmation": "none",
            "result": {"ok": False, "error": str(exc), "type": type(exc).__name__},
            "cli_command": command,
        }
    payload.setdefault("cli_command", command)
    payload.setdefault("confirmation", "none")
    if isinstance(payload.get("result"), dict) and payload["result"].get("ok") is False:
        payload["confirmation"] = "none"
    return {"ok": True, "action_id": action_id, "label": action["label"], "values": values, "preview": payload}


def apply_tui_action(preview: dict[str, Any]) -> dict[str, Any]:
    action_id = preview["action_id"]
    payload = preview["preview"]
    if action_id in {"scan", "state", "global_state", "project_state", "diff", "doctor", "preset_list", "preset_show", "action_log"}:
        return {"ok": True, "read_only": True, "result": payload.get("result")}
    if action_id == "import_inbox":
        return apply_import_preview(payload)
    if action_id == "adopt_path":
        return apply_adopt_preview(payload)
    if action_id == "migrate":
        return apply_migrate_preview(payload)
    if action_id in {"enable", "disable", "remove_override", "global_enable", "global_disable", "project_enable", "project_disable", "project_remove_override"}:
        return _apply_desired_edit_preview(payload)
    if action_id == "materialize":
        return apply_materialize_preview(payload["result"])
    if action_id == "backup":
        return apply_backup_preview(payload)
    if action_id == "restore":
        return apply_restore_preview(payload)
    if action_id == "rollback":
        return apply_rollback_preview(payload)
    if action_id == "preset_create":
        return _apply_preset_create_preview(payload)
    if action_id == "preset_capture":
        return _apply_preset_capture_preview(payload)
    if action_id in {"preset_add", "preset_remove"}:
        return _apply_preset_edit_preview(payload)
    if action_id == "preset_rename":
        return _apply_preset_rename_preview(payload)
    if action_id == "preset_delete":
        return _apply_preset_delete_preview(payload)
    if action_id == "preset_apply":
        return _apply_preset_apply_preview(payload)
    return {"ok": False, "error": f"unimplemented apply action: {action_id}"}


def tui_capability_coverage() -> dict[str, Any]:
    missing = {
        capability: [action_id for action_id in action_ids if action_id not in TUI_ACTIONS]
        for capability, action_ids in CLI_CAPABILITY_ACTIONS.items()
        if any(action_id not in TUI_ACTIONS for action_id in action_ids)
    }
    return {
        "ok": not missing,
        "missing": missing,
        "capabilities": CLI_CAPABILITY_ACTIONS,
        "action_count": len(TUI_ACTIONS),
    }


def _value_lines(value: Any, limit: int = 36) -> list[str]:
    text = json.dumps(value, indent=2, sort_keys=True, default=str)
    lines = text.splitlines()
    if len(lines) > limit:
        return [*lines[: limit - 1], f"... {len(lines) - limit + 1} more lines"]
    return lines


def tui_action_preview_lines(preview: dict[str, Any]) -> list[str]:
    payload = preview["preview"]
    lines = [
        preview["label"],
        f"Action: {preview['action_id']}",
        f"Confirmation: {payload.get('confirmation', 'none')}",
        "",
        "Preview/result:",
        *_value_lines(payload.get("result", payload), limit=32),
    ]
    command = payload.get("cli_command")
    if command:
        lines.extend(["", "Equivalent CLI:", f"  {command}"])
    confirmation = payload.get("confirmation", "none")
    if confirmation == "typed":
        lines.extend(["", "Press y to continue to typed confirmation."])
    elif confirmation == "single_key":
        lines.extend(["", "Press y to apply."])
    else:
        lines.extend(["", "Read-only action completed."])
    lines.extend(["", ": command palette · b/backspace: back · q: quit"])
    return _limit_lines(lines, limit=60)


def tui_action_apply_lines(action_id: str, result: dict[str, Any]) -> list[str]:
    lines = [
        "Action applied" if not result.get("read_only") else "Action result",
        f"Action: {action_id}",
        f"OK: {result.get('ok', True)}",
        "",
        *_value_lines(result, limit=42),
        "",
        "enter: refresh section · : command palette · b/backspace: back · q: quit",
    ]
    return _limit_lines(lines, limit=60)


def begin_tui_action(state: TuiState, action_id: str, values: dict[str, Any] | None = None) -> TuiState:
    preview = preview_tui_action(action_id, values or {})
    if not preview.get("ok"):
        return replace(
            state,
            mode="detail",
            detail_item="Action error",
            detail_lines=tuple(_value_lines(preview)),
            pending_action=None,
            status="Action error · b/backspace returns · q quits",
        )
    payload_result = preview["preview"].get("result")
    if isinstance(payload_result, dict) and payload_result.get("ok") is False:
        return replace(
            state,
            mode="detail",
            detail_item=f"{preview['label']} error",
            detail_lines=tuple(tui_action_preview_lines(preview)),
            pending_action=None,
            status=f"{preview['label']} failed validation · b/backspace returns · q quits",
        )
    confirmation = preview["preview"].get("confirmation", "none")
    if confirmation == "none":
        result = apply_tui_action(preview)
        return replace(
            state,
            mode="detail",
            detail_item=preview["label"],
            detail_lines=tuple(tui_action_apply_lines(action_id, result)),
            pending_action=None,
            status=f"{preview['label']} · read-only result · b/backspace returns · q quits",
        )
    return replace(
        state,
        mode="detail",
        detail_item=preview["label"],
        detail_lines=tuple(tui_action_preview_lines(preview)),
        pending_action={"kind": "tui_action", "confirmation": confirmation, "preview": preview},
        status=f"{preview['label']} preview · press y to apply · b/backspace cancels",
    )


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
    if state.mode == "detail":
        lines.append(f"> {state.detail_item or 'Detail'}")
        lines.extend(state.detail_lines)
        lines.extend(["", "b/backspace: back · q: quit"])
        return lines
    items = state.filtered_items()
    if not items:
        lines.append("No matching items.")
    for index, item in enumerate(items):
        marker = ">" if index == state.selected_index else " "
        lines.append(f"{marker} {item}")
    lines.extend(["", f"CLI: {command_hint(state.selected_item())}"])
    return lines


def _safe_curses_call(func, *args) -> None:
    try:
        func(*args)
    except curses.error:
        pass


def _rgb_to_curses(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(round(channel * 1000 / 255) for channel in rgb)


def _can_use_custom_colors() -> bool:
    return bool(
        curses.has_colors()
        and getattr(curses, "COLORS", 0) > CUSTOM_MUTED
        and getattr(curses, "can_change_color", lambda: False)()
    )


def _init_custom_color(index: int, rgb: tuple[int, int, int]) -> None:
    _safe_curses_call(curses.init_color, index, *_rgb_to_curses(rgb))


def _init_colors() -> None:
    if not curses.has_colors():
        return
    _safe_curses_call(curses.start_color)
    _safe_curses_call(curses.use_default_colors)
    # The Ghostty Cyberpunk 2077 theme maps ANSI color 0 ("black" to curses) to
    # neon yellow. Avoid the low ANSI slots for backgrounds; use RGB colors when
    # supported and fall back to the terminal default background otherwise.
    if _can_use_custom_colors():
        _init_custom_color(CUSTOM_BG, THEME_BG)
        _init_custom_color(CUSTOM_FG, THEME_FG)
        _init_custom_color(CUSTOM_ACCENT, THEME_ACCENT)
        _init_custom_color(CUSTOM_MUTED, THEME_MUTED)
        _safe_curses_call(curses.init_pair, PAIR_NORMAL, CUSTOM_FG, CUSTOM_BG)
        _safe_curses_call(curses.init_pair, PAIR_HIGHLIGHT, CUSTOM_BG, CUSTOM_ACCENT)
        _safe_curses_call(curses.init_pair, PAIR_MUTED, CUSTOM_MUTED, CUSTOM_BG)
    else:
        _safe_curses_call(curses.init_pair, PAIR_NORMAL, curses.COLOR_CYAN, -1)
        _safe_curses_call(curses.init_pair, PAIR_HIGHLIGHT, curses.COLOR_WHITE, curses.COLOR_MAGENTA)
        _safe_curses_call(curses.init_pair, PAIR_MUTED, curses.COLOR_BLUE, -1)


def _color_attr(pair: int) -> int:
    if not curses.has_colors():
        return curses.A_NORMAL
    try:
        return curses.color_pair(pair)
    except curses.error:
        return curses.A_NORMAL


def _line_attr(line: str, row: int) -> int:
    if line.startswith(">"):
        return _color_attr(PAIR_HIGHLIGHT) | curses.A_BOLD
    if row < 3 or line.startswith("CLI:") or line.endswith(":"):
        return _color_attr(PAIR_MUTED)
    return _color_attr(PAIR_NORMAL)


def _init_screen(stdscr) -> None:
    _safe_curses_call(curses.curs_set, 0)
    _safe_curses_call(stdscr.keypad, True)
    _init_colors()
    _safe_curses_call(stdscr.bkgd, " ", _color_attr(PAIR_NORMAL))


def _draw(stdscr, state: TuiState) -> None:
    _safe_curses_call(stdscr.bkgd, " ", _color_attr(PAIR_NORMAL))
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if width <= 1:
        return
    for row, line in enumerate(render_lines(state)[:height]):
        attr = _line_attr(line, row)
        stdscr.addnstr(row, 0, line, width - 1, attr)
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


def _read_prompt(stdscr, prompt: str) -> str:
    curses.echo()
    try:
        height, width = stdscr.getmaxyx()
        stdscr.move(height - 1, 0)
        stdscr.clrtoeol()
        stdscr.addnstr(height - 1, 0, prompt, max(0, width - 1))
        return stdscr.getstr(height - 1, len(prompt), max(1, width - len(prompt) - 1)).decode("utf-8", "replace")
    finally:
        curses.noecho()


def _run_command_prompt(stdscr, state: TuiState) -> TuiState:
    command = _read_prompt(stdscr, "skills-manager ")
    result = execute_cli_command(command)
    return show_cli_result(state, command, result)


def _collect_action_values(stdscr, action_id: str) -> tuple[dict[str, Any] | None, str | None]:
    action = TUI_ACTIONS[action_id]
    values: dict[str, Any] = {}
    for field in action.get("fields", []):
        default = field.get("default", "")
        choices = field.get("choices")
        label = field["label"]
        suffix = ""
        if choices:
            suffix += f" ({'/'.join(choices)})"
        if default:
            suffix += f" [{default}]"
        raw = _read_prompt(stdscr, f"{label}{suffix}: ")
        value = raw.strip() if raw.strip() else default
        if field.get("required") and not value:
            return None, f"Required field missing: {field['name']}"
        if choices and value not in choices:
            return None, f"Invalid {field['name']}: {value}; choose one of {', '.join(choices)}"
        values[field["name"]] = value
    return values, None


def _begin_section_action_prompt(stdscr, state: TuiState, action_id: str) -> TuiState:
    values, error = _collect_action_values(stdscr, action_id)
    if error:
        return replace(state, status=error)
    return begin_tui_action(state, action_id, values or {})


def _main(stdscr) -> int:
    _init_screen(stdscr)
    state = initial_tui_state()
    while True:
        _draw(stdscr, state)
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return 0
        if key == ord(":"):
            state = _run_command_prompt(stdscr, state)
            continue
        if state.mode == "detail":
            if is_back_key(key):
                state = close_detail(state)
            elif state.pending_action and state.pending_action.get("confirmation") == "single_key" and key == ord("y"):
                state = confirm_pending_action(state)
            elif state.pending_action and state.pending_action.get("confirmation") == "typed" and key == ord("y"):
                preview = state.pending_action.get("preview", {})
                action_id = preview.get("action_id") or state.pending_action.get("kind")
                expected = {
                    "migrate": "MIGRATE",
                    "restore": "RESTORE",
                    "rollback": "ROLLBACK",
                    "preset_delete": "DELETE",
                    "preset_apply": "APPLY",
                }.get(action_id, "MIGRATE")
                typed = _read_prompt(stdscr, f"type {expected} to apply: ")
                state = confirm_pending_action(state, typed)
            elif state.pending_action:
                state = replace(state, status="Pending preview · press y to confirm or b/backspace to cancel")
            elif is_enter_key(key):
                state = refresh_detail(state)
            elif (action_id := action_id_for_key(state.detail_item, key)):
                state = _begin_section_action_prompt(stdscr, state, action_id)
            elif state.detail_item == "First-run wizard" and key == ord("i"):
                state = begin_first_run_action(state, "import")
            elif state.detail_item == "First-run wizard" and key == ord("m"):
                state = begin_first_run_action(state, "migrate")
            elif state.detail_item == "First-run wizard" and key == ord("p"):
                state = begin_first_run_action(state, "materialize")
            elif state.detail_item == "First-run wizard" and key == ord("d"):
                state = begin_first_run_action(state, "doctor")
            elif state.pending_action and state.pending_action.get("kind") == "materialize" and key == ord("a"):
                state = replace(state, status="Materialize preview · press y to apply · b/backspace cancels")
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            state = move_selection(state, 1)
        elif key in (curses.KEY_UP, ord("k")):
            state = move_selection(state, -1)
        elif key in (ord("\t"),):
            state = cycle_client_mode(state)
        elif is_enter_key(key):
            state = open_selected_item(state)
        elif key == ord("/"):
            state = _read_filter(stdscr, state)


def run() -> int:
    return curses.wrapper(_main)
