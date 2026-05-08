import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

async function makeSkill(path: string): Promise<void> {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "SKILL.md"), "# Skill\n");
}

async function makeSkillWithBody(path: string, body: string): Promise<void> {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "SKILL.md"), body);
}

test("skills-manager scan emits parseable JSON for a temp home", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-scan-"));
  await makeSkill(join(home, ".agents", "skills", "eli5"));

  const result = spawnSync("node", ["dist/cli.js", "scan", "--json"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  const parsed = JSON.parse(result.stdout);
  assert.equal(parsed.locations.inbox.entries[0].name, "eli5");
  assert.equal(parsed.locations.inbox.entries[0].type, "skill_dir");
});

test("skills-manager backup dry-run is implemented and parseable", () => {
  const result = spawnSync("node", ["dist/cli.js", "backup", "--dry-run"], {
    cwd: process.cwd(),
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(JSON.parse(result.stdout).target, /agent-skills-backup$/);
});

test("skills-manager pre-migration-backup dry-run is implemented and parseable", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-premigration-"));
  const result = spawnSync("node", ["dist/cli.js", "pre-migration-backup", "--dry-run"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  const parsed = JSON.parse(result.stdout);
  assert.match(parsed.target, /agent-skills-pre-migration-backup$/);
  assert.deepEqual(parsed.raw_copies.map((copy: { name: string }) => copy.name), ["claude", "codex", "agents"]);
});

test("checked-in skills-manager-ts wrapper runs the built CLI", () => {
  const result = spawnSync("bin/skills-manager-ts", ["--help"], {
    cwd: process.cwd(),
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Commands:/);
});

test("checked-in skills-manager wrapper delegates to TypeScript CLI", () => {
  const result = spawnSync("bin/skills-manager", ["--help"], {
    cwd: process.cwd(),
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Usage: skills-manager/);
});

test("skills-manager state and enable work against a temp home", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-state-"));
  await makeSkill(join(home, ".agents", "skills-store", "skills", "skill.test-owner.eli5"));
  await writeFile(
    join(home, ".agents", "skills-store", "skills", "skill.test-owner.eli5", "skill.json"),
    JSON.stringify(
      {
        id: "skill.test-owner.eli5",
        aliases: { claude: "eli5", codex: "eli5" },
        compatibility: { claude: true, codex: true }
      },
      null,
      2
    )
  );

  const enable = spawnSync("node", ["dist/cli.js", "enable", "eli5", "--scope", "global"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(enable.status, 0, enable.stderr);
  assert.equal(JSON.parse(enable.stdout).skill_id, "skill.test-owner.eli5");

  const state = spawnSync("node", ["dist/cli.js", "state", "--client", "claude", "--json"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(state.status, 0, state.stderr);
  const parsed = JSON.parse(state.stdout);
  assert.equal(parsed.desired["skill.test-owner.eli5"].alias, "eli5");
});

test("skills-manager import/adopt/migrate operate on temp homes", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-plan-"));
  await makeSkill(join(home, ".agents", "skills", "eli5"));
  await makeSkill(join(home, "external", "adopt-me"));
  await makeSkillWithBody(join(home, ".claude", "skills", "same"), "identical\n");
  await makeSkillWithBody(join(home, ".codex", "skills", "same"), "identical\n");

  const importPreview = spawnSync("node", ["dist/cli.js", "import", "--dry-run"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(importPreview.status, 0, importPreview.stderr);
  assert.equal(JSON.parse(importPreview.stdout).candidates.length, 1);

  const adopt = spawnSync("node", ["dist/cli.js", "adopt", join(home, "external", "adopt-me")], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(adopt.status, 0, adopt.stderr);
  assert.equal(JSON.parse(adopt.stdout).skill_id, "skill.test-owner.adopt-me");

  const migrate = spawnSync("node", ["dist/cli.js", "migrate", "--dry-run"], {
    cwd: process.cwd(),
    env: { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" },
    encoding: "utf8"
  });
  assert.equal(migrate.status, 0, migrate.stderr);
  assert.equal(JSON.parse(migrate.stdout).actions[0].kind, "merge");
});

test("skills-manager preset commands mutate temp homes through JSON CLI", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-preset-"));
  const skillId = "skill.test-owner.eli5";
  await makeSkill(join(home, ".agents", "skills-store", "skills", skillId));
  await writeFile(
    join(home, ".agents", "skills-store", "skills", skillId, "skill.json"),
    JSON.stringify(
      {
        id: skillId,
        aliases: { claude: "eli5", codex: "eli5" },
        compatibility: { claude: true, codex: true }
      },
      null,
      2
    )
  );

  const env = { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };
  const create = spawnSync("node", ["dist/cli.js", "preset", "create", "Starter", "--description", "demo", "--tag", "base"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(create.status, 0, create.stderr);
  assert.equal(JSON.parse(create.stdout).written, true);

  const add = spawnSync("node", ["dist/cli.js", "preset", "add", "starter", "eli5"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(add.status, 0, add.stderr);
  assert.equal(JSON.parse(add.stdout).added[0].id, skillId);

  const apply = spawnSync("node", ["dist/cli.js", "preset", "apply", "starter", "--scope", "global", "--dry-run"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(apply.status, 0, apply.stderr);
  const preview = JSON.parse(apply.stdout);
  assert.equal(preview.would_write, true);
  assert.deepEqual(preview.after.enable, [skillId]);

  const capture = spawnSync("node", ["dist/cli.js", "preset", "create", "Captured", "--from-scope", "global", "--dry-run"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(capture.status, 0, capture.stderr);
  assert.equal(JSON.parse(capture.stdout).source_scope, "global");

  const listed = spawnSync("node", ["dist/cli.js", "preset", "list"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(listed.status, 0, listed.stderr);
  assert.deepEqual(JSON.parse(listed.stdout), ["starter"]);
});

test("skills-manager diff/materialize/rollback run through temp rendered dirs", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-cli-render-"));
  const skillId = "skill.test-owner.eli5";
  await makeSkill(join(home, ".agents", "skills-store", "skills", skillId));
  await writeFile(
    join(home, ".agents", "skills-store", "skills", skillId, "skill.json"),
    JSON.stringify({ id: skillId, aliases: { claude: "eli5", codex: "eli5" }, compatibility: { claude: true, codex: true } }, null, 2)
  );
  const env = { ...process.env, SKILLS_MANAGER_HOME: home, OWNER_PREFIX: "skill.test-owner" };

  const enable = spawnSync("node", ["dist/cli.js", "enable", "eli5", "--scope", "global", "--client", "claude"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(enable.status, 0, enable.stderr);

  const diff = spawnSync("node", ["dist/cli.js", "diff", "--client", "claude"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(diff.status, 0, diff.stderr);
  assert.equal(JSON.parse(diff.stdout).creates[0].skill_id, skillId);

  const materialize = spawnSync("node", ["dist/cli.js", "materialize", "--client", "claude"], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(materialize.status, 0, materialize.stderr);
  const tx = JSON.parse(materialize.stdout).claude.transaction_id;
  assert.equal(typeof tx, "string");

  const rollback = spawnSync("node", ["dist/cli.js", "rollback", tx], {
    cwd: process.cwd(),
    env,
    encoding: "utf8"
  });
  assert.equal(rollback.status, 0, rollback.stderr);
  assert.equal(JSON.parse(rollback.stdout).ok, true);
});
