# skills-manager Project Context

This document captures durable domain vocabulary and product decisions for `skills-manager`.
Use these terms when writing PRDs, issues, plans, tests, docs, or implementation notes.

## Mission

`skills-manager` is a dependency-light Python tool for managing Claude and Codex Agent Skills through one canonical managed store.

The product direction is now:

- **TUI-first for humans**
- **CLI retained for automation/plumbing**
- **Core modules own behavior**
- **Rendered client skill directories are never source of truth**

The tool should help users manage skills without memorizing a pile of subcommands and flags.

## Core State Model

Use these terms consistently:

- **Managed store**: canonical source of truth under `~/.agents/skills-store`.
- **Managed skills**: copied skill directories stored under the managed store.
- **Managed skill ID**: an ID invented by `skills-manager`, such as `skill.davidl.prompt-engineer`.
  Upstream skills do not have native IDs; they are just directories containing `SKILL.md`.
- **Alias**: rendered/user-facing skill name, usually derived from the source directory name.
- **Manifest**: desired-state JSON for a scope.
- **Desired state**: abstract enable/disable state in manifests.
- **Rendered output**: actual Claude/Codex skill directories.
- **Materialize**: reconcile desired state into rendered output.
- **Doctor**: audit store, manifests, presets, rendered output, conflicts, broken links, and stale references.
- **Preset**: reusable apply-once snapshot template containing enable/disable entries.
- **TUI**: full-screen interactive terminal interface; primary human control surface.

Important invariant:

> Importing/adopting/migrating a skill does not enable it. Enabling/disabling changes desired state. Materialization renders desired state into client directories.

## Current Architecture Facts

- The existing code has `global`, `profile`, `project`, and `session` scope concepts.
- `profile` support is half-wired: storage/resolver support exists, but there is no explicit `--profile` CLI UX.
- The user has not installed or depended on profile behavior.
- The redesign should **remove profile scope**, not expand it.
- Presets replace the earlier profile-overlay idea.

## Product Pivot: TUI First

The CLI is useful for scripts, but it is too much mental overhead for normal configuration.
The default bare command should open the TUI:

```bash
skills-manager
```

Existing subcommands remain available:

```bash
skills-manager doctor
skills-manager materialize --client all
skills-manager enable ...
```

`skills-manager --help` should still show CLI help.

The TUI is not a preset-only editor. It must cover the full feature surface:

- first-run scan/setup
- import inbox skills
- adopt skill from path
- migrate existing Claude/Codex skills
- state/effective-state inspection
- enable/disable
- preset CRUD and apply
- diff
- materialize
- doctor
- backup
- restore
- rollback

## TUI Design Decisions

V1 TUI decisions:

- Full-screen `curses` app.
- Stdlib-only; no Textual/Rich dependency unless explicitly approved later.
- Keyboard-only for v1.
- No mouse support in v1.
- Bare `skills-manager` launches TUI.
- Subcommands remain automation plumbing.
- TUI calls core modules directly; it should not shell out to its own CLI.
- Business rules must live in core modules, not in TUI rendering code.
- TUI rendering should be thin; state/navigation helpers should be testable without curses.

Recommended top-level sections:

- First-run wizard / setup
- Managed skills
- Presets
- Global configuration
- Project configuration
- Diff
- Materialize
- Doctor
- Backup / restore
- Rollback / transactions
- Action log

Global keys/behaviors:

- `/` filters the current list.
- Search/filter should work across skills, presets, scan results, doctor issues, transactions, and other lists.
- Client views should use tabs/modes: `all`, `claude`, `codex`.
- Incompatible skills are hidden by default with a toggle to show them.
- TUI should display equivalent CLI commands for selected actions.
- TUI should show a needs-materialize banner after desired-state changes.
- TUI should offer preview/materialize from that banner.

## First-Run Wizard Decisions

If the managed store is empty or uninitialized, bare `skills-manager` should open a first-run wizard automatically.

The wizard should:

1. Scan existing inbox, Claude, and Codex skill locations.
2. Show unmanaged skills and migration candidates.
3. Recommend/offer backup before migration/materialization.
4. Preview import/migration actions before applying.
5. Let the user pick initial global skills or presets.
6. Preview materialization.
7. Apply materialization only after confirmation.
8. Auto-run doctor after completion.
9. Open the normal dashboard.

## Mutation Safety Decisions

All TUI mutating operations must show preview/dry-run first and require confirmation.

Normal actions use single-key confirmation after preview:

- enable/disable
- preset add/remove
- materialize after preview

High-risk actions require typing an exact action word:

- restore
- rollback
- preset delete
- preset apply with replace
- migrate apply

The TUI writes confirmed enable/disable and similar edits immediately.
There is no staged dirty buffer in v1.

After high-impact operations, the TUI auto-runs doctor:

- materialize
- restore
- migrate
- first-run completion

V1 has no generic undo beyond existing materialization rollback and action-log-assisted recovery.

## Scope Editing Decisions

The TUI must distinguish:

- **effective state**: result after global/project/session resolution
- **direct scope entries**: entries physically present in the selected manifest

Project view must not become a mushy checkbox list.
It should show inherited global state separately from direct project overrides.

Project view edits project overrides only:

- Enable directly for this project.
- Disable/mask for this project.
- Remove project override.

Project view must not mutate global state.

## Preset Concept

Presets are reusable **apply-once snapshot templates**.

They are not:

- live profiles
- live inherited layers
- automatically updated project state
- provenance records

Applying a preset stamps normal enable/disable entries into the target manifest.
After stamping, users can customize the target independently.
Future preset edits do not affect previously applied targets.
To refresh a target from a changed preset, the user manually reapplies it.

V1 preset apply targets:

- global
- project

Session preset apply is deferred until a launch-wrapper/session UX exists.

## Preset Storage

Presets live under the managed store:

```text
~/.agents/skills-store/presets/
```

Preset names are flat/global:

```text
presets/vue.json
presets/angular.json
presets/default.json
```

No namespaced directories in v1.
Optional tags metadata is acceptable.

Preset files are plain JSON.
No YAML, TOML, comments, or new parsing dependencies.

Example shape:

```json
{
  "version": 1,
  "name": "vue",
  "description": "Common Vue project skills",
  "tags": ["frontend", "javascript"],
  "enable": [
    {
      "id": "skill.davidl.prompt-engineer",
      "alias": "prompt-engineer"
    }
  ],
  "disable": [],
  "clients": {
    "claude": {
      "enable": [],
      "disable": []
    },
    "codex": {
      "enable": [],
      "disable": []
    }
  }
}
```

`id` is authoritative.
`alias` is readability metadata.

## Managed Skill ID Decision

Skills themselves do not have native IDs.
`skills-manager` invents managed skill IDs when adopting/importing/migrating.

Reasons managed IDs exist:

- resolve alias collisions
- support content forks during migration
- provide stable managed-store directory names
- allow rendered aliases to differ by client
- let manifests survive alias changes

Use the phrase **managed skill ID** when clarity matters.

## Preset CRUD Decisions

Presets support full CRUD:

- list
- show
- create
- add skill entries
- remove skill entries
- rename
- delete
- apply
- capture from existing scope

Behavior:

- `preset list` returns names only.
- `preset show` emits enriched JSON with resolved skill info and issues.
- `preset create` mutates by default and supports `--dry-run`.
- `preset create --from-scope ...` captures direct manifest entries only, not effective inherited state.
- `preset add` mutates by default and supports `--dry-run`.
- `preset remove` mutates by default and supports `--dry-run`.
- `preset add/remove` accept multiple skills atomically.
- If any skill reference is unknown or ambiguous, the whole add/remove command writes nothing.
- `preset remove` targets one mode, defaulting to `enable`.
- Presets may include explicit disable masks.
- `preset rename` is dry-run by default and requires `--apply`.
- `preset delete` is dry-run by default and requires `--apply`.
- Deleting a preset removes only the preset definition and never removes managed skills, manifests, or rendered directories.

Ambiguous alias resolution must fail loudly and report candidates.
Do not pick the first match.
Do not add all matches.

## Preset Apply Decisions

Preset apply:

- handles one preset at a time in v1
- mutates by default
- supports `--dry-run`
- defaults to merge mode
- supports replace mode
- fails atomically on unknown skill IDs
- records no provenance metadata in target manifests
- does not materialize by default
- supports optional materialize shortcut if implemented

Project apply defaults to current working directory if `--project` is omitted.
Output must report the resolved project path.

Merge mode:

- Adds preset entries to touched buckets.
- Removes opposite entries in touched buckets.
- Does not clear unrelated existing entries.

Replace mode:

- Clears `enable` and `disable` in selected buckets before stamping.
- For `--client codex --replace`, clears only `clients.codex`.
- For `--client claude --replace`, clears only `clients.claude`.
- For `--client all --replace`, clears top-level, Claude, and Codex buckets.

Client-targeted apply:

- `--client codex` applies preset top-level entries plus `clients.codex` entries to the Codex bucket.
- `--client claude` applies preset top-level entries plus `clients.claude` entries to the Claude bucket.
- `--client all` applies top-level entries to top-level and client-specific entries to matching client buckets.

Dry-run output should include structured before/after and change summary.

## Presets and Backup/Restore/Doctor

Presets are managed workflow state and must be included in backup/restore.

Doctor should validate presets:

- malformed JSON/schema
- unknown managed skill IDs
- duplicate entries inside same bucket
- alias metadata drift

Unknown IDs are real issues.
Alias drift can be warning-level unless it breaks resolution.

## Action Log

Mutating core actions should write a lightweight shared action log for CLI and TUI.

Suggested location:

```text
~/.agents/skills-store/logs/actions.jsonl
```

Log applied mutations, not dry-runs.

Entries should include:

- time
- surface (`cli` or `tui`)
- action
- target scope
- project path when applicable
- client
- manifest path
- preset name when applicable
- materialization transaction ID when applicable

## Launch Wrapper / Session Future Work

Session presets are deferred.

The future direction is a launch wrapper that can bind a named session setup to a client launch:

```bash
skills-manager run codex --project /app --preset debug
skills-manager run claude --project /app --preset review
```

Until such a wrapper exists, v1 should focus on global/project presets.

## Testing Strategy

Behavior changes need tests in the stdlib `unittest` suite.

Test core behavior, not curses rendering details.

Preset core tests should cover:

- schema normalization
- create
- capture
- add/remove
- atomic multi-add/multi-remove failure
- rename dry-run/apply
- delete dry-run/apply
- list
- show
- validation
- alias resolution
- ambiguous alias failure
- unknown ID failure
- duplicate handling
- merge apply
- replace apply
- client-specific apply
- all-client apply
- dry-run before/after output
- atomic apply failure

Resolver tests should be updated for removal of profile scope.

Doctor tests should cover preset validation.

Backup/restore tests should prove presets are exported/restored as managed state.

Action-log tests should prove applied core mutations log and dry-runs do not.

TUI tests should target pure helpers:

- selection movement
- filtering
- client mode switching
- incompatible-skill toggle
- first-run wizard transitions
- confirmation classification
- needs-materialize flag behavior

Avoid brittle full curses interaction tests.

## Related Issue

PRD issue:

- https://github.com/david-lai-jpg/skills-manager/issues/1
