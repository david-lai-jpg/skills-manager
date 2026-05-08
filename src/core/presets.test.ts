import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { actionsPath } from "./action-log.js";
import {
  addEntries,
  applyPreset,
  capturePreset,
  createPreset,
  deletePreset,
  listPresets,
  presetPath,
  removeEntries,
  renamePreset,
  showPreset,
  validatePresets
} from "./presets.js";
import { loadManifest, readJson, saveManifest, stableId, writeJson, writeSkillMeta } from "./store.js";

async function addManagedSkill(home: string, name = "eli5"): Promise<string> {
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = stableId(name, undefined, env);
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

test("preset create/add/remove/list/show preserve client buckets and logs only on apply", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-preset-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);

  const dry = await createPreset("Starter", { dryRun: true, env, surface: "test" });
  assert.equal(dry.would_write, true);
  await assert.rejects(readFile(actionsPath(env), "utf8"));

  const created = await createPreset("Starter", { env, surface: "test" });
  assert.equal(created.written, true);
  assert.deepEqual(await listPresets(env), ["starter"]);

  const added = await addEntries("starter", ["eli5"], { env, surface: "test" });
  assert.equal(added.written, true);
  assert.equal((await showPreset("starter", env)).issues instanceof Array, true);

  const removed = await removeEntries("starter", [skillId], { env, dryRun: true, surface: "test" });
  assert.equal(removed.would_write, true);
  const preset = await readJson<Record<string, unknown>>(presetPath("starter", env), {});
  assert.deepEqual((preset.enable as Array<Record<string, string>>).map((entry) => entry.id), [skillId]);
});

test("preset apply stamps manifests and unknown IDs fail atomically", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-preset-apply-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);
  await createPreset("Starter", { env, surface: "test" });
  await addEntries("starter", [skillId], { env, surface: "test" });

  const preview = await applyPreset("starter", "global", { env, dryRun: true });
  assert.equal(preview.would_write, true);
  assert.deepEqual((await loadManifest("global", { env })).enable, []);

  const applied = await applyPreset("starter", "global", { env, surface: "test" });
  assert.equal(applied.written, true);
  assert.deepEqual((await loadManifest("global", { env })).enable, [skillId]);

  await createPreset("Unknown", { env, surface: "test" });
  await writeSkillMeta("skill.test-owner.other", { id: "skill.test-owner.other", aliases: { claude: "other", codex: "other" }, compatibility: { claude: true, codex: true } }, env);
  await addEntries("unknown", ["skill.test-owner.other"], { env, surface: "test" });
  // Delete metadata after preset creation to simulate stale/unknown preset ID.
  await import("node:fs/promises").then((fs) => fs.rm(join(home, ".agents", "skills-store", "skills", "skill.test-owner.other"), { recursive: true, force: true }));
  const failed = await applyPreset("unknown", "global", { env });
  assert.equal(failed.ok, false);
});

test("preset capture snapshots manifest buckets without rendering", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-preset-capture-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);
  await saveManifest(
    "global",
    {
      version: 1,
      inherit: true,
      enable: [skillId],
      disable: [],
      clients: {
        claude: { enable: [], disable: [skillId] },
        codex: { enable: [skillId], disable: [] }
      }
    },
    { env }
  );

  const preview = await capturePreset("Captured", "global", { env, dryRun: true, surface: "test" });
  assert.equal(preview.would_write, true);
  assert.deepEqual(await listPresets(env), []);

  const captured = await capturePreset("Captured", "global", { env, surface: "test" });
  assert.equal(captured.written, true);
  const shown = await showPreset("captured", env);
  const clients = shown.clients as { claude: { disable: Array<Record<string, unknown>> } };
  assert.deepEqual((shown.enable as Array<Record<string, unknown>>).map((entry) => entry.id), [skillId]);
  assert.deepEqual(clients.claude.disable.map((entry) => entry.id), [skillId]);
});

test("preset validation reports malformed buckets, duplicates, unknown IDs, and alias drift", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-preset-validate-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const skillId = await addManagedSkill(home);

  await writeJson(presetPath("malformed", env), { name: "malformed", enable: "not-a-list" });
  await writeJson(presetPath("stale", env), {
    name: "stale",
    enable: [
      { id: skillId, alias: "old-alias" },
      { id: skillId, alias: "old-alias" },
      { id: "skill.test-owner.missing", alias: "missing" }
    ],
    disable: [],
    clients: {
      claude: { enable: [], disable: [] },
      codex: { enable: [], disable: [] }
    }
  });

  const types = [...new Set((await validatePresets(env)).map((issue) => issue.type))].sort();
  assert.deepEqual(types, ["preset_alias_drift", "preset_duplicate_entry", "preset_malformed", "preset_unknown_id"]);
});

test("preset rename and delete preview by default and apply only when requested", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-preset-rename-"));
  const env = { SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  await createPreset("Starter", { env, surface: "test" });

  assert.equal((await renamePreset("starter", "renamed", { env })).would_rename, true);
  assert.deepEqual(await listPresets(env), ["starter"]);
  assert.equal((await renamePreset("starter", "renamed", { env, apply: true, surface: "test" })).renamed, true);
  assert.deepEqual(await listPresets(env), ["renamed"]);

  assert.equal((await deletePreset("renamed", { env })).would_delete, true);
  assert.deepEqual(await listPresets(env), ["renamed"]);
  assert.equal((await deletePreset("renamed", { env, apply: true, surface: "test" })).deleted, true);
  assert.deepEqual(await listPresets(env), []);
});
