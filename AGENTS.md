# AGENTS.md

## Mission

This repo is `skills-manager`: a dependency-light Python CLI that manages Claude and Codex Agent Skills through one canonical managed store, desired-state manifests, safe materialization into client skill directories, backups, restores, and rollback journals.

Do not treat rendered client skill directories as source. The source of truth is the managed store plus manifests.

## Hard rules

- Prefix every shell command run through Bash/exec with `rtk`.
- Read existing files before editing them.
- Keep the project stdlib-only unless the user explicitly approves a dependency. `pyproject.toml` currently declares no runtime dependencies.
- Use `trash-put` instead of irreversible deletion commands for user/global files.
- Never copy secrets, tokens, cookies, or `.env` values into docs, tests, fixtures, logs, or generated files.
- Do not hand-edit rendered skill directories under `~/.claude/skills`, `$CODEX_HOME/skills`, or `~/.codex/skills`; use `skills-manager materialize`, `diff`, `doctor`, and `rollback`.
- For commands that could mutate real user skill state, prefer dry-runs first and use a temporary `SKILLS_MANAGER_HOME` for tests/smokes unless the task explicitly targets the real machine state.

## Project shape

- CLI wrapper: `bin/skills-manager` inserts the repo root on `sys.path` and calls `skills_manager.cli:main`.
- Package code: `skills_manager/`.
- Tests: `tests/test_skills_manager.py`, stdlib `unittest`.
- User docs: `README.md`.
- Wrapper skill docs: `SKILL.md`.
- Per-agent setup docs: `docs/agents/`.

## Architecture map

- `skills_manager/adapters.py` maps client names to rendered directories:
  - Claude: `~/.claude/skills`
  - Codex: `$CODEX_HOME/skills` or `~/.codex/skills`
  - Project scope: `.<client>/skills` under the provided project path.
- `skills_manager/store.py` owns home/store paths, skill IDs, manifests, content hashing, JSON I/O, manager markers, and path-safety helpers.
- `skills_manager/scanner.py` classifies physical skill-looking directories, symlinks, broken symlinks, and missing `SKILL.md` cases.
- `skills_manager/planner.py` plans and applies `import`, `adopt`, and `migrate`. These copy skills into the managed store; they do not enable skills.
- `skills_manager/resolver.py` resolves effective desired state from global, profile, project, and session manifests. Disable masks win in their scope.
- `skills_manager/materializer.py` compares desired state with rendered output and creates/removes only manager-owned rendered entries.
- `skills_manager/transactions.py` writes materialization journals and rolls back manager-created render changes.
- `skills_manager/backup.py` exports/restores the managed store and inbox; rendered client directories are metadata-only in backups.
- `skills_manager/cli.py` is the argparse boundary. Keep business rules in modules, not buried in CLI handlers.

## State model vocabulary

Use these terms consistently:

- `~/.agents/skills-store` — managed source of truth.
- `~/.agents/skills` — inbox for external installers such as `npx skills add`.
- `~/.claude/skills` — Claude rendered output.
- `$CODEX_HOME/skills` or `~/.codex/skills` — Codex rendered output.
- `import`, `adopt`, `migrate` — copy skills into managed state.
- `enable`, `disable` — change desired visibility in manifests.
- `materialize` — render desired state into client directories.
- `diff` — compare desired state to rendered filesystem state.
- `doctor` — audit broken links, missing `SKILL.md`, conflicts, and desired-vs-actual problems.
- `rollback` — undo manager-created materialization effects from a transaction journal.

The important behavioral invariant: importing a skill does not enable it. Enabling a skill does not render it. Rendering happens only through materialization.

## Development workflow

1. Reproduce or inspect with the smallest safe command.
2. Add or update a focused test before changing behavior when practical.
3. Keep changes small and in the module that owns the concept.
4. Preserve safety invariants around unmanaged directories, symlinks, transactions, and rollback.
5. Run targeted tests, then broader validation before claiming done.

Useful commands:

```bash
rtk python3 -m unittest tests/test_skills_manager.py
rtk python3 -m compileall -q skills_manager tests
rtk python3 bin/skills-manager --help
rtk python3 bin/skills-manager backup --dry-run
rtk python3 bin/skills-manager doctor
```

For CLI smoke tests that should not touch real user state:

```bash
rtk env SKILLS_MANAGER_HOME=/tmp/skills-manager-smoke python3 bin/skills-manager doctor
```

Prefer `tempfile.TemporaryDirectory()` in Python tests and set `SKILLS_MANAGER_HOME` inside the test, matching the existing test style.

## Safety invariants to protect

- `materialize` must refuse unmanaged or mismatched rendered conflicts instead of overwriting them.
- Removals must go through manager ownership checks: symlinks must point under `store.skills_root()`, copied directories must contain `.skills-manager.json` with `manager: skills-manager`.
- Transaction journals must be written before materialization mutates rendered directories.
- Rollback must remove only manager-created rendered entries and must not delete original source skills.
- Backup/restore must treat rendered Claude/Codex outputs as metadata, not canonical state.
- `copy_skill_tree` and `content_hash` must ignore local junk such as `.git`, `__pycache__`, `.pytest_cache`, and `.DS_Store`.
- Manifest writes should remain atomic via `store.write_json` and stable via sorted JSON keys.

## Testing expectations

- Behavior changes need tests in `tests/test_skills_manager.py` unless the change is docs-only.
- Cover filesystem mutation behavior with temporary homes, not real `~/.agents`, `~/.claude`, or `~/.codex`.
- Test both dry-run and apply paths when adding a mutating command.
- Test conflict refusal and rollback behavior for changes near materialization.
- Do not weaken or remove safety tests to make a run pass; fix the implementation or explain why the test contract changed.

## Style conventions

- Keep modules small and boring; this tool is filesystem plumbing, not a framework audition.
- Prefer explicit dictionaries/lists and readable `Path` operations over clever abstractions.
- Emit machine-readable JSON for CLI commands unless an existing command already intentionally emits text.
- Keep command names and README terminology aligned with the state model vocabulary above.
- On macOS, do not use bare `sed -i`; use `perl -pi -e`, a temp-file rewrite, or an editor patch.
- For generated scripts, start real Bash scripts with `set -euo pipefail`; do not add that to fragile one-off inspection commands.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `david-lai-jpg/skills-manager` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default Matt Pocock skill triage labels unchanged. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses the single-context domain docs layout. See `docs/agents/domain.md`.
