import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { actionsPath } from "./action-log.js";
import { readJson, stableId, writeSkillMeta } from "./store.js";
import { loadManifest } from "./store.js";
import { resolveDesired, resolveSkillRef, setSkill } from "./resolver.js";

test("resolver applies global/project/session precedence and client masks", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-resolve-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = stableId("eli5", undefined, env);
  await writeSkillMeta(
    skillId,
    {
      id: skillId,
      aliases: { claude: "eli5", codex: "eli5" },
      compatibility: { claude: true, codex: true }
    },
    env
  );

  await setSkill("global", skillId, true, { env, surface: "test" });
  await setSkill("global", skillId, false, { env, client: "codex", surface: "test" });
  await setSkill("session", skillId, false, { env, surface: "test" });

  const claude = await resolveDesired("claude", { env });
  const codex = await resolveDesired("codex", { env });

  assert.equal(skillId in claude.desired, false);
  assert.equal(skillId in codex.desired, false);
  assert.deepEqual(claude.skills[skillId]?.reasons, ["global:enable", "session:disable"]);
  assert.deepEqual(codex.skills[skillId]?.reasons, ["global:enable", "global:codex:disable", "session:disable"]);
});

test("resolver hides incompatible desired skills and reports unknown IDs", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-incompat-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = stableId("claude-only", undefined, env);
  await writeSkillMeta(
    skillId,
    {
      id: skillId,
      aliases: { claude: "claude-only", codex: "claude-only" },
      compatibility: { claude: true, codex: false }
    },
    env
  );

  await setSkill("global", skillId, true, { env, surface: "test" });
  await setSkill("global", "skill.test-owner.unknown", true, { env, surface: "test" });

  const codex = await resolveDesired("codex", { env });

  assert.equal(skillId in codex.desired, false);
  assert.deepEqual(codex.skills[skillId]?.reasons, ["global:enable", "codex:incompatible"]);
  assert.deepEqual(codex.unknown_enabled_ids, ["skill.test-owner.unknown"]);
});

test("setSkill mutates manifests and appends action logs", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-set-skill-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = stableId("eli5", undefined, env);

  const path = await setSkill("global", skillId, true, { env, client: "claude", surface: "test" });
  const manifest = await loadManifest("global", { env });
  const log = await readJson<Record<string, unknown>>(actionsPath(env), {});

  assert.match(path, /global\.json$/);
  assert.deepEqual(manifest.clients.claude.enable, [skillId]);
  assert.equal(log.action, "enable");
  assert.equal(log.surface, "test");
  assert.equal(log.client, "claude");
});

test("resolveSkillRef resolves unique aliases and leaves ambiguous refs unchanged", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-ref-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  await writeSkillMeta(
    "skill.test-owner.eli5",
    {
      id: "skill.test-owner.eli5",
      aliases: { claude: "eli5", codex: "eli5" },
      compatibility: { claude: true, codex: true }
    },
    env
  );
  await writeSkillMeta(
    "skill.test-owner.other",
    {
      id: "skill.test-owner.other",
      aliases: { claude: "same", codex: "same" },
      compatibility: { claude: true, codex: true }
    },
    env
  );
  await writeSkillMeta(
    "skill.test-owner.same",
    {
      id: "skill.test-owner.same",
      aliases: { claude: "same", codex: "same" },
      compatibility: { claude: true, codex: true }
    },
    env
  );

  assert.equal(await resolveSkillRef("eli5", env), "skill.test-owner.eli5");
  assert.equal(await resolveSkillRef("same", env), "same");
  assert.equal(await resolveSkillRef("skill.test-owner.eli5", env), "skill.test-owner.eli5");
});
