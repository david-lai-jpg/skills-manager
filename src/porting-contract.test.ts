import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

const root = process.cwd();
const matrixPath = join(root, "docs", "porting", "compatibility-matrix.md");
const manifestPath = join(root, "tests", "fixtures", "parity", "manifest.json");
const packagePath = join(root, "package.json");
const wrapperPath = join(root, "bin", "skills-manager");

const requiredCommands = [
  "scan",
  "import",
  "adopt",
  "migrate",
  "state",
  "enable",
  "disable",
  "materialize",
  "diff",
  "doctor",
  "rollback",
  "backup",
  "pre-migration-backup",
  "restore",
  "preset list",
  "preset show",
  "preset create",
  "preset add",
  "preset remove",
  "preset rename",
  "preset delete",
  "preset apply",
  "bare tui"
];

const requiredScenarios = [
  "empty-home",
  "inbox-import",
  "adopt-enable-materialize",
  "migrate-merge-fork",
  "project-session-manifests",
  "unmanaged-conflict",
  "broken-symlink",
  "missing-skill-md",
  "symlink-fallback-copy",
  "rollback-after-failure",
  "backup-restore-rendered-metadata",
  "preset-crud-apply",
  "preset-alias-drift",
  "preset-unknown-id",
  "malformed-doctorable-preset",
  "action-log-dry-run"
];

type Manifest = {
  commands: string[];
  scenarios: Array<{ id: string; covers: string[] }>;
};

const skillBody = "# Skill\n\nFixture skill for port parity.\n";

async function writeText(path: string, text: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, text, "utf8");
}

async function writeJson(path: string, data: unknown): Promise<void> {
  await writeText(path, `${JSON.stringify(data, null, 2)}\n`);
}

async function skill(path: string, body = skillBody): Promise<void> {
  await writeText(join(path, "SKILL.md"), body);
}

async function managedSkill(home: string, skillId: string, alias: string, body = skillBody): Promise<void> {
  const skillRoot = join(home, ".agents", "skills-store", "skills", skillId);
  await skill(skillRoot, body);
  await writeJson(join(skillRoot, "skill.json"), {
    id: skillId,
    aliases: { claude: alias, codex: alias },
    compatibility: { claude: true, codex: true },
    sources: [],
    version: 1
  });
}

async function createFixture(scenarioId: string, destination: string): Promise<string> {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as Manifest;
  const knownScenarios = new Set(manifest.scenarios.map((scenario) => scenario.id));
  if (!knownScenarios.has(scenarioId)) {
    throw new Error(`unknown parity fixture scenario: ${scenarioId}`);
  }
  await mkdir(destination, { recursive: true });

  switch (scenarioId) {
    case "empty-home":
      return destination;
    case "inbox-import":
      await skill(join(destination, ".agents", "skills", "eli5"));
      await skill(join(destination, ".agents", "skills", "prompt-engineer"));
      return destination;
    case "adopt-enable-materialize":
      await skill(join(destination, "external", "eli5"));
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await writeJson(join(destination, ".agents", "skills-store", "manifests", "global.json"), {
        version: 1,
        inherit: true,
        enable: ["skill.test-owner.eli5"],
        disable: [],
        clients: { claude: { enable: [], disable: [] }, codex: { enable: [], disable: [] } }
      });
      return destination;
    case "migrate-merge-fork":
      await skill(join(destination, ".claude", "skills", "same"), "identical\n");
      await skill(join(destination, ".codex", "skills", "same"), "identical\n");
      await skill(join(destination, ".claude", "skills", "fork-me"), "claude\n");
      await skill(join(destination, ".codex", "skills", "fork-me"), "codex\n");
      return destination;
    case "project-session-manifests":
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await writeJson(join(destination, ".agents", "skills-store", "manifests", "global.json"), {
        version: 1,
        inherit: true,
        enable: ["skill.test-owner.eli5"],
        disable: [],
        clients: { claude: { enable: [], disable: [] }, codex: { enable: [], disable: ["skill.test-owner.eli5"] } }
      });
      await writeJson(join(destination, ".agents", "skills-store", "manifests", "sessions", "default.json"), {
        version: 1,
        inherit: true,
        enable: [],
        disable: ["skill.test-owner.eli5"],
        clients: { claude: { enable: [], disable: [] }, codex: { enable: [], disable: [] } }
      });
      return destination;
    case "unmanaged-conflict":
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await skill(join(destination, ".claude", "skills", "eli5"), "unmanaged\n");
      return destination;
    case "broken-symlink": {
      const target = join(destination, ".agents", "skills", "missing-target");
      const link = join(destination, ".agents", "skills", "broken");
      await mkdir(dirname(link), { recursive: true });
      try {
        await import("node:fs/promises").then((fs) => fs.symlink(target, link, "dir"));
      } catch {
        await writeJson(`${link}.symlink-unavailable.json`, { target });
      }
      return destination;
    }
    case "missing-skill-md":
      await mkdir(join(destination, ".agents", "skills", "missing"), { recursive: true });
      return destination;
    case "symlink-fallback-copy": {
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      const rendered = join(destination, ".claude", "skills", "eli5");
      await skill(rendered);
      await writeJson(join(rendered, ".skills-manager.json"), { manager: "skills-manager", skill_id: "skill.test-owner.eli5" });
      return destination;
    }
    case "rollback-after-failure":
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await writeJson(join(destination, ".agents", "skills-store", "transactions", "tx-fixture.json"), {
        id: "tx-fixture",
        status: "pending",
        actions: [
          {
            op: "create_symlink",
            client: "claude",
            alias: "eli5",
            skill_id: "skill.test-owner.eli5",
            target: join(destination, ".claude", "skills", "eli5")
          }
        ]
      });
      return destination;
    case "backup-restore-rendered-metadata":
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await skill(join(destination, ".claude", "skills", "eli5"));
      return destination;
    case "preset-crud-apply":
      await managedSkill(destination, "skill.test-owner.eli5", "eli5");
      await writeJson(join(destination, ".agents", "skills-store", "presets", "starter.json"), {
        version: 1,
        name: "starter",
        description: "Fixture preset",
        tags: ["fixture"],
        enable: [{ id: "skill.test-owner.eli5", alias: "eli5" }],
        disable: [],
        clients: {
          claude: { enable: [{ id: "skill.test-owner.eli5", alias: "eli5" }], disable: [] },
          codex: { enable: [], disable: [{ id: "skill.test-owner.eli5", alias: "eli5" }] }
        }
      });
      return destination;
    case "preset-alias-drift":
      await managedSkill(destination, "skill.test-owner.eli5", "renamed-eli5");
      await writeJson(join(destination, ".agents", "skills-store", "presets", "drift.json"), {
        version: 1,
        name: "drift",
        enable: [{ id: "skill.test-owner.eli5", alias: "old-eli5" }],
        disable: []
      });
      return destination;
    case "preset-unknown-id":
      await writeJson(join(destination, ".agents", "skills-store", "presets", "unknown.json"), {
        version: 1,
        name: "unknown",
        enable: [{ id: "skill.test-owner.nope", alias: "nope" }],
        disable: []
      });
      return destination;
    case "malformed-doctorable-preset":
      await writeText(join(destination, ".agents", "skills-store", "presets", "broken.json"), "{not json\n");
      return destination;
    case "action-log-dry-run":
      await skill(join(destination, ".agents", "skills", "eli5"));
      await mkdir(join(destination, ".agents", "skills-store", "logs"), { recursive: true });
      return destination;
  }
  throw new Error(`scenario declared but not implemented: ${scenarioId}`);
}

test("compatibility matrix covers full CLI surface", async () => {
  const text = await readFile(matrixPath, "utf8");
  for (const command of requiredCommands) {
    assert.match(text, new RegExp(command.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(text, /Dry-runs and previews must not append to the action log/);
  assert.match(text, /TypeScript runtime is now the checked-out wrapper/);
});

test("parity fixture manifest covers required scenarios and commands", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as Manifest;
  const commands = new Set(manifest.commands);
  const scenarios = new Map(manifest.scenarios.map((scenario) => [scenario.id, new Set(scenario.covers)]));
  for (const command of requiredCommands) {
    assert.equal(commands.has(command), true, command);
  }
  for (const scenario of requiredScenarios) {
    assert.equal(scenarios.has(scenario), true, scenario);
  }
  const covered = new Set(manifest.scenarios.flatMap((scenario) => scenario.covers));
  for (const command of requiredCommands) {
    assert.equal(covered.has(command), true, command);
  }
});

test("TypeScript fixture builder writes only under destination and preserves client preset buckets", async () => {
  const base = mkdtempSync(join(tmpdir(), "sm-porting-fixtures-"));
  for (const scenario of requiredScenarios) {
    const destination = join(base, scenario);
    const home = await createFixture(scenario, destination);
    assert.equal(home, destination);
    assert.equal(resolve(home).startsWith(resolve(base)), true);
  }

  const presetHome = join(base, "preset-check");
  await createFixture("preset-crud-apply", presetHome);
  const preset = JSON.parse(await readFile(join(presetHome, ".agents", "skills-store", "presets", "starter.json"), "utf8"));
  assert.equal(preset.clients.claude.enable[0].id, "skill.test-owner.eli5");
  assert.equal(preset.clients.codex.disable[0].id, "skill.test-owner.eli5");
});

test("TypeScript package and checked-out wrapper are the runtime baseline", async () => {
  const packageJson = JSON.parse(await readFile(packagePath, "utf8"));
  assert.equal(packageJson.type, "module");
  assert.match(packageJson.packageManager, /^pnpm@/);
  assert.equal(packageJson.engines.node, ">=22.0.0");
  assert.equal(packageJson.bin["skills-manager"], "./bin/skills-manager");
  assert.equal(packageJson.bin["skills-manager-ts"], "./bin/skills-manager-ts");
  for (const dependency of ["commander", "dotenv", "zod", "ink", "react", "picocolors"]) {
    assert.equal(dependency in packageJson.dependencies, true, dependency);
  }
  assert.equal("@inquirer/prompts" in packageJson.dependencies, false);

  const wrapper = await readFile(wrapperPath, "utf8");
  assert.match(wrapper, /exec "\$ROOT\/bin\/skills-manager-ts" "\$@"/);
  assert.doesNotMatch(wrapper, /python|sys\.path/);

  const run = spawnSync("bin/skills-manager", ["doctor"], {
    cwd: root,
    env: { ...process.env, SKILLS_MANAGER_HOME: mkdtempSync(join(tmpdir(), "sm-wrapper-contract-")), OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(run.status, 0, run.stderr);
  assert.equal(JSON.parse(run.stdout).ok, true);
});
