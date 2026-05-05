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
