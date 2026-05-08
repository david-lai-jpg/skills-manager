import test from "node:test";
import assert from "node:assert/strict";
import { lstat, mkdtemp, mkdir, readlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { diff, materialize } from "./materializer.js";
import { setSkill } from "./resolver.js";
import { rollback } from "./transactions.js";
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

test("materialize creates manager-owned links and rollback removes them", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-materialize-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);
  await setSkill("global", skillId, true, { env, surface: "test" });

  const preview = await materialize("claude", { env, dryRun: true });
  assert.equal(preview.ok, true);
  assert.equal((preview.actions as unknown[]).length, 1);

  const applied = await materialize("claude", { env, surface: "test" });
  assert.equal(applied.ok, true);
  const rendered = join(home, ".claude", "skills", "eli5");
  assert.equal((await lstat(rendered)).isSymbolicLink(), true);
  assert.match(await readlink(rendered), /skill\.test-owner\.eli5$/);

  const clean = await diff("claude", { env });
  assert.deepEqual(clean.creates, []);
  assert.deepEqual(clean.removes, []);

  const rolledBack = await rollback(applied.transaction_id as string, env);
  assert.equal(rolledBack.ok, true);
  await assert.rejects(lstat(rendered));
});

test("materialize removes only manager-owned rendered entries and rollback restores links", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-materialize-remove-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);
  await setSkill("global", skillId, true, { env, surface: "test" });
  await materialize("claude", { env, surface: "test" });

  await setSkill("global", skillId, false, { env, surface: "test" });
  const removal = await materialize("claude", { env, surface: "test" });
  assert.equal(removal.ok, true);
  const rendered = join(home, ".claude", "skills", "eli5");
  await assert.rejects(lstat(rendered));

  const restored = await rollback(removal.transaction_id as string, env);
  assert.equal(restored.ok, true);
  assert.equal((await lstat(rendered)).isSymbolicLink(), true);
});

test("materialize refuses unmanaged rendered conflicts", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-materialize-conflict-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);
  await setSkill("global", skillId, true, { env, surface: "test" });
  await mkdir(join(home, ".claude", "skills", "eli5"), { recursive: true });

  const result = await materialize("claude", { env, surface: "test" });
  assert.equal(result.ok, false);
  assert.match(String(result.error), /conflicts/);
});
