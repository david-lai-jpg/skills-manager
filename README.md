# skills-manager

CLI-first Agent Skill manager for Claude and Codex.

It gives you one managed skill store and renders selected skills into each
client’s real skill directory.

No MCP. No GUI.

## Mental model

There are three different places. Do not mush them together.

| Path | Meaning |
| --- | --- |
| `~/.agents/skills-store` | Managed source of truth. Skills live here after import/adopt/migrate. |
| `~/.agents/skills` | Inbox for external installers such as `npx skills add`. Not the Codex canonical directory. |
| `~/.claude/skills` | Claude rendered output. `skills-manager` creates symlinks/copies here. |
| `$CODEX_HOME/skills` or `~/.codex/skills` | Codex rendered output. `skills-manager` creates symlinks/copies here. |

Important rule:

```text
import/adopt/migrate = managed
enable/disable       = desired visibility
materialize          = render desired visibility into Claude/Codex
```

Importing a skill does **not** enable it. That is intentional.

## Install state on this machine

This repo is expected at:

```bash
/Users/{userName}/Projects/skills-manager
```

The CLI tool **is installed** on this machine as a symlink:

```bash
~/.local/bin/skills-manager
```

Current install:

```text
~/.local/bin/skills-manager -> /Users/{userName}/Projects/skills-manager/bin/skills-manager
```

That means it runs directly from this repo checkout. There is no pip package,
Homebrew formula, npm package, or generated shim involved. It is just:

```bash
ln -s /Users/{userName}/Projects/skills-manager/bin/skills-manager ~/.local/bin/skills-manager
```

The wrapper skill is linked into:

```bash
~/.claude/skills/skills-manager
~/.codex/skills/skills-manager
```

Check it:

```bash
which skills-manager
skills-manager --help
```

Expected:

```text
/Users/{userName}/.local/bin/skills-manager
```

If the command is missing, repair the install:

```bash
mkdir -p ~/.local/bin
ln -s /Users/{userName}/Projects/skills-manager/bin/skills-manager ~/.local/bin/skills-manager
```

If the symlink already exists but points somewhere wrong, inspect it first:

```bash
ls -la ~/.local/bin/skills-manager
```

Then replace only if you are sure:

```bash
trash-put ~/.local/bin/skills-manager
ln -s /Users/{userName}/Projects/skills-manager/bin/skills-manager ~/.local/bin/skills-manager
```

## First-time use: migrate existing Claude/Codex skills

Use this when you already have skills in `~/.claude/skills` and/or
`~/.codex/skills` and want `skills-manager` to adopt them into the managed
store.

### 1. Preview the migration

```bash
skills-manager migrate --dry-run
```

This scans existing Claude/Codex skill dirs and prints the copy plan.

It does not move or delete originals.

### 2. Apply the migration

```bash
skills-manager migrate --apply
```

This copies skills into:

```bash
~/.agents/skills-store/skills/
```

Same-name/same-content skills are merged. Same-name/different-content skills
are forked into deterministic IDs such as:

```text
skill.{userName}.<name>.claude
skill.{userName}.<name>.codex
```

### 3. Inspect managed state

```bash
skills-manager state --client all --json
```

At this point the migrated skills are managed, but not necessarily enabled.

### 4. Enable the skill you want visible

Enable everywhere:

```bash
skills-manager enable <skill-alias-or-id> --scope global --client all
```

Enable Codex only:

```bash
skills-manager enable <skill-alias-or-id> --scope global --client codex
```

Enable Claude only:

```bash
skills-manager enable <skill-alias-or-id> --scope global --client claude
```

Example:

```bash
skills-manager enable eli5 --scope global --client all
```

### 5. Preview rendering

```bash
skills-manager materialize --client all --dry-run
```

This shows what symlinks/copies would be created or removed.

### 6. Render into Claude/Codex

```bash
skills-manager materialize --client all
```

For Codex, restart the Codex session after materializing. Codex skill loading is
not reliably hot-reloaded.

### 7. Verify

```bash
skills-manager doctor
skills-manager diff --client all
```

## Initial use: import inbox skills

Use this when an external installer wrote skills into:

```bash
~/.agents/skills
```

Preview:

```bash
skills-manager import --dry-run
```

Apply:

```bash
skills-manager import
```

Then enable and materialize:

```bash
skills-manager enable <skill-alias-or-id> --scope global --client all
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
```

## Initial use: adopt one skill directory

Use this when you have one skill somewhere random and want to bring it under
management.

```bash
skills-manager adopt /path/to/my-skill
skills-manager enable my-skill --scope global --client all
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
```

The source directory is copied into the store. It is not moved.

## Continued use

### See what exists

```bash
skills-manager scan --json
```

### See effective enabled/disabled state

```bash
skills-manager state --client all --json
```

### Enable a skill globally

```bash
skills-manager enable <skill> --scope global --client all
skills-manager materialize --client all
```

### Disable a skill globally

```bash
skills-manager disable <skill> --scope global --client all
skills-manager materialize --client all
```

### Disable a globally enabled skill for one project

```bash
skills-manager disable <skill> --scope project --project /path/to/project --client all
skills-manager materialize --client all --project /path/to/project
```

Disable masks win inside their scope. So a skill can be globally enabled but
project-disabled.

### Codex-only or Claude-only masks

```bash
skills-manager disable <skill> --scope global --client codex
skills-manager enable <skill> --scope global --client claude
skills-manager materialize --client all
```

## Diff and doctor

Show desired-vs-rendered differences:

```bash
skills-manager diff --client all
```

Run safety checks:

```bash
skills-manager doctor
```

`doctor` checks for broken links, missing store entries, unmanaged conflicts,
and unsafe target dirs.

## Transactions and rollback

Every non-dry-run materialization writes a transaction journal first:

```bash
~/.agents/skills-store/transactions/
```

If a materialization goes sideways, rollback by transaction ID:

```bash
skills-manager rollback <transaction-id>
```

Rollback only removes manager-created rendered entries. It refuses to delete
unmanaged directories.

## Backup and restore

Create a portable backup preview:

```bash
skills-manager backup --dry-run
```

Export a backup:

```bash
skills-manager backup --export ~/Downloads
```

This creates:

```bash
~/Downloads/agent-skills-backup/
```

Restore preview:

```bash
skills-manager restore --from ~/Downloads/agent-skills-backup --dry-run
```

Restore:

```bash
skills-manager restore --from ~/Downloads/agent-skills-backup --apply
skills-manager materialize --client all
skills-manager doctor
```

Restore copies the managed store and inbox. It does not treat rendered
Claude/Codex symlink outputs as canonical state.

## Safe defaults

- Prefers symlinks.
- Falls back to copy if symlinks fail.
- Never overwrites unmanaged real directories.
- Only removes entries previously created by `skills-manager`.
- Mutations are dry-runnable or rollbackable.
- Import/adopt/migrate never auto-enable skills globally.

## Common workflows

### Migrate everything, then enable one skill

```bash
skills-manager migrate --dry-run
skills-manager migrate --apply
skills-manager state --client all --json
skills-manager enable <skill> --scope global --client all
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
skills-manager doctor
```

### Add a new skill from inbox

```bash
skills-manager import --dry-run
skills-manager import
skills-manager enable <skill> --scope global --client all
skills-manager materialize --client all
```

### Make a skill Codex-only

```bash
skills-manager enable <skill> --scope global --client codex
skills-manager disable <skill> --scope global --client claude
skills-manager materialize --client all
```

### Preview everything before touching rendered dirs

```bash
skills-manager scan --json
skills-manager state --client all --json
skills-manager diff --client all
skills-manager materialize --client all --dry-run
```

## Troubleshooting

### “I imported a skill but Codex cannot see it”

Import only makes it managed. You still need:

```bash
skills-manager enable <skill> --scope global --client codex
skills-manager materialize --client codex
```

Then restart Codex.

### “materialize refuses a conflict”

That means the target rendered path already exists and was not created by
`skills-manager`.

The tool is refusing to stomp your stuff. Move or inspect that path manually,
then rerun:

```bash
skills-manager materialize --client all --dry-run
```

### “doctor reports existing junk”

`doctor` audits the whole rendered skill directory. If you had old broken
symlinks or non-skill dirs before `skills-manager`, it will report them.

That does not necessarily mean the managed store is broken.

### “Codex still shows old skill state”

Restart Codex. This is not reliably hot-loaded.

## Command reference

### `scan`

Inspect skill-related directories and classify what is there.

```bash
skills-manager scan [--json]
```

What it reads:

- `~/.agents/skills`
- `~/.agents/skills-store/skills`
- `~/.claude/skills`
- `$CODEX_HOME/skills` or `~/.codex/skills`
- optional project skill dirs when `--project` is provided

What it reports:

- normal skill dirs
- symlinks
- broken symlinks
- dirs missing `SKILL.md`
- duplicate names
- duplicate content hashes

What it mutates: nothing.

Use it when you want to know what physical files exist before doing anything
else.

### `import`

Adopt unmanaged skills from the inbox:

```bash
~/.agents/skills
```

```bash
skills-manager import --dry-run
skills-manager import
```

What it does:

- scans `~/.agents/skills`
- finds skill dirs not already in the managed store
- copies them into `~/.agents/skills-store/skills/<stable-id>/`
- writes `skill.json`

What it does **not** do:

- does not enable the skill
- does not render it into Claude/Codex
- does not delete the inbox copy

Use it after external installers such as `npx skills add` drop skills into
`~/.agents/skills`.

### `adopt`

Copy one explicit skill directory into the managed store.

```bash
skills-manager adopt /path/to/my-skill
```

What it does:

- verifies `/path/to/my-skill/SKILL.md` exists
- computes a deterministic content hash
- copies the skill into `~/.agents/skills-store/skills/<stable-id>/`
- writes `skill.json`

What it does **not** do:

- does not move the source directory
- does not enable the skill
- does not render it into Claude/Codex

Use it when a skill lives somewhere random and you want `skills-manager` to own
a managed copy.

### `migrate`

Bulk-adopt existing Claude/Codex skill directories into the managed store.

```bash
skills-manager migrate --dry-run
skills-manager migrate --apply
```

What it reads:

- `~/.claude/skills`
- `$CODEX_HOME/skills` or `~/.codex/skills`

What it does:

- plans or copies existing client skills into `~/.agents/skills-store`
- merges same-name/same-content skills
- forks same-name/different-content skills into deterministic IDs
- preserves original Claude/Codex skill dirs

What it does **not** do:

- does not delete original skill directories
- does not enable migrated skills globally
- does not render new output until you run `materialize`

Use it for first-time setup when you already have a pile of Claude/Codex skills.

### `state`

Show effective desired state after applying manifests.

```bash
skills-manager state --client all --json
skills-manager state --client codex --json
skills-manager state --client claude --project /path/to/project --json
```

What it reads:

- managed skills in `~/.agents/skills-store/skills`
- global/profile/project/session manifests
- client-specific enable/disable masks

What it reports:

- which managed skills are effectively enabled
- which are disabled
- why a skill is on or off
- unknown IDs referenced by manifests

What it mutates: nothing.

Use it when you need to answer: “Should this skill be visible for this client
and scope?”

### `enable`

Add a desired-state enable entry to a manifest.

```bash
skills-manager enable <skill> --scope global --client all
skills-manager enable <skill> --scope project --project /path/to/project --client codex
```

`<skill>` can be a full skill ID or an alias when the alias resolves cleanly.

What it does:

- writes to the relevant manifest
- removes the same skill from the matching disable list in that manifest

What it does **not** do:

- does not immediately create files in Claude/Codex skill dirs
- does not restart Codex or Claude

Run `materialize` after enabling.

Use it when you want a managed skill to become visible.

### `disable`

Add a desired-state disable mask to a manifest.

```bash
skills-manager disable <skill> --scope global --client all
skills-manager disable <skill> --scope project --project /path/to/project --client codex
```

What it does:

- writes to the relevant manifest
- removes the same skill from the matching enable list in that manifest

Disable masks win inside their scope. That means a skill can be globally enabled
but disabled for one project or session.

What it does **not** do:

- does not immediately remove rendered Claude/Codex links

Run `materialize` after disabling.

Use it when you want to hide a skill without deleting it from the store.

### `materialize`

Render desired state into actual Claude/Codex skill directories.

This is the “make the filesystem match the manifests” command.

```bash
skills-manager materialize --client all --dry-run
skills-manager materialize --client all
skills-manager materialize --client codex
skills-manager materialize --client claude --project /path/to/project
```

What it reads:

- manifests
- managed skill store
- current rendered client directories

What it does:

- computes desired enabled skills for each client
- creates symlinks from Claude/Codex skill dirs to managed store entries
- removes old manager-created rendered entries that are no longer desired
- writes a transaction journal before mutating anything

What it refuses to do:

- refuses to overwrite unmanaged real directories
- refuses to delete anything it did not create

Why the name is weird:

The managed store and manifests are abstract desired state. Claude and Codex
need actual directories on disk. `materialize` turns the abstract desired state
into those physical symlinks/copies. It makes the ghost wear pants.

Use it after `enable`, `disable`, `import`, `adopt`, `migrate`, or `restore`.

For Codex, restart the session after materializing because skill loading is not
reliably hot.

### `diff`

Compare desired state with rendered filesystem state.

```bash
skills-manager diff --client all
skills-manager diff --client codex --project /path/to/project
```

What it reports:

- links/copies that should be created
- manager-created rendered entries that should be removed
- unmanaged conflicts that block materialization
- actual rendered entries

What it mutates: nothing.

Use it before `materialize` when you want a readable “what would change?” view.

### `doctor`

Run safety and consistency checks.

```bash
skills-manager doctor
skills-manager doctor --project /path/to/project
```

What it checks:

- broken symlinks
- dirs missing `SKILL.md`
- rendered conflicts
- unsafe target dirs
- desired-vs-actual problems

What it mutates: nothing.

Use it after materialization or when Claude/Codex skill loading looks wrong.

### `rollback`

Undo a previous manager-created materialization transaction.

```bash
skills-manager rollback <transaction-id>
```

Transaction journals live in:

```bash
~/.agents/skills-store/transactions/
```

What it does:

- removes symlinks/copies created by that transaction
- restores manager-created rendered links when possible

What it refuses to do:

- refuses to delete unmanaged dirs/files
- refuses to delete original source skills

Use it if a materialization did the wrong thing.

### `backup`

Create a portable backup of the managed skill system.

```bash
skills-manager backup --dry-run
skills-manager backup --export ~/Downloads
```

What it includes:

- `~/.agents/skills-store/skills`
- `~/.agents/skills-store/manifests`
- `~/.agents/skills-store/transactions`
- `~/.agents/skills` inbox
- metadata listing rendered Claude/Codex outputs

What it does **not** treat as canonical:

- rendered symlink-only output in `~/.claude/skills`
- rendered symlink-only output in `~/.codex/skills`

Use it when moving the managed skill setup to another machine.

### `restore`

Restore a backup created by `skills-manager backup`.

```bash
skills-manager restore --from ~/Downloads/agent-skills-backup --dry-run
skills-manager restore --from ~/Downloads/agent-skills-backup --apply
```

What it does:

- copies store/manifests/transactions back into `~/.agents/skills-store`
- copies inbox data back into `~/.agents/skills`

What it does **not** do:

- does not automatically render Claude/Codex outputs

After restore, run:

```bash
skills-manager materialize --client all
skills-manager doctor
```

Use it after transferring a backup to another machine or rebuilding local state.

## Quick command table

| Command | Plain-English meaning | Mutates? | Usually followed by |
| --- | --- | --- | --- |
| `scan` | What skill-looking files exist? | No | `migrate`, `import`, or `doctor` |
| `import` | Copy inbox skills into managed store | Yes | `enable`, `materialize` |
| `adopt` | Copy one explicit skill into managed store | Yes | `enable`, `materialize` |
| `migrate` | Copy existing Claude/Codex skills into managed store | Yes with `--apply` | `enable`, `materialize` |
| `state` | What should be enabled after masks? | No | `enable`/`disable` or `materialize` |
| `enable` | Mark skill as desired-visible | Yes | `materialize` |
| `disable` | Mark skill as desired-hidden | Yes | `materialize` |
| `materialize` | Make Claude/Codex dirs match desired state | Yes unless `--dry-run` | `doctor` |
| `diff` | Show desired vs actual rendered dirs | No | `materialize` |
| `doctor` | Find broken/conflicting skill state | No | fix conflicts |
| `rollback` | Undo manager-created render changes | Yes | `doctor` |
| `backup` | Export managed store/inbox | Yes with `--export` | copy backup somewhere |
| `restore` | Import managed store/inbox from backup | Yes with `--apply` | `materialize`, `doctor` |

## Raw command syntax

```bash
skills-manager scan [--json]
skills-manager import [--dry-run]
skills-manager adopt <path>
skills-manager migrate --dry-run
skills-manager migrate --apply
skills-manager state --client claude|codex|all [--project PATH] [--json]
skills-manager enable <skill> --scope global|project|session|profile [--client claude|codex|all]
skills-manager disable <skill> --scope global|project|session|profile [--client claude|codex|all]
skills-manager materialize --client claude|codex|all [--project PATH] [--dry-run]
skills-manager diff --client claude|codex|all [--project PATH]
skills-manager doctor
skills-manager rollback <transaction-id>
skills-manager backup --dry-run
skills-manager backup --export PATH
skills-manager restore --from PATH --dry-run
skills-manager restore --from PATH --apply
```
