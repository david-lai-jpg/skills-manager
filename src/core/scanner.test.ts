import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { adapter, expandClients } from "./adapters.js";
import { classifyEntry, scan, scanDir } from "./scanner.js";

async function makeSkill(path: string, body = "# Skill\n\nDo the thing.\n"): Promise<void> {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "SKILL.md"), body);
}

test("adapter paths and client expansion stay stable", () => {
  const env = { SKILLS_MANAGER_HOME: "/tmp/sm-home", CODEX_HOME: "/tmp/codex-home" };

  assert.deepEqual(expandClients("all"), ["claude", "codex"]);
  assert.deepEqual(expandClients("claude"), ["claude"]);
  assert.equal(adapter("claude", env).globalDir(), "/tmp/sm-home/.claude/skills");
  assert.equal(adapter("codex", env).globalDir(), "/tmp/codex-home/skills");
  assert.equal(adapter("codex", { SKILLS_MANAGER_HOME: "/tmp/sm-home" }).globalDir(), "/tmp/sm-home/.codex/skills");
});

test("scanDir classifies skill dirs, missing SKILL.md, symlinks, broken symlinks, and files", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-scan-"));
  await makeSkill(join(home, "normal"));
  await mkdir(join(home, "missing"));
  await writeFile(join(home, "file.txt"), "plain");
  await symlink(join(home, "normal"), join(home, "link"), "dir");
  await symlink(join(home, "nope"), join(home, "broken"), "dir");

  const result = await scanDir(home);
  const types = Object.fromEntries(result.entries.map((entry) => [entry.name, entry.type]));

  assert.equal(types.normal, "skill_dir");
  assert.equal(types.missing, "missing_skill_md");
  assert.equal(types["file.txt"], "file");
  assert.equal(types.link, "symlink_skill");
  assert.equal(types.broken, "broken_symlink");

  const broken = result.entries.find((entry) => entry.name === "broken");
  assert.equal(broken?.resolved, undefined);
});

test("classifyEntry resolves valid symlinks through the filesystem", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-scan-realpath-"));
  await makeSkill(join(home, "final"));
  await symlink(join(home, "final"), join(home, "middle"), "dir");
  await symlink(join(home, "middle"), join(home, "link"), "dir");

  const entry = await classifyEntry(join(home, "link"));

  assert.equal(entry.type, "symlink_skill");
  assert.equal(entry.resolved, await import("node:fs/promises").then((fs) => fs.realpath(join(home, "final"))));
});

test("scan reports duplicate names and content hashes across locations", async () => {
  const home = await mkdtemp(join(tmpdir(), "sm-scan-all-"));
  const env = { SKILLS_MANAGER_HOME: home };
  await makeSkill(join(home, ".agents", "skills", "same"), "identical\n");
  await makeSkill(join(home, ".claude", "skills", "same"), "identical\n");
  await makeSkill(join(home, ".codex", "skills", "same"), "identical\n");

  const result = await scan({ env });

  assert.deepEqual(Object.keys(result.duplicates.names), ["same"]);
  assert.equal(Object.values(result.duplicates.content_hashes)[0]?.length, 3);
});

test("classifyEntry records errors instead of throwing", async () => {
  const result = await classifyEntry(join(tmpdir(), "does-not-exist-for-sm"));

  assert.equal(result.type, "error");
  assert.match(result.error ?? "", /no such file/i);
});
