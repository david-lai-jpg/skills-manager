import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { backupRoot, dryRunExport, exportBackup, normalizeBackup, restoreBackup } from "./backup.js";
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
  assert.equal(await normalizeBackup("~/missing", env), join(home, "missing"));
});

test("backup export and restore preserve managed store and presets but not rendered dirs", async () => {
  const sourceHome = await mkdtemp(join(tmpdir(), "sm-backup-source-"));
  const sourceEnv = { SKILLS_MANAGER_HOME: sourceHome, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(sourceHome);
  await createPreset("Starter", { env: sourceEnv, surface: "test" });
  await makeFixtureSkill(join(sourceHome, ".claude", "skills", "rendered-only"));

  const exported = await exportBackup(join(sourceHome, "exports"), sourceEnv);
  assert.equal(exported.ok, true);
  const backupRoot = exported.backup as string;
  assert.equal(JSON.parse(await readFile(join(backupRoot, "manifest.json"), "utf8")).kind, "agent-skills-backup");

  const restoreHome = await mkdtemp(join(tmpdir(), "sm-backup-restore-"));
  const restored = await restoreBackup(backupRoot, { dryRun: false, env: { SKILLS_MANAGER_HOME: restoreHome, OWNER_PREFIX: "skill.test-owner" } });
  assert.equal(restored.ok, true);
  assert.equal(JSON.parse(await readFile(join(restoreHome, ".agents", "skills-store", "skills", skillId, "skill.json"), "utf8")).id, skillId);
  assert.equal(JSON.parse(await readFile(join(restoreHome, ".agents", "skills-store", "presets", "starter.json"), "utf8")).name, "starter");
  await assert.rejects(readFile(join(restoreHome, ".claude", "skills", "rendered-only", "SKILL.md"), "utf8"));
});
