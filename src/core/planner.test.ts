import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { adoptSkill, importInbox, migrateApply, migratePlan } from "./planner.js";
import { makeFixtureSkill } from "./test-helpers.js";
import { readJson } from "./store.js";

test("adoptSkill copies skill and does not enable it", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-adopt-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const source = join(home, "source", "eli5");
  await makeFixtureSkill(source);

  const result = await adoptSkill(source, { env });
  const meta = await readJson<Record<string, unknown>>(join(home, ".agents", "skills-store", "skills", "skill.test-owner.eli5", "skill.json"), {});

  assert.equal(result.ok, true);
  assert.equal(await readFile(join(home, ".agents", "skills-store", "skills", "skill.test-owner.eli5", "SKILL.md"), "utf8"), "# Skill\n");
  assert.equal((meta.aliases as Record<string, string>).codex, "eli5");
});

test("importInbox dry-run detects unmanaged inbox skills and apply adopts them", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-import-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  await makeFixtureSkill(join(home, ".agents", "skills", "eli5"));

  const preview = await importInbox({ dryRun: true, env });
  assert.equal((preview.candidates as unknown[]).length, 1);

  const applied = await importInbox({ dryRun: false, env });
  assert.equal((applied.adopted as Array<Record<string, unknown>>)[0]?.skill_id, "skill.test-owner.eli5");
});

test("migratePlan merges identical aliases and forks different content", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-migrate-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  await makeFixtureSkill(join(home, ".claude", "skills", "same"), "identical\n");
  await makeFixtureSkill(join(home, ".codex", "skills", "same"), "identical\n");
  await makeFixtureSkill(join(home, ".claude", "skills", "fork-me"), "claude\n");
  await makeFixtureSkill(join(home, ".codex", "skills", "fork-me"), "codex\n");

  const plan = await migratePlan({ env });
  const kinds = plan.actions.map((action) => action.kind);

  assert.deepEqual(kinds, ["fork", "fork", "merge"]);
});

test("migrateApply copies without moving originals", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-migrate-apply-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const source = join(home, ".claude", "skills", "one");
  await makeFixtureSkill(source);

  const result = await migrateApply({ env });

  assert.equal((result.applied as Array<Record<string, unknown>>)[0]?.ok, true);
  assert.equal(await readFile(join(source, "SKILL.md"), "utf8"), "# Skill\n");
  assert.equal(await readFile(join(home, ".agents", "skills-store", "skills", "skill.test-owner.one", "SKILL.md"), "utf8"), "# Skill\n");
});
