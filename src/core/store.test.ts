import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  allSkills,
  contentHash,
  copySkillTree,
  ensureStore,
  loadManifest,
  manifestPath,
  manifestTemplate,
  markerData,
  ownerPrefix,
  pathUnder,
  readJson,
  saveManifest,
  shouldSkipLocalJunkName,
  stableId,
  stableJson,
  writeSkillMeta,
  writeJson
} from "./store.js";
import { inboxDir, skillsRoot, storeRoot } from "./paths.js";

test("path helpers honor SKILLS_MANAGER_HOME and SKILLS_MANAGER_STORE", async () => {
  const env = { SKILLS_MANAGER_HOME: "/tmp/sm-home", SKILLS_MANAGER_STORE: "/tmp/custom-store" };

  assert.equal(inboxDir(env), "/tmp/sm-home/.agents/skills");
  assert.equal(storeRoot(env), "/tmp/custom-store");

  await ensureStore(env);
});

test("junk predicate skips all dot names except explicit managed roots when allowed", () => {
  assert.equal(shouldSkipLocalJunkName(".system"), true);
  assert.equal(shouldSkipLocalJunkName(".husky"), true);
  assert.equal(shouldSkipLocalJunkName(".agents"), true);
  assert.equal(shouldSkipLocalJunkName(".agents", { allowManagedDotRoot: true }), false);
  assert.equal(shouldSkipLocalJunkName(".claude", { allowManagedDotRoot: true }), false);
  assert.equal(shouldSkipLocalJunkName(".codex", { allowManagedDotRoot: true }), false);
});

test("stable ids and manifest templates stay stable", () => {
  const env = { OWNER_PREFIX: "skill.test-owner" };
  assert.equal(ownerPrefix(env), "skill.test-owner");
  assert.equal(stableId("Prompt Engineer", undefined, env), "skill.test-owner.prompt-engineer");
  assert.equal(stableId("same", "Claude", env), "skill.test-owner.same.claude");
  assert.deepEqual(manifestTemplate(), {
    version: 1,
    inherit: true,
    enable: [],
    disable: [],
    clients: {
      claude: { enable: [], disable: [] },
      codex: { enable: [], disable: [] }
    }
  });
});

test("owner prefix loads from .env through dotenv", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "sm-dotenv-prefix-"));
  await writeFile(join(cwd, ".env"), "OWNER_PREFIX=skill.dotenv-test\n");

  const moduleUrl = pathToFileURL(join(process.cwd(), "dist", "core", "store.js")).href;
  const run = spawnSync("node", ["--input-type=module", "-e", `import { stableId } from ${JSON.stringify(moduleUrl)}; console.log(stableId("From Env"));`], {
    cwd,
    encoding: "utf8",
    env: { PATH: process.env.PATH }
  });

  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.stdout.trim(), "skill.dotenv-test.from-env");
});

test("writeJson writes sorted, newline-terminated JSON and readJson returns defaults", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-store-json-"));
  const path = join(home, "nested", "data.json");

  assert.deepEqual(await readJson(path, { missing: true }), { missing: true });
  await writeJson(path, { z: 1, a: { y: 2, b: 3 } });

  assert.equal(await readFile(path, "utf8"), '{\n  "a": {\n    "b": 3,\n    "y": 2\n  },\n  "z": 1\n}\n');
  assert.equal(stableJson({ b: 1, a: 2 }), '{\n  "a": 2,\n  "b": 1\n}\n');
});

test("contentHash skips local junk and stays stable", async () => {
  const root = await mkdtemp(join(tmpdir(), "sm-hash-"));
  await writeFile(join(root, "SKILL.md"), "# Skill\n");
  await writeFile(join(root, ".DS_Store"), "junk");
  await writeFile(join(root, ".gitignore"), "junk");
  await writeFile(join(root, ".local-note"), "junk");
  await writeFile(join(root, "skill.json"), "{}");
  await mkdir(join(root, ".husky"));
  await writeFile(join(root, ".husky", "pre-commit"), "junk");
  await mkdir(join(root, "__pycache__"));
  await writeFile(join(root, "__pycache__", "x.pyc"), "junk");
  await symlink(join(root, "SKILL.md"), join(root, "link.md"));

  const actual = await contentHash(root);
  assert.equal(actual, "e2b494fb6dd0c226e12416767e8526c80f59cccbd0c685de52645043a00be1be");
});

test("copySkillTree preserves skill files while ignoring local junk", async () => {
  const src = await mkdtemp(join(tmpdir(), "sm-copy-src-"));
  const dst = await mkdtemp(join(tmpdir(), "sm-copy-dst-parent-"));
  await writeFile(join(src, "SKILL.md"), "# Skill\n");
  await writeFile(join(src, ".DS_Store"), "junk");
  await writeFile(join(src, ".gitignore"), "junk");
  await writeFile(join(src, ".local-note"), "junk");
  await mkdir(join(src, ".husky"));
  await writeFile(join(src, ".husky", "pre-commit"), "junk");
  await mkdir(join(src, ".git"));
  await writeFile(join(src, ".git", "HEAD"), "junk");

  const target = join(dst, "skill");
  await copySkillTree(src, target);

  assert.equal(await readFile(join(target, "SKILL.md"), "utf8"), "# Skill\n");
  await assert.rejects(readFile(join(target, ".DS_Store"), "utf8"));
  await assert.rejects(readFile(join(target, ".gitignore"), "utf8"));
  await assert.rejects(readFile(join(target, ".local-note"), "utf8"));
  await assert.rejects(readFile(join(target, ".husky", "pre-commit"), "utf8"));
  await assert.rejects(readFile(join(target, ".git", "HEAD"), "utf8"));
});

test("manifestPath keeps stable managed-store locations", async () => {
  const env = { SKILLS_MANAGER_HOME: "/tmp/sm-home" };

  assert.equal(await manifestPath("global", { env }), "/tmp/sm-home/.agents/skills-store/manifests/global.json");
  assert.equal(await manifestPath("session", { env, session: "My Session" }), "/tmp/sm-home/.agents/skills-store/manifests/sessions/my-session.json");
});

test("loadManifest merges partial legacy manifests with template defaults", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-manifest-"));
  const env = { SKILLS_MANAGER_HOME: home };
  const globalPath = await manifestPath("global", { env });
  await writeJson(globalPath, {
    version: 1,
    inherit: true,
    enable: ["skill.test-owner.eli5"],
    disable: [],
    clients: {
      codex: { disable: ["skill.test-owner.eli5"] }
    }
  });

  assert.deepEqual(await loadManifest("global", { env }), {
    version: 1,
    inherit: true,
    enable: ["skill.test-owner.eli5"],
    disable: [],
    clients: {
      claude: { enable: [], disable: [] },
      codex: { enable: [], disable: ["skill.test-owner.eli5"] }
    }
  });

  const saved = manifestTemplate({ enable: ["skill.test-owner.saved"] });
  const savedPath = await saveManifest("session", saved, { env, session: "Saved Session" });
  assert.equal(savedPath, join(home, ".agents", "skills-store", "manifests", "sessions", "saved-session.json"));
  assert.deepEqual(await loadManifest("session", { env, session: "Saved Session" }), saved);
});

test("skill metadata helpers provide stable fallback behavior", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-meta-"));
  const env = { SKILLS_MANAGER_HOME: home };
  const skillRoot = join(skillsRoot(env), "skill.test-owner.eli5");
  await mkdir(skillRoot, { recursive: true });
  await writeFile(join(skillRoot, "SKILL.md"), "# Skill\n");

  assert.deepEqual(await allSkills(env), {
    "skill.test-owner.eli5": {
      id: "skill.test-owner.eli5",
      aliases: { claude: "skill.test-owner.eli5", codex: "skill.test-owner.eli5" },
      compatibility: { claude: true, codex: true },
      source_paths: [],
      content_hash: await contentHash(skillRoot)
    }
  });

  await writeSkillMeta(
    "skill.test-owner.eli5",
    {
      id: "skill.test-owner.eli5",
      aliases: { claude: "eli5", codex: "eli5" },
      compatibility: { claude: true, codex: true }
    },
    env
  );

  assert.equal((await allSkills(env))["skill.test-owner.eli5"]?.aliases.claude, "eli5");
  assert.deepEqual(markerData("skill.test-owner.eli5"), {
    manager: "skills-manager",
    version: 1,
    skill_id: "skill.test-owner.eli5"
  });
});

test("pathUnder compares resolved paths", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-under-"));
  await mkdir(join(home, "parent", "child"), { recursive: true });
  await mkdir(join(home, "other"), { recursive: true });

  assert.equal(await pathUnder(join(home, "parent", "child"), join(home, "parent")), true);
  assert.equal(await pathUnder(join(home, "parent", "missing"), join(home, "parent")), true);
  assert.equal(await pathUnder(join(home, "missing-parent", "child"), join(home, "missing-parent")), true);
  assert.equal(await pathUnder(join(home, "other"), join(home, "parent")), false);
});
