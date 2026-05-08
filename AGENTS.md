# AGENTS.md

## Mission

This repo is `skills-manager`: a TUI-first TypeScript/pnpm tool that manages Claude and Codex Agent Skills through one canonical managed store, desired-state manifests, reusable presets, safe materialization into client skill directories, action logs, backups, restores, and rollback journals.

The bare `skills-manager` command opens the Ink React control panel. CLI subcommands remain the automation and test surface.

Do not treat rendered client skill directories as source. The source of truth is the managed store plus manifests. Presets are reusable templates, not live profiles.

## Hard rules

- Prefix every shell command run through Bash/exec with `rtk`.
- Read existing files before editing them.
- Keep dependencies small and explicit. The approved runtime dependency set is in `package.json`; do not add dependencies without user approval.
- Use `trash-put` instead of irreversible deletion commands for user/global files.
- Never copy secrets, tokens, cookies, or `.env` values into docs, tests, fixtures, logs, or generated files.
- Do not hand-edit rendered skill directories under `~/.claude/skills`, `$CODEX_HOME/skills`, or `~/.codex/skills`; use `skills-manager materialize`, `diff`, `doctor`, and `rollback`.
- For commands that could mutate real user skill state, prefer dry-runs first and use a temporary `SKILLS_MANAGER_HOME` for tests/smokes unless the task explicitly targets the real machine state.

## Project shape

- CLI wrapper: `bin/skills-manager` delegates to `bin/skills-manager-ts`, which builds `dist/cli.js` if needed and runs Node.
- Package code: `src/`.
- Tests: `src/**/*.test.ts`, Node test runner.
- Package manager/runtime: pnpm with Node `>=22.0.0`; `.nvmrc` may pin a newer known-good Node for local development.
- Local configuration: `.env` is loaded with `dotenv`; `OWNER_PREFIX` controls newly generated managed skill IDs. Do not hardcode personal owner prefixes in source or tests.
- User docs: `README.md`.
- Wrapper skill docs: `SKILL.md`.
- Shared domain context: `CONTEXT.md`.
- Per-agent setup docs: `docs/agents/`.
- Task memory: `tasks/todo.md` and `tasks/lessons.md`.

## Architecture map

- `src/core/adapters.ts` maps client names to rendered directories:
  - Claude: `~/.claude/skills`
  - Codex: `$CODEX_HOME/skills` or `~/.codex/skills`
  - Project scope: `.<client>/skills` under the provided project path.
- `src/core/store.ts` owns home/store paths, skill IDs, manifests, preset/log roots, content hashing, JSON I/O, manager markers, and path-safety helpers.
- `src/core/scanner.ts` classifies physical skill-looking directories, symlinks, broken symlinks, and missing `SKILL.md` cases.
- `src/core/planner.ts` plans and applies `import`, `adopt`, and `migrate`. These copy skills into the managed store; they do not enable skills.
- `src/core/resolver.ts` resolves effective desired state from global, project, and session manifests. Disable masks win in their scope. There is no profile scope.
- `src/core/presets.ts` owns flat preset JSON files: list/show, schema validation, create/capture, add/remove, rename/delete, and apply-to-manifest behavior.
- `src/core/materializer.ts` compares desired state with rendered output and creates/removes only manager-owned rendered entries.
- `src/core/transactions.ts` writes materialization journals and rolls back manager-created render changes.
- `src/core/action-log.ts` appends JSONL records for applied CLI/core mutations. Dry-runs and previews do not log.
- `src/core/backup.ts` exports/restores the managed store, manifests, transactions, presets, logs, and inbox; rendered client directories are metadata-only in backups.
- `src/tui.tsx` owns the Ink React TUI. It exposes every CLI capability through direct core-module calls, typed confirmations for high-impact actions, scrollable output panes, and human-readable result summaries above full JSON. Keep business rules in core modules where practical; keep Ink rendering thin and test action/prompt/execution helpers directly.
- `src/cli.ts` is the Commander boundary. Bare invocation launches the TUI; subcommands expose automation. Keep business rules in modules, not buried in CLI handlers.

## State model vocabulary

Use these terms consistently:

- `~/.agents/skills-store` — managed source of truth.
- `~/.agents/skills-store/skills` — canonical managed skill copies.
- `~/.agents/skills-store/manifests` — desired-state manifests for global/project/session scopes.
- `~/.agents/skills-store/presets` — flat preset templates that can stamp enable/disable entries into manifests.
- `~/.agents/skills-store/transactions` — materialization journals used by rollback.
- `~/.agents/skills-store/logs/actions.jsonl` — append-only action log for applied mutations.
- `~/.agents/skills` — inbox for external installers such as `npx skills add`.
- `~/.claude/skills` — Claude rendered output.
- `$CODEX_HOME/skills` or `~/.codex/skills` — Codex rendered output.
- `skills-manager` — opens the Ink TUI; an empty managed store shows the scan → backup → import/migrate → enable/preset → materialize → doctor path.
- `import`, `adopt`, `migrate` — copy skills into managed state.
- `enable`, `disable` — change desired visibility in manifests.
- `preset` — reusable snapshot/template; applying a preset mutates manifests but does not materialize and does not store live provenance.
- `materialize` — render desired state into client directories.
- `diff` — compare desired state to rendered filesystem state.
- `doctor` — audit broken links, missing `SKILL.md`, conflicts, unsafe targets, desired-vs-actual problems, and preset validity.
- `rollback` — undo manager-created materialization effects from a transaction journal.

The important behavioral invariant: importing a skill does not enable it. Enabling a skill does not render it. Applying a preset does not render it. Rendering happens only through materialization.

## Development workflow

1. Reproduce or inspect with the smallest safe command.
2. Add or update a focused test before changing behavior when practical.
3. Keep changes small and in the module that owns the concept.
4. Preserve safety invariants around unmanaged directories, symlinks, transactions, rollback, presets, and action logs.
5. For TUI work, test pure helpers and light Ink render smoke behavior; avoid brittle full-terminal interaction tests unless the behavior cannot be covered another way.
6. Run targeted tests, then broader validation before claiming done.

Useful commands:

```bash
rtk pnpm run check
rtk pnpm run build
rtk pnpm test
rtk bin/skills-manager --help
rtk bin/skills-manager preset --help
rtk bin/skills-manager backup --dry-run
rtk bin/skills-manager doctor
```

For CLI smoke tests that should not touch real user state:

```bash
rtk sh -c 'SKILLS_MANAGER_HOME=/tmp/skills-manager-smoke bin/skills-manager doctor'
```

Prefer `mkdtemp`/temporary homes in tests and set `SKILLS_MANAGER_HOME` inside the test.

## Safety invariants to protect

- `materialize` must refuse unmanaged or mismatched rendered conflicts instead of overwriting them.
- Removals must go through manager ownership checks: symlinks must point under `store.skills_root()`, copied directories must contain `.skills-manager.json` with `manager: skills-manager`.
- Transaction journals must be written before materialization mutates rendered directories.
- Rollback must remove only manager-created rendered entries and must not delete original source skills.
- Backup/restore must treat rendered Claude/Codex outputs as metadata, not canonical state.
- Backup/restore must include presets and action logs with the managed store.
- `copySkillTree` and `contentHash` must ignore local junk such as `.git`, `__pycache__`, `.pytest_cache`, and `.DS_Store`.
- Manifest writes should remain atomic via `writeJson` and stable via sorted JSON keys.
- Preset writes should be atomic, schema-validated, alias-resolved when mutating entries, and dry-runnable where the CLI advertises dry-run behavior.
- Preset delete removes only the preset JSON definition; it must not remove managed skills, manifests, rendered output, transactions, or backups.
- Preset apply supports global/project only, writes normal manifests, does not materialize, and does not create a hidden live profile/provenance system.
- Dry-runs and TUI previews must not append to `logs/actions.jsonl`.
- Applied mutations should append useful action-log entries without leaking secrets or environment values.
- High-impact TUI flows such as migrate, restore, preset delete, and rollback should keep explicit confirmation semantics.

## Testing expectations

- Behavior changes need tests under `src/**/*.test.ts` unless the change is docs-only.
- Cover filesystem mutation behavior with temporary homes, not real `~/.agents`, `~/.claude`, or `~/.codex`.
- Test both dry-run and apply paths when adding a mutating command.
- Test conflict refusal and rollback behavior for changes near materialization.
- Test preset schema/validation, alias drift, dry-runs, merge/replace apply behavior, and backup/restore inclusion when touching presets.
- Test action-log presence for applied mutations and absence for dry-runs/previews when touching logging.
- Test TUI behavior through action/prompt/execution helpers and light Ink render smokes. Cover prompt/confirmation contracts, output scrolling, and result summaries when touching the TUI.
- Do not weaken or remove safety tests to make a run pass; fix the implementation or explain why the test contract changed.

## Style conventions

- Keep modules small and boring; this tool is filesystem plumbing, not a framework audition.
- Prefer explicit dictionaries/lists and readable `Path` operations over clever abstractions.
- Emit machine-readable JSON for CLI commands unless an existing command already intentionally emits text.
- Keep command names and README terminology aligned with the state model vocabulary above.
- Keep README, `SKILL.md`, `CONTEXT.md`, and this file synchronized when changing user-visible behavior.
- On macOS, do not use bare `sed -i`; use `perl -pi -e`, a temp-file rewrite, or an editor patch.
- For generated scripts, start real Bash scripts with `set -euo pipefail`; do not add that to fragile one-off inspection commands.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `david-lai-jpg/skills-manager` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock skill triage labels unchanged. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses the single-context domain docs layout. See `docs/agents/domain.md`.
