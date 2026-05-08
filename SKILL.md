---
name: skills-manager
description: Manage Claude and Codex Agent Skills through a TUI-first canonical store, manifests, presets, materialization, backup, restore, action logs, and rollback.
---

# skills-manager

Use this skill when the user wants to inspect, import, migrate, enable, disable, preset, materialize, back up, restore, or troubleshoot Agent Skills across Claude and Codex.

## Operating contract

- Delegate filesystem work to `bin/skills-manager`; do not hand-edit rendered client skill directories.
- Use `bin/skills-manager` for the Ink React TUI and TypeScript CLI. `bin/skills-manager-ts` is an equivalent compatibility wrapper.
- Treat `~/.agents/skills-store` as the managed source of truth.
- Treat `~/.agents/skills-store/presets` as reusable snapshot templates, not live profiles.
- Treat `~/.agents/skills-store/logs/actions.jsonl` as the applied-mutation action log.
- Treat `~/.agents/skills` as an inbox for external installers such as `npx skills add`.
- Treat `~/.claude/skills` and `$CODEX_HOME/skills` or `~/.codex/skills` as rendered outputs.
- Import/adopt/migrate means "managed", not "enabled".
- Enable/disable changes desired state; `materialize` renders desired state.
- Preset apply stamps normal manifests; it does not materialize or create hidden provenance.
- Report Codex changes as next-session-safe unless the current runtime proves hot reload.
- Use a temporary `SKILLS_MANAGER_HOME` for smokes unless the user explicitly targets real state.
- Current desired-state scopes are `global`, `project`, and `session`; do not mention `profile` as supported.

## Common commands

```bash
skills-manager
skills-manager scan --json
skills-manager import --dry-run
skills-manager migrate --dry-run
skills-manager state --client all --json
skills-manager enable <skill-id-or-alias> --scope global --client all
skills-manager disable <skill-id-or-alias> --scope project --client codex --project <path>
skills-manager preset list
skills-manager preset show <name>
skills-manager preset create <name> [--description TEXT] [--tag TAG] [--dry-run]
skills-manager preset create <name> --from-scope global|project [--project <path>] [--dry-run]
skills-manager preset add <name> <skill...> [--mode enable|disable] [--dry-run]
skills-manager preset remove <name> <skill...> [--mode enable|disable] [--dry-run]
skills-manager preset rename <old-name> <new-name> [--apply]
skills-manager preset delete <name> [--apply]
skills-manager preset apply <name> --scope global|project [--project <path>] [--client all|claude|codex] [--replace] [--dry-run]
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
skills-manager diff --client all
skills-manager doctor
skills-manager pre-migration-backup --dry-run
skills-manager pre-migration-backup --export <path>
skills-manager backup --dry-run
skills-manager backup --export <path>
skills-manager restore --from <path> --dry-run
skills-manager restore --from <path> --apply
```

## Wrapper behavior

1. Inspect with `skills-manager state`, `scan`, `diff`, or `doctor`.
2. If a scope or client is missing and the task is ambiguous, ask for that one missing choice.
3. Prefer dry-runs before mutating commands unless the user explicitly asked to apply.
4. Use `skills-manager preset show` before applying a named preset when the user has not already confirmed its contents.
5. After mutations, run `skills-manager doctor`; after materialization, report transaction IDs and Codex restart guidance.
6. Never overwrite unmanaged real directories; surface the conflict and manual cleanup path.
7. For local smokes, prefer `SKILLS_MANAGER_HOME=/tmp/...` so you do not touch real `~/.agents`, `~/.claude`, or `~/.codex`.
