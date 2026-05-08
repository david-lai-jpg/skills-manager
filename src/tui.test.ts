import test from "node:test";
import assert from "node:assert/strict";
import { createElement, type ComponentType } from "react";
import { render } from "ink-testing-library";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { executeTuiAction, SkillsManagerApp, TUI_ACTIONS, formatTuiOutput, promptsForAction, summarizeTuiResult, tuiActionCoverage, visibleOutput } from "./tui.js";

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
    assert.match(await waitForFrame(instance, /Empty managed store detected/), /scan → backup → import\/migrate/);
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
  assert.deepEqual(promptsForAction("preset-delete").map((prompt) => prompt.name), ["name", "mode", "confirmDelete"]);
  assert.deepEqual(promptsForAction("restore").map((prompt) => prompt.name), ["from", "mode", "confirmRestore"]);
  assert.equal(promptsForAction("rollback").find((prompt) => prompt.name === "confirmRollback")?.type, "typed-confirm");
  assert.deepEqual(visibleOutput("a\nb\nc\nd", 1, 2), ["b", "c"]);
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
