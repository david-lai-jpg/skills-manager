#!/usr/bin/env node
import { Command } from "commander";
import { expandClients } from "./core/adapters.js";
import { dryRunExport, dryRunPreMigrationBackup, exportBackup, exportPreMigrationBackup, restoreBackup } from "./core/backup.js";
import { inboxDir, storeRoot } from "./core/paths.js";
import { diff as materializerDiff, materialize } from "./core/materializer.js";
import { adoptSkill, importInbox, migrateApply, migratePlan } from "./core/planner.js";
import {
  addEntries,
  applyPreset,
  capturePreset,
  createPreset,
  deletePreset,
  listPresets,
  removeEntries,
  renamePreset,
  showPreset,
  validatePresets
} from "./core/presets.js";
import { resolveDesired, resolveSkillRef, setSkill } from "./core/resolver.js";
import { scan } from "./core/scanner.js";
import { stableJson } from "./core/store.js";
import { rollback } from "./core/transactions.js";
import type { Client, Scope } from "./core/schemas.js";
import { runTui } from "./tui.js";
import { VERSION } from "./version.js";

function parseClient(value: string): Client | "all" {
  if (value === "all" || value === "claude" || value === "codex") {
    return value;
  }
  throw new Error(`unknown client: ${value}`);
}

function parseScope(value: string): Scope {
  if (value === "global" || value === "project" || value === "session") {
    return value;
  }
  throw new Error(`unknown scope: ${value}`);
}

function parsePresetScope(value: string): "global" | "project" {
  if (value === "global" || value === "project") {
    return value;
  }
  throw new Error(`unknown preset scope: ${value}`);
}

function parsePresetMode(value: string): "enable" | "disable" {
  if (value === "enable" || value === "disable") {
    return value;
  }
  throw new Error(`unknown preset mode: ${value}`);
}

function emitResult(data: Record<string, unknown>): void {
  process.stdout.write(stableJson(data));
  if (data.ok === false) {
    process.exitCode = 1;
  }
}

export function buildProgram(): Command {
  const program = new Command();
  program
    .name("skills-manager")
    .description("Manage Claude and Codex Agent Skills")
    .version(VERSION)
    .showHelpAfterError();

  program
    .command("scan")
    .option("--json")
    .option("--project <path>")
    .option("--include-non-skills", "include non-skill directories missing SKILL.md for diagnostics")
    .action(async (options: { json?: boolean; project?: string; includeNonSkills?: boolean }) => {
      process.stdout.write(stableJson(await scan({ ...(options.project ? { project: options.project } : {}), includeNonSkills: options.includeNonSkills ?? false })));
    });

  program
    .command("import")
    .option("--dry-run")
    .action(async (options: { dryRun?: boolean }) => {
      process.stdout.write(stableJson(await importInbox({ dryRun: options.dryRun ?? false })));
    });

  program
    .command("adopt")
    .argument("<path>")
    .action(async (path: string) => {
      const result = await adoptSkill(path);
      process.stdout.write(stableJson(result));
      if (!result.ok) {
        process.exitCode = 1;
      }
    });

  program
    .command("migrate")
    .option("--dry-run")
    .option("--apply")
    .action(async (options: { dryRun?: boolean; apply?: boolean }) => {
      if (options.apply) {
        process.stdout.write(stableJson(await migrateApply()));
        return;
      }
      process.stdout.write(stableJson({ ...(await migratePlan()), dry_run: true }));
    });

  program
    .command("state")
    .option("--client <client>", "claude, codex, or all", "all")
    .option("--project <path>")
    .option("--json")
    .action(async (options: { client: string; project?: string }) => {
      const client = parseClient(options.client);
      const clients = expandClients(client);
      const data = Object.fromEntries(
        await Promise.all(clients.map(async (current) => [current, await resolveDesired(current, options.project ? { project: options.project } : {})]))
      );
      process.stdout.write(stableJson(client === "all" ? data : data[client]));
    });

  for (const name of ["enable", "disable"]) {
    program
      .command(name)
      .argument("<skill>")
      .requiredOption("--scope <scope>", "global, project, or session")
      .option("--client <client>", "claude, codex, or all", "all")
      .option("--project <path>")
      .action(async (skill: string, options: { scope: string; client: string; project?: string }) => {
        const enabled = name === "enable";
        const skillId = await resolveSkillRef(skill);
        const scope = parseScope(options.scope);
        const client = parseClient(options.client);
        const manifest = await setSkill(scope, skillId, enabled, {
          client,
          ...(options.project ? { project: options.project } : {}),
          surface: "cli"
        });
        process.stdout.write(
          stableJson({
            ok: true,
            enabled,
            skill_id: skillId,
            scope,
            client,
            manifest,
            note: "Codex skill visibility may require a new Codex session."
          })
        );
      });
  }

  program
    .command("materialize")
    .option("--client <client>", "claude, codex, or all", "all")
    .option("--project <path>")
    .option("--dry-run")
    .action(async (options: { client: string; project?: string; dryRun?: boolean }) => {
      const client = parseClient(options.client);
      const results = Object.fromEntries(
        await Promise.all(
          expandClients(client).map(async (current) => [
            current,
            await materialize(current, {
              ...(options.project ? { project: options.project } : {}),
              dryRun: options.dryRun ?? false,
              surface: "cli"
            })
          ])
        )
      );
      process.stdout.write(stableJson(results));
      if (!Object.values(results).every((result) => (result as Record<string, unknown>).ok)) {
        process.exitCode = 1;
      }
    });

  program
    .command("diff")
    .option("--client <client>", "claude, codex, or all", "all")
    .option("--project <path>")
    .action(async (options: { client: string; project?: string }) => {
      const client = parseClient(options.client);
      const data = Object.fromEntries(
        await Promise.all(expandClients(client).map(async (current) => [current, await materializerDiff(current, options.project ? { project: options.project } : {})]))
      );
      process.stdout.write(stableJson(client === "all" ? data : data[client]));
    });

  program
    .command("doctor")
    .option("--project <path>")
    .action(async (options: { project?: string }) => {
      const scanned = await scan({ ...(options.project ? { project: options.project } : {}), includeNonSkills: true });
      const issues: Array<Record<string, unknown>> = [];
      for (const [location, value] of Object.entries(scanned.locations)) {
        for (const entry of value.entries) {
          if (entry.type === "broken_symlink" || entry.type === "missing_skill_md" || entry.type === "error") {
            issues.push({ location, ...entry });
          }
        }
      }
      for (const client of ["claude", "codex"] as const) {
        const d = await materializerDiff(client, options.project ? { project: options.project } : {});
        for (const conflict of d.conflicts as Array<Record<string, unknown>>) {
          issues.push({ location: `${client}_rendered`, type: "conflict", ...conflict });
        }
      }
      issues.push(...(await validatePresets()));
      emitResult({ ok: issues.length === 0, issues, store: storeRoot(), inbox: inboxDir() });
    });

  program
    .command("rollback")
    .argument("<transaction_id>")
    .action(async (transactionId: string) => {
      emitResult(await rollback(transactionId));
    });

  program
    .command("backup")
    .description("Export managed store state; rendered Claude/Codex dirs are metadata-only")
    .option("--dry-run")
    .option("--export <path>")
    .action(async (options: { dryRun?: boolean; export?: string }) => {
      if (options.dryRun || !options.export) {
        process.stdout.write(stableJson(await dryRunExport(options.export)));
        return;
      }
      emitResult(await exportBackup(options.export));
    });

  program
    .command("pre-migration-backup")
    .description("Export full raw copies of rendered Claude/Codex skill dirs and the agents inbox")
    .option("--dry-run")
    .option("--export <path>")
    .action(async (options: { dryRun?: boolean; export?: string }) => {
      if (options.dryRun || !options.export) {
        process.stdout.write(stableJson(await dryRunPreMigrationBackup(options.export)));
        return;
      }
      emitResult(await exportPreMigrationBackup(options.export));
    });

  program
    .command("restore")
    .requiredOption("--from <path>")
    .option("--dry-run")
    .option("--apply")
    .action(async (options: { from: string; dryRun?: boolean; apply?: boolean }) => {
      emitResult(await restoreBackup(options.from, { dryRun: options.dryRun || !options.apply }));
    });

  const preset = program.command("preset");
  preset.command("list").action(async () => {
    process.stdout.write(stableJson(await listPresets()));
  });
  preset.command("show").argument("<name>").action(async (name: string) => {
    process.stdout.write(stableJson(await showPreset(name)));
  });
  preset
    .command("create")
    .argument("<name>")
    .option("--description <text>")
    .option("--tag <tag>", "tag", (value: string, previous: string[]) => [...previous, value], [])
    .option("--dry-run")
    .option("--from-scope <scope>")
    .option("--project <path>")
    .action(async (name: string, options: { description?: string; tag: string[]; dryRun?: boolean; fromScope?: string; project?: string }) => {
      const base = {
        description: options.description ?? "",
        tags: options.tag,
        dryRun: options.dryRun ?? false,
        surface: "cli",
        ...(options.project ? { project: options.project } : {})
      };
      if (options.fromScope) {
        emitResult(await capturePreset(name, parsePresetScope(options.fromScope), base));
        return;
      }
      emitResult(await createPreset(name, base));
    });
  preset
    .command("add")
    .argument("<name>")
    .argument("<skills...>")
    .option("--mode <mode>", "enable or disable", "enable")
    .option("--dry-run")
    .action(async (name: string, skills: string[], options: { mode: string; dryRun?: boolean }) => {
      emitResult(await addEntries(name, skills, { mode: parsePresetMode(options.mode), dryRun: options.dryRun ?? false, surface: "cli" }));
    });
  preset
    .command("remove")
    .argument("<name>")
    .argument("<skills...>")
    .option("--mode <mode>", "enable or disable", "enable")
    .option("--dry-run")
    .action(async (name: string, skills: string[], options: { mode: string; dryRun?: boolean }) => {
      emitResult(await removeEntries(name, skills, { mode: parsePresetMode(options.mode), dryRun: options.dryRun ?? false, surface: "cli" }));
    });
  preset
    .command("rename")
    .argument("<old_name>")
    .argument("<new_name>")
    .option("--apply")
    .action(async (oldName: string, newName: string, options: { apply?: boolean }) => {
      emitResult(await renamePreset(oldName, newName, { apply: options.apply ?? false, surface: "cli" }));
    });
  preset
    .command("delete")
    .argument("<name>")
    .option("--apply")
    .action(async (name: string, options: { apply?: boolean }) => {
      emitResult(await deletePreset(name, { apply: options.apply ?? false, surface: "cli" }));
    });
  preset
    .command("apply")
    .argument("<name>")
    .requiredOption("--scope <scope>")
    .option("--project <path>")
    .option("--client <client>", "claude, codex, or all", "all")
    .option("--replace")
    .option("--dry-run")
    .action(async (name: string, options: { scope: string; project?: string; client: string; replace?: boolean; dryRun?: boolean }) => {
      emitResult(
        await applyPreset(name, parsePresetScope(options.scope), {
          ...(options.project ? { project: options.project } : {}),
          client: parseClient(options.client),
          replace: options.replace ?? false,
          dryRun: options.dryRun ?? false,
          surface: "cli"
        })
      );
    });

  return program;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  if (process.argv.length <= 2) {
    await runTui();
  } else {
    await buildProgram().parseAsync(process.argv);
  }
}
