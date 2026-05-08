import test from "node:test";
import assert from "node:assert/strict";
import { createElement, type ComponentType } from "react";
import { render } from "ink-testing-library";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  choiceWindow,
  executeTuiAction,
  filterChoiceOptions,
  filterTuiActions,
  outputLines,
  SkillsManagerApp,
  TUI_ACTIONS,
  formatTuiOutput,
  promptsForAction,
  summarizeTuiResult,
  tuiActionCoverage,
  visibleOutput,
  visibleOutputLines
} from "./tui.js";

async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 30));
}

async function waitForFrame(instance: { lastFrame: () => string | undefined }, pattern: RegExp): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const frame = instance.lastFrame() ?? "";
    if (pattern.test(frame)) {
      return frame;
    }
    await settle();
  }
  return instance.lastFrame() ?? "";
}

function stripAnsi(value: string): string {
  return value.replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "");
}

async function withTempHome<T>(prefix: string, run: (home: string) => Promise<T>): Promise<T> {
  const oldHome = process.env.SKILLS_MANAGER_HOME;
  process.env.SKILLS_MANAGER_HOME = await mkdtemp(join(tmpdir(), prefix));
  try {
    return await run(process.env.SKILLS_MANAGER_HOME);
  } finally {
    if (oldHome === undefined) {
      delete process.env.SKILLS_MANAGER_HOME;
    } else {
      process.env.SKILLS_MANAGER_HOME = oldHome;
    }
  }
}

async function writeManagedSkill(home: string, id: string, alias: string): Promise<void> {
  const dir = join(home, ".agents", "skills-store", "skills", id);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, "skill.json"), JSON.stringify({
    id,
    aliases: { claude: alias, codex: alias },
    compatibility: { claude: true, codex: true },
    sources: []
  }));
}

test("Ink TUI action catalog covers every CLI capability with executable actions", () => {
  assert.equal(new Set(TUI_ACTIONS.map((action) => action.value)).size, TUI_ACTIONS.length);
  assert.deepEqual(tuiActionCoverage(), [
    "adopt",
    "backup",
    "diff",
    "disable",
    "doctor",
    "enable",
    "import",
    "materialize",
    "migrate",
    "pre-migration-backup",
    "preset-add",
    "preset-apply",
    "preset-capture",
    "preset-create",
    "preset-delete",
    "preset-list",
    "preset-remove",
    "preset-rename",
    "preset-show",
    "restore",
    "rollback",
    "scan",
    "state"
  ]);
});

test("Ink TUI renders a real interactive menu", () => {
  const instance = render(createElement(SkillsManagerApp));
  assert.match(instance.lastFrame() ?? "", /Ink React TUI/);
  assert.match(instance.lastFrame() ?? "", /Scan skill locations/);
  instance.cleanup();
});

test("Ink TUI gives empty-store users a first-run path", async () => {
  const oldHome = process.env.SKILLS_MANAGER_HOME;
  process.env.SKILLS_MANAGER_HOME = await mkdtemp(join(tmpdir(), "sm-ink-first-run-"));
  const instance = render(createElement(SkillsManagerApp));
  try {
    const frame = stripAnsi(await waitForFrame(instance, /Empty managed store detected/));
    assert.match(frame, /scan → pre-migration backup if needed →\s+import\/migrate/);
  } finally {
    instance.cleanup();
    if (oldHome === undefined) {
      delete process.env.SKILLS_MANAGER_HOME;
    } else {
      process.env.SKILLS_MANAGER_HOME = oldHome;
    }
  }
});

test("TUI action prompts expose mutating confirmation and scrolling-friendly output flows", () => {
  assert.deepEqual(promptsForAction("materialize").map((prompt) => prompt.name), ["client", "project", "mode", "confirmMaterialize"]);
  assert.deepEqual(promptsForAction("pre-migration-backup").map((prompt) => prompt.name), ["exportPath", "mode", "confirmPreMigrationBackup"]);
  assert.deepEqual(promptsForAction("preset-delete").map((prompt) => prompt.name), ["names", "mode", "confirmDelete"]);
  assert.deepEqual(promptsForAction("restore").map((prompt) => prompt.name), ["from", "mode", "confirmRestore"]);
  assert.equal(promptsForAction("rollback").find((prompt) => prompt.name === "confirmRollback")?.type, "typed-confirm");
  assert.equal(promptsForAction("enable").find((prompt) => prompt.name === "skill")?.type, "search-select");
  assert.equal(promptsForAction("preset-add").find((prompt) => prompt.name === "skills")?.type, "multi-select");
  assert.deepEqual(visibleOutput("a\nb\nc\nd", 1, 2), ["b", "c"]);
});

test("TUI searchable choice helpers filter options and keep the active row visible", () => {
  const choices = [
    { value: "skill.example.alpha", label: "Alpha", description: "Claude helper" },
    { value: "skill.example.beta", label: "Beta", description: "Codex helper" },
    { value: "skill.example.gamma", label: "Gamma" }
  ];

  assert.deepEqual(filterChoiceOptions(choices, "codex").map((choice) => choice.value), ["skill.example.beta"]);
  assert.deepEqual(filterChoiceOptions(choices, "gamma").map((choice) => choice.value), ["skill.example.gamma"]);
  assert.deepEqual(choiceWindow(["a", "b", "c", "d", "e"], 3, 3), [
    { item: "c", index: 2 },
    { item: "d", index: 3 },
    { item: "e", index: 4 }
  ]);
});

test("TUI menu and output filter helpers narrow noisy lists without dropping scroll support", () => {
  assert.deepEqual(filterTuiActions(TUI_ACTIONS, "backup").map((action) => action.value), ["backup", "pre-migration-backup", "restore"]);
  assert.deepEqual(outputLines("Summary\nFull JSON\n  \"backup\": true", "json"), ["Full JSON"]);
  assert.deepEqual(visibleOutputLines(["a", "b", "c", "d"], 2, 2), ["c", "d"]);
});

test("TUI preset mutations accept multi-selected skill and preset values", async () => {
  await withTempHome("sm-ink-multi-", async (home) => {
    await writeManagedSkill(home, "skill.example.alpha", "alpha");
    await writeManagedSkill(home, "skill.example.beta", "beta");
    await executeTuiAction("preset-create", { name: "daily", dryRun: false });
    await executeTuiAction("preset-create", { name: "cleanup", dryRun: false });

    const added = await executeTuiAction("preset-add", {
      name: "daily",
      skills: ["skill.example.alpha", "skill.example.beta"],
      mode: "enable",
      dryRun: true
    });
    assert.equal((added as { ok?: boolean }).ok, true);
    assert.equal(((added as { added?: unknown[] }).added ?? []).length, 2);

    const deleted = await executeTuiAction("preset-delete", {
      names: ["daily", "cleanup"],
      mode: "preview"
    });
    assert.equal((deleted as { ok?: boolean }).ok, true);
    assert.equal((deleted as { count?: number }).count, 2);
  });
});

test("TUI result formatter shows human summary before full JSON", () => {
  const output = formatTuiOutput("migrate", {
    dry_run: true,
    actions: [
      { kind: "merge", alias: "same" },
      { kind: "fork", alias: "different" },
      { kind: "fork", alias: "different" }
    ]
  });

  assert.match(output, /^Summary\n- Preview: 3 migration actions \(merge: 1 · fork: 2\)\./);
  assert.match(output, /Full JSON\n\{/);
  assert.match(output, /"dry_run": true/);
});

test("TUI summaries cover scan, diff, doctor, and materialize result shapes", () => {
  assert.deepEqual(summarizeTuiResult("scan", {
    locations: {
      inbox: { entries: [{ name: "a" }] },
      store: { entries: [] }
    }
  }), ["inbox: 1 entry · store: 0 entries"]);

  assert.deepEqual(summarizeTuiResult("diff", {
    claude: { creates: [{ skill_id: "a" }], removes: [], conflicts: [] },
    codex: { creates: [], removes: [{ skill_id: "b" }], conflicts: [{ alias: "c" }] }
  }), ["claude: 1 create · 0 removes · 0 conflicts\ncodex: 0 creates · 1 remove · 1 conflict"]);

  assert.deepEqual(summarizeTuiResult("doctor", { ok: false, issues: [{ type: "conflict" }, { type: "broken_symlink" }] }), ["Doctor found 2 issues."]);

  assert.deepEqual(summarizeTuiResult("materialize", {
    claude: { ok: true, actions: [{ kind: "create" }], transaction_id: "tx-1" },
    codex: { ok: false, actions: [], error: "conflicts detected" }
  }), ["claude: ok · 1 action · tx tx-1\ncodex: blocked · 0 actions · conflicts detected"]);
});

test("typed confirmations prevent high-impact TUI actions from mutating by accident", async () => {
  const rollback = await executeTuiAction("rollback", { transaction: "missing" });
  assert.deepEqual(rollback, { ok: false, cancelled: true, message: "rollback requires typed confirmation" });

  const migratePreview = await executeTuiAction("migrate", { mode: "apply" });
  assert.equal((migratePreview as { dry_run?: boolean }).dry_run, true);
});

test("Ink TUI requires typed confirmation before applying migration mode", async () => {
  const oldHome = process.env.SKILLS_MANAGER_HOME;
  process.env.SKILLS_MANAGER_HOME = await mkdtemp(join(tmpdir(), "sm-ink-confirm-"));
  const TestApp = SkillsManagerApp as unknown as ComponentType<{ initialAction: string }>;
  const instance = render(createElement(TestApp, { initialAction: "migrate" }));
  try {
    instance.stdin.write("\r");
    await settle();
    assert.match(instance.lastFrame() ?? "", /Mode/);

    instance.stdin.write("j");
    await settle();
    instance.stdin.write("\r");
    await settle();
    assert.match(instance.lastFrame() ?? "", /Type MIGRATE/);

    instance.stdin.write("\r");
    assert.match(await waitForFrame(instance, /"dry_run": true/), /"dry_run": true/);
  } finally {
    instance.cleanup();
    if (oldHome === undefined) {
      delete process.env.SKILLS_MANAGER_HOME;
    } else {
      process.env.SKILLS_MANAGER_HOME = oldHome;
    }
  }
});

test("Ink TUI output panes scroll instead of truncating all content away", async () => {
  const oldHome = process.env.SKILLS_MANAGER_HOME;
  process.env.SKILLS_MANAGER_HOME = await mkdtemp(join(tmpdir(), "sm-ink-scroll-"));
  const instance = render(createElement(SkillsManagerApp));
  try {
    instance.stdin.write("\r");
    await settle();
    instance.stdin.write("\r");
    await settle();
    instance.stdin.write("\r");
    await settle();
    const firstFrame = await waitForFrame(instance, /Scan skill locations result/);
    assert.match(firstFrame, /Scan skill locations result/);

    instance.stdin.write("j");
    await settle();
    const scrolledFrame = instance.lastFrame() ?? "";
    assert.notEqual(scrolledFrame, firstFrame);
  } finally {
    instance.cleanup();
    if (oldHome === undefined) {
      delete process.env.SKILLS_MANAGER_HOME;
    } else {
      process.env.SKILLS_MANAGER_HOME = oldHome;
    }
  }
});

test("Ink TUI action menu supports focused filtering", async () => {
  const instance = render(createElement(SkillsManagerApp));
  try {
    instance.stdin.write("/");
    await settle();
    instance.stdin.write("rollback");
    const frame = stripAnsi(await waitForFrame(instance, /1\/24 actions/));
    assert.match(frame, /filter: rollback/);
    assert.match(frame, /Rollback transaction/);
    assert.doesNotMatch(frame, /Scan skill locations/);
  } finally {
    instance.cleanup();
  }
});

test("Ink TUI output panes support line filtering and top/bottom navigation", async () => {
  await withTempHome("sm-ink-output-filter-", async () => {
    const instance = render(createElement(SkillsManagerApp));
    try {
      instance.stdin.write("\r");
      await settle();
      instance.stdin.write("\r");
      await settle();
      instance.stdin.write("\r");
      await settle();
      await waitForFrame(instance, /Scan skill locations result/);

      instance.stdin.write("/");
      await settle();
      instance.stdin.write("Full JSON");
      const filteredFrame = stripAnsi(await waitForFrame(instance, /filter: Full JSON/));
      assert.match(filteredFrame, /1\/\d+ lines/);
      assert.match(filteredFrame, /Full JSON/);
      assert.doesNotMatch(filteredFrame, /Summary\n- /);

      instance.stdin.write("\u001b");
      await settle();
      instance.stdin.write("G");
      await settle();
      const bottomFrame = instance.lastFrame() ?? "";
      instance.stdin.write("g");
      await settle();
      const topFrame = instance.lastFrame() ?? "";
      assert.notEqual(bottomFrame, topFrame);
    } finally {
      instance.cleanup();
    }
  });
});
