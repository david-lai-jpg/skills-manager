import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { backupRoot, copyIfExists, dryRunExport, dryRunPreMigrationBackup, exportBackup, exportPreMigrationBackup, normalizeBackup, preMigrationBackupRoot, restoreBackup } from "./backup.js";
import { createPreset } from "./presets.js";
import { stableId, writeSkillMeta } from "./store.js";
import { makeFixtureSkill } from "./test-helpers.js";

async function addManagedSkill(home: string, name = "eli5"): Promise<string> {
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = stableId(name, undefined, env);
  const skillPath = join(home, ".agents", "skills-store", "skills", skillId);
  await makeFixtureSkill(skillPath);
  await writeSkillMeta(
    skillId,
    {
      id: skillId,
      aliases: { claude: name, codex: name },
      compatibility: { claude: true, codex: true }
    },
    env
  );
  return skillId;
}

test("backup dry-run reports store/inbox includes and rendered metadata only", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-backup-preview-"));
  const env = { SKILLS_MANAGER_HOME: home };
  const preview = await dryRunExport(undefined, env);
  assert.match(String(preview.target), /agent-skills-backup$/);
  assert.equal((preview.include as string[]).some((path) => path.endsWith(".agents/skills-store/skills")), true);
  assert.deepEqual((preview.rendered_metadata_only as Record<string, unknown[]>).claude, []);
});

test("backup paths expand tilde with the provided temp HOME", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-backup-home-"));
  const env = { HOME: home, SKILLS_MANAGER_HOME: home };

  assert.equal(backupRoot("~/exports", env), join(home, "exports", "agent-skills-backup"));
  assert.equal(preMigrationBackupRoot("~/exports", env), join(home, "exports", "agent-skills-pre-migration-backup"));
  assert.equal(await normalizeBackup("~/missing", env), join(home, "missing"));
});

test("pre-migration backup dry-run reports raw rendered and inbox copies", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-premigration-preview-"));
  const env = { SKILLS_MANAGER_HOME: home };

  const preview = await dryRunPreMigrationBackup(undefined, env);
  assert.match(String(preview.target), /agent-skills-pre-migration-backup$/);
  assert.equal(preview.kind, "agent-skills-pre-migration-backup");
  const copies = preview.raw_copies as Array<{ name: string; from: string; to: string; exists: boolean }>;
  assert.deepEqual(copies.map((copy) => copy.name), ["claude", "codex", "agents"]);
  assert.equal(copies.some((copy) => copy.to.endsWith("raw/claude-skills")), true);
});

test("backup export and restore preserve managed store and presets but not rendered dirs", async () => {
  const sourceHome = await mkdtemp(join(tmpdir(), "sm-backup-source-"));
  const sourceEnv = { SKILLS_MANAGER_HOME: sourceHome, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(sourceHome);
  await createPreset("Starter", { env: sourceEnv, surface: "test" });
  await mkdir(join(sourceHome, ".agents", "skills", ".git"), { recursive: true });
  await writeFile(join(sourceHome, ".agents", "skills", ".git", "HEAD"), "junk");
  await writeFile(join(sourceHome, ".agents", "skills", ".gitignore"), "junk");
  await makeFixtureSkill(join(sourceHome, ".claude", "skills", "rendered-only"));

  const exported = await exportBackup(join(sourceHome, "exports"), sourceEnv);
  assert.equal(exported.ok, true);
  const backupRoot = exported.backup as string;
  assert.equal(JSON.parse(await readFile(join(backupRoot, "manifest.json"), "utf8")).kind, "agent-skills-backup");
  await assert.rejects(readFile(join(backupRoot, "inbox", "agents-skills", ".gitignore"), "utf8"));
  await assert.rejects(readFile(join(backupRoot, "inbox", "agents-skills", ".git", "HEAD"), "utf8"));

  const restoreHome = await mkdtemp(join(tmpdir(), "sm-backup-restore-"));
  const restored = await restoreBackup(backupRoot, { dryRun: false, env: { SKILLS_MANAGER_HOME: restoreHome, OWNER_PREFIX: "skill.test-owner" } });
  assert.equal(restored.ok, true);
  assert.equal(JSON.parse(await readFile(join(restoreHome, ".agents", "skills-store", "skills", skillId, "skill.json"), "utf8")).id, skillId);
  assert.equal(JSON.parse(await readFile(join(restoreHome, ".agents", "skills-store", "presets", "starter.json"), "utf8")).name, "starter");
  await assert.rejects(readFile(join(restoreHome, ".claude", "skills", "rendered-only", "SKILL.md"), "utf8"));
});

test("pre-migration backup copies full raw Claude, Codex, and agents skill dirs", async () => {
  const sourceHome = await mkdtemp(join(tmpdir(), "sm-premigration-source-"));
  const sourceEnv = { SKILLS_MANAGER_HOME: sourceHome, OWNER_PREFIX: "skill.test-owner" };
  await makeFixtureSkill(join(sourceHome, ".claude", "skills", "claude-only"));
  await makeFixtureSkill(join(sourceHome, ".codex", "skills", "codex-only"));
  await makeFixtureSkill(join(sourceHome, ".agents", "skills", "inbox-only"));
  await writeFile(join(sourceHome, ".claude", "skills", ".DS_Store"), "junk");
  await mkdir(join(sourceHome, ".claude", "skills", ".husky"), { recursive: true });
  await writeFile(join(sourceHome, ".claude", "skills", ".husky", "pre-commit"), "junk");
  await writeFile(join(sourceHome, ".codex", "skills", ".gitignore"), "junk");
  await mkdir(join(sourceHome, ".codex", "skills", ".system"), { recursive: true });
  await writeFile(join(sourceHome, ".codex", "skills", ".system", "SKILL.md"), "junk");
  await mkdir(join(sourceHome, ".agents", "skills", ".git"), { recursive: true });
  await writeFile(join(sourceHome, ".agents", "skills", ".git", "HEAD"), "junk");

  const exported = await exportPreMigrationBackup(join(sourceHome, "exports"), sourceEnv);
  assert.equal(exported.ok, true);
  const backupRoot = exported.backup as string;
  const manifest = JSON.parse(await readFile(join(backupRoot, "manifest.json"), "utf8"));
  assert.equal(manifest.kind, "agent-skills-pre-migration-backup");
  assert.equal((exported.copied as unknown[]).length, 3);
  assert.equal(await readFile(join(backupRoot, "raw", "claude-skills", "claude-only", "SKILL.md"), "utf8"), "# Skill\n");
  assert.equal(await readFile(join(backupRoot, "raw", "codex-skills", "codex-only", "SKILL.md"), "utf8"), "# Skill\n");
  assert.equal(await readFile(join(backupRoot, "raw", "agents-skills", "inbox-only", "SKILL.md"), "utf8"), "# Skill\n");
  await assert.rejects(readFile(join(backupRoot, "raw", "claude-skills", ".DS_Store"), "utf8"));
  await assert.rejects(readFile(join(backupRoot, "raw", "claude-skills", ".husky", "pre-commit"), "utf8"));
  await assert.rejects(readFile(join(backupRoot, "raw", "codex-skills", ".gitignore"), "utf8"));
  await assert.rejects(readFile(join(backupRoot, "raw", "codex-skills", ".system", "SKILL.md"), "utf8"));
  await assert.rejects(readFile(join(backupRoot, "raw", "agents-skills", ".git", "HEAD"), "utf8"));
});

test("backup copy allows managed dot roots but filters dot children", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-backup-dot-root-"));
  await makeFixtureSkill(join(home, ".agents", "skills", "visible"));
  await mkdir(join(home, ".agents", "skills", ".system"), { recursive: true });
  await writeFile(join(home, ".agents", "skills", ".system", "SKILL.md"), "junk");

  const target = join(home, "out", "agents-root");
  await copyIfExists(join(home, ".agents"), target);

  assert.equal(await readFile(join(target, "skills", "visible", "SKILL.md"), "utf8"), "# Skill\n");
  await assert.rejects(readFile(join(target, "skills", ".system", "SKILL.md"), "utf8"));
});
