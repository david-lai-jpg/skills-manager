---
name: skills-manager
description: Manage Claude and Codex Agent Skills through a CLI-first canonical store, manifests, materialization, backup, restore, and rollback.
---

# skills-manager

Use this skill when the user wants to inspect, import, migrate, enable, disable, materialize, back up, restore, or troubleshoot Agent Skills across Claude and Codex.

## Operating contract

- Delegate filesystem work to `bin/skills-manager`; do not hand-edit rendered client skill directories.
- Treat `~/.agents/skills-store` as the managed source of truth.
- Treat `~/.agents/skills` as an inbox for external installers such as `npx skills add`.
- Treat `~/.claude/skills` and `$CODEX_HOME/skills` or `~/.codex/skills` as rendered outputs.
- Import/adopt/migrate means "managed", not "enabled".
- Enable/disable changes desired state; `materialize` renders desired state.
- Report Codex changes as next-session-safe unless the current runtime proves hot reload.

## Common commands

```bash
skills-manager scan --json
skills-manager import --dry-run
skills-manager migrate --dry-run
skills-manager state --client all --json
skills-manager enable <skill-id-or-alias> --scope global --client all
skills-manager disable <skill-id-or-alias> --scope project --client codex --project <path>
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
skills-manager diff --client all
skills-manager doctor
skills-manager backup --dry-run
skills-manager backup --export <path>
skills-manager restore --from <path> --dry-run
skills-manager restore --from <path> --apply
```

## Wrapper behavior

1. Inspect with `skills-manager state`, `scan`, `diff`, or `doctor`.
2. If a scope or client is missing and the task is ambiguous, ask for that one missing choice.
3. Prefer dry-runs before mutating commands unless the user explicitly asked to apply.
4. After mutations, run `skills-manager doctor` and report transaction IDs.
5. Never overwrite unmanaged real directories; surface the conflict and manual cleanup path.
