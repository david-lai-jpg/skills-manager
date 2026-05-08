# Node/TypeScript Port Compatibility Matrix

This is the contract for the Node/TypeScript runtime. It freezes the observable
behavior that the TypeScript implementation must keep before it writes real user
state.

The TypeScript runtime is now the checked-out wrapper. TypeScript tests own the
safety contracts.

## Non-negotiable port rules

- Existing `~/.agents/skills-store` state must work without migration.
- During the strangler phase, TypeScript may write only fixture/temp homes.
- Rendered Claude/Codex directories are never source of truth.
- `pre-migration-backup` is the only backup command that raw-copies rendered
  Claude/Codex skill directories.
- `restore` must not materialize automatically.
- `materialize` must refuse unmanaged rendered conflicts.
- `rollback` must remove only manager-owned rendered entries.
- Dry-runs and previews must not append to the action log.
- Logs, fixtures, and docs must not contain secrets, tokens, cookies, or `.env`
  values.

## Dependency baseline

| Area | Approved choice | Reason |
| --- | --- | --- |
| Runtime | Node 24 LTS | Stable current LTS target for the port. |
| Module system | ESM | Matches modern Node tooling and `skill-installer`. |
| Language mode | strict TypeScript | Compile-time guardrails for filesystem contracts. |
| CLI parser | Commander | Stable command tree, help, flags, and exit behavior. |
| Schema validation | Zod | Separate permissive legacy reads from strict writes. |
| Prompt UX | Ink + React | Keyboard TUI with direct core execution and scrollable output. |
| Color | `picocolors` | Small, stable, already used by `skill-installer`. |
| Tests | Node test runner by default | Avoid extra dependency unless TypeScript ergonomics require more. |
| Full-screen UI | Ink | Approved terminal UI framework. |

## Compatibility matrix

Every row needs TypeScript tests before the TypeScript binary can remain the
default `skills-manager`.

| ID | Command / capability | Required args and variants | Exit contract | Output contract | Filesystem contract | Log contract | Required fixtures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C001 | `scan` | default, `--json`, `--project <path>` | `0` for successful scan | JSON mode must remain machine-readable; text mode may be human-readable | Detect inbox, store, Claude/Codex global, project dirs, symlink skills, broken symlinks, missing `SKILL.md` | No action log | `empty-home`, `inbox-import`, `broken-symlink`, `missing-skill-md` |
| C002 | `import` | default apply, `--dry-run` | `0` when candidates are valid; nonzero on invalid mutation | JSON includes dry-run status, candidates/applied entries, skill IDs | Copies inbox skills into managed store; does not enable them; dry-run writes nothing | Apply logs; dry-run does not log | `inbox-import`, `action-log-dry-run` |
| C003 | `adopt` | `adopt <path>` | `0` on valid skill path; nonzero for non-skill path | JSON includes copied skill metadata or error | Copies external skill into store; original remains; desired state unchanged | Apply logs | `adopt-enable-materialize`, `missing-skill-md` |
| C004 | `migrate` | `--dry-run`, `--apply` | `0` for valid plan/apply; nonzero for conflicts | JSON includes merge/fork actions and compatibility | Copies existing Claude/Codex skills into store; never deletes originals; dry-run writes nothing | Apply logs; dry-run does not log | `migrate-merge-fork`, `action-log-dry-run` |
| C005 | `state` | default, `--client claude`, `--client codex`, `--client all`, `--project`, `--json` | `0` for readable state | JSON shape remains stable for automation | Reads store and manifests only | No action log | `adopt-enable-materialize`, `project-session-manifests` |
| C006 | `enable` | `--scope global`, `--scope project --project`, `--scope session`, `--client all/claude/codex` | `0` on known skill; nonzero on unknown/ambiguous skill | JSON includes manifest path and changed entries | Mutates desired state only; does not render | Apply logs | `adopt-enable-materialize`, `project-session-manifests` |
| C007 | `disable` | same variants as `enable` | `0` on known skill; nonzero on unknown/ambiguous skill | JSON includes manifest path and changed entries | Disable masks win within their scope; does not render | Apply logs | `project-session-manifests` |
| C008 | `materialize` | default, `--client claude/codex/all`, `--project`, `--dry-run` | `0` when safe; nonzero on unmanaged/mismatched conflicts | JSON includes diff, actions, transaction ID on apply | Writes transaction before mutation; creates symlink or copy fallback; refuses unmanaged conflicts | Apply logs transaction ID; dry-run does not log | `adopt-enable-materialize`, `unmanaged-conflict`, `symlink-fallback-copy` |
| C009 | `diff` | default, `--client claude/codex/all`, `--project` | `0` for computed diff | JSON/text reports desired-vs-rendered changes and conflicts | No writes | No action log | `adopt-enable-materialize`, `unmanaged-conflict` |
| C010 | `doctor` | default, `--project` | `0` when no blocking issues; nonzero only for blocking failures | Reports store, rendered dirs, desired-vs-actual, preset validity, broken links | No writes | No action log | `broken-symlink`, `missing-skill-md`, `malformed-doctorable-preset`, `preset-alias-drift` |
| C011 | `rollback` | `rollback <transaction_id>` | `0` when rollback succeeds; nonzero for unknown/unsafe transaction | JSON includes removed/skipped entries | Removes only manager-owned symlinks or marked copies; never deletes source skills | Apply logs | `rollback-after-failure`, `symlink-fallback-copy` |
| C012 | `backup` | default dry-run behavior, `--dry-run`, `--export <path>` | `0` for preview/export | JSON reports included roots and rendered metadata | Exports store, manifests, presets, transactions, logs, inbox; rendered dirs are metadata only | Apply logs only for export | `backup-restore-rendered-metadata` |
| C013 | `pre-migration-backup` | default dry-run behavior, `--dry-run`, `--export <path>` | `0` for preview/export | JSON reports raw copy sources, destinations, and existence | Raw-copies full `~/.claude/skills`, `$CODEX_HOME/skills` or `~/.codex/skills`, and `~/.agents/skills`; separate from restoreable managed backup | No action log | `backup-restore-rendered-metadata` |
| C014 | `restore` | `--from <path> --dry-run`, `--from <path> --apply` | `0` for valid preview/apply; nonzero on invalid backup | JSON reports changes and materialize-afterward requirement | Restores managed state and inbox; never materializes rendered dirs automatically | Apply logs; dry-run does not log | `backup-restore-rendered-metadata` |
| C015 | `preset list` | no args | `0` | JSON/list shape remains stable | No writes | No action log | `preset-crud-apply`, `malformed-doctorable-preset` |
| C016 | `preset show` | `show <name>` | `0` for known preset; nonzero unknown | JSON includes enriched entries where resolvable | No writes | No action log | `preset-crud-apply`, `preset-alias-drift` |
| C017 | `preset create` | default, `--dry-run`, `--description`, repeated `--tag`, `--from-scope global/project`, `--project` | `0` on valid create/capture; nonzero duplicate/invalid | JSON includes preview/write path and captured entries | Writes one preset JSON on apply; dry-run writes nothing | Apply logs; dry-run does not log | `preset-crud-apply`, `action-log-dry-run` |
| C018 | `preset add` | `--mode enable/disable`, `--dry-run`, multiple refs | `0` when all refs resolve; nonzero and atomic on unknown/ambiguous refs | JSON includes before/after entries | Mutates only preset JSON; dry-run writes nothing | Apply logs; dry-run does not log | `preset-crud-apply`, `preset-unknown-id`, `action-log-dry-run` |
| C019 | `preset remove` | same variants as `preset add` | `0` when target entries exist or no-op | JSON includes before/after entries | Mutates only preset JSON; dry-run writes nothing | Apply logs; dry-run does not log | `preset-crud-apply` |
| C020 | `preset rename` | preview default, `--apply` | `0` for preview/apply; nonzero conflict/unknown | JSON distinguishes preview from apply | Apply renames one preset JSON; preview writes nothing | Apply logs only with `--apply` | `preset-crud-apply` |
| C021 | `preset delete` | preview default, `--apply` | `0` for preview/apply; nonzero unknown | JSON distinguishes preview from apply | Deletes only preset JSON; never skills/manifests/rendered/transactions/backups | Apply logs only with `--apply` | `preset-crud-apply` |
| C022 | `preset apply` | `--scope global/project`, `--project`, `--client all/claude/codex`, `--replace`, `--dry-run` | `0` for valid apply; nonzero unknown IDs and atomic failure | JSON includes before/after manifest state | Mutates manifests only; does not materialize; replace clears selected buckets only | Apply logs; dry-run does not log | `preset-crud-apply`, `preset-unknown-id`, `action-log-dry-run` |
| C023 | Bare `skills-manager` control panel / bare tui | no args | `0` on clean exit | Interactive, not machine-readable | Must expose every CLI capability with preview/confirmation | Preview flows do not log | `empty-home`, `inbox-import`, `preset-crud-apply`, `rollback-after-failure` |

## Rollback compatibility gates

These gates protect users across the historical cutover:

1. Existing transaction journals remain TypeScript rollback-compatible.
2. TypeScript failed/partial materialize leaves a doctorable rollback journal.
3. Manager-marked copy fallback renders remain safe for TypeScript ownership detection.
4. Dry-runs and previews still never append to the action log.

## Fixture coverage checklist

The parity fixture manifest at `tests/fixtures/parity/manifest.json` is the
machine-readable index for the suite. It must include at least these scenarios:

- `empty-home`
- `inbox-import`
- `adopt-enable-materialize`
- `migrate-merge-fork`
- `project-session-manifests`
- `unmanaged-conflict`
- `broken-symlink`
- `missing-skill-md`
- `symlink-fallback-copy`
- `rollback-after-failure`
- `backup-restore-rendered-metadata`
- `preset-crud-apply`
- `preset-alias-drift`
- `preset-unknown-id`
- `malformed-doctorable-preset`
- `action-log-dry-run`
