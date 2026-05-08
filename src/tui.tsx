import { useEffect, useMemo, useState, type ReactElement } from "react";
import { Box, Text, render, useApp, useInput } from "ink";
import { expandClients } from "./core/adapters.js";
import { dryRunExport, exportBackup, restoreBackup } from "./core/backup.js";
import { diff as materializerDiff, materialize } from "./core/materializer.js";
import { adoptSkill, importInbox, migrateApply, migratePlan } from "./core/planner.js";
import {
  addEntries,
  applyPreset,
  capturePreset,
  createPreset,
  deletePreset,
  listPresets,
  removeEntries,
  renamePreset,
  showPreset,
  validatePresets
} from "./core/presets.js";
import { resolveDesired, resolveSkillRef, setSkill } from "./core/resolver.js";
import { scan } from "./core/scanner.js";
import { allSkills, stableJson } from "./core/store.js";
import { rollback } from "./core/transactions.js";
import type { Client, Scope } from "./core/schemas.js";

export type TuiAction =
  | "scan"
  | "import"
  | "adopt"
  | "migrate"
  | "state"
  | "enable"
  | "disable"
  | "diff"
  | "materialize"
  | "doctor"
  | "rollback"
  | "backup"
  | "restore"
  | "preset-list"
  | "preset-show"
  | "preset-create"
  | "preset-capture"
  | "preset-add"
  | "preset-remove"
  | "preset-rename"
  | "preset-delete"
  | "preset-apply"
  | "quit";

export const TUI_ACTIONS: Array<{ value: TuiAction; name: string; description: string }> = [
  { value: "scan", name: "Scan skill locations", description: "Read-only inventory of inbox, store, Claude, and Codex dirs." },
  { value: "import", name: "Import inbox skills", description: "Preview/apply copying ~/.agents/skills entries into the managed store." },
  { value: "adopt", name: "Adopt a skill path", description: "Copy one external skill directory into the managed store." },
  { value: "migrate", name: "Migrate Claude/Codex skills", description: "Preview/apply copying existing rendered skills into the managed store." },
  { value: "state", name: "View desired state", description: "Resolve manifests for Claude/Codex." },
  { value: "enable", name: "Enable a skill", description: "Write desired visibility to a manifest; does not render." },
  { value: "disable", name: "Disable a skill", description: "Write a disable mask to a manifest; does not render." },
  { value: "diff", name: "Diff desired vs rendered", description: "Show creates/removes/conflicts before materializing." },
  { value: "materialize", name: "Materialize skills", description: "Preview/apply rendering manager-owned links/copies into client dirs." },
  { value: "doctor", name: "Doctor", description: "Audit broken links, conflicts, missing SKILL.md, and preset validity." },
  { value: "rollback", name: "Rollback transaction", description: "Undo manager-created render effects from a journal." },
  { value: "backup", name: "Backup", description: "Preview/export managed store, inbox, logs, presets, and rendered metadata." },
  { value: "restore", name: "Restore backup", description: "Preview/apply copying backup store/inbox data; rendered dirs stay metadata-only." },
  { value: "preset-list", name: "Preset: list", description: "List reusable preset templates." },
  { value: "preset-show", name: "Preset: show", description: "Inspect a preset and stale alias/unknown ID issues." },
  { value: "preset-create", name: "Preset: create empty", description: "Create an empty preset template." },
  { value: "preset-capture", name: "Preset: capture scope", description: "Create a preset from an existing global/project manifest." },
  { value: "preset-add", name: "Preset: add skills", description: "Add enable/disable entries to a preset." },
  { value: "preset-remove", name: "Preset: remove skills", description: "Remove enable/disable entries from a preset." },
  { value: "preset-rename", name: "Preset: rename", description: "Preview/apply renaming a preset JSON file." },
  { value: "preset-delete", name: "Preset: delete", description: "Preview/apply deleting only the preset JSON definition." },
  { value: "preset-apply", name: "Preset: apply to manifest", description: "Stamp preset entries into global/project manifests; does not render." },
  { value: "quit", name: "Quit", description: "Exit skills-manager." }
];

type Prompt =
  | { name: string; message: string; type: "input"; default?: string; required?: boolean; when?: (answers: Answers) => boolean }
  | { name: string; message: string; type: "confirm"; default?: boolean; when?: (answers: Answers) => boolean }
  | { name: string; message: string; type: "typed-confirm"; phrase: string; when?: (answers: Answers) => boolean }
  | { name: string; message: string; type: "select"; choices: string[]; default?: string; when?: (answers: Answers) => boolean };

type Answers = Record<string, string | boolean>;
type Screen = "menu" | "prompt" | "running" | "output";

export function tuiActionCoverage(): string[] {
  return TUI_ACTIONS.map((action) => action.value).filter((value) => value !== "quit").sort();
}

function optionalProject(value: Answers): { project?: string } {
  const project = String(value.project ?? "").trim();
  return project ? { project } : {};
}

function clientValue(value: Answers): Client | "all" {
  const client = String(value.client ?? "all");
  if (client === "claude" || client === "codex" || client === "all") {
    return client;
  }
  throw new Error(`unknown client: ${client}`);
}

function scopeValue(value: Answers): Scope {
  const scope = String(value.scope ?? "global");
  if (scope === "global" || scope === "project" || scope === "session") {
    return scope;
  }
  throw new Error(`unknown scope: ${scope}`);
}

function presetScopeValue(value: Answers): "global" | "project" {
  const scope = String(value.scope ?? "global");
  if (scope === "global" || scope === "project") {
    return scope;
  }
  throw new Error(`unknown preset scope: ${scope}`);
}

function csv(value: Answers, name: string): string[] {
  return String(value[name] ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

function isApplyMode(value: Answers): boolean {
  return value.mode === "apply" || value.mode === "export";
}

function confirmed(value: Answers, name: string): boolean {
  return value[name] === true;
}

export function promptsForAction(action: TuiAction): Prompt[] {
  switch (action) {
    case "scan":
    case "state":
    case "diff":
    case "doctor":
      return [
        ...(action === "state" || action === "diff" ? [{ name: "client", message: "Client", type: "select" as const, choices: ["all", "claude", "codex"], default: "all" }] : []),
        { name: "project", message: "Project path (blank for global only)", type: "input" }
      ];
    case "import":
      return [
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmImport", message: "Type IMPORT to copy inbox skills into the managed store", type: "typed-confirm", phrase: "IMPORT", when: isApplyMode }
      ];
    case "adopt":
      return [{ name: "path", message: "Skill directory path", type: "input", required: true }];
    case "migrate":
      return [
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmMigrate", message: "Type MIGRATE to copy existing Claude/Codex skills into the managed store", type: "typed-confirm", phrase: "MIGRATE", when: isApplyMode }
      ];
    case "enable":
    case "disable":
      return [
        { name: "scope", message: "Scope", type: "select", choices: ["global", "project", "session"], default: "global" },
        { name: "client", message: "Client", type: "select", choices: ["all", "claude", "codex"], default: "all" },
        { name: "project", message: "Project path (only used for project scope)", type: "input" },
        { name: "skill", message: "Skill id/alias", type: "input", required: true }
      ];
    case "materialize":
      return [
        { name: "client", message: "Client", type: "select", choices: ["all", "claude", "codex"], default: "all" },
        { name: "project", message: "Project path (blank for global)", type: "input" },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmMaterialize", message: "Type MATERIALIZE to render desired state into client skill dirs", type: "typed-confirm", phrase: "MATERIALIZE", when: isApplyMode }
      ];
    case "rollback":
      return [
        { name: "transaction", message: "Transaction id", type: "input", required: true },
        { name: "confirmRollback", message: "Type ROLLBACK to undo this transaction", type: "typed-confirm", phrase: "ROLLBACK" }
      ];
    case "backup":
      return [
        { name: "exportPath", message: "Export path", type: "input", default: "./agent-skills-backup" },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "export"], default: "preview" },
        { name: "confirmBackup", message: "Type BACKUP to write the export directory", type: "typed-confirm", phrase: "BACKUP", when: isApplyMode }
      ];
    case "restore":
      return [
        { name: "from", message: "Backup path", type: "input", required: true },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmRestore", message: "Type RESTORE to copy backup state into the managed store", type: "typed-confirm", phrase: "RESTORE", when: isApplyMode }
      ];
    case "preset-show":
      return [{ name: "name", message: "Preset name", type: "input", required: true }];
    case "preset-create":
      return [
        { name: "name", message: "Preset name", type: "input", required: true },
        { name: "description", message: "Description", type: "input" },
        { name: "dryRun", message: "Dry-run only?", type: "confirm", default: false }
      ];
    case "preset-capture":
      return [
        { name: "name", message: "Preset name", type: "input", required: true },
        { name: "scope", message: "Scope", type: "select", choices: ["global", "project"], default: "global" },
        { name: "project", message: "Project path (only used for project scope)", type: "input" },
        { name: "dryRun", message: "Dry-run only?", type: "confirm", default: false }
      ];
    case "preset-add":
    case "preset-remove":
      return [
        { name: "name", message: "Preset name", type: "input", required: true },
        { name: "skills", message: "Skill ids/aliases, comma separated", type: "input", required: true },
        { name: "mode", message: "Mode", type: "select", choices: ["enable", "disable"], default: "enable" },
        { name: "dryRun", message: "Dry-run only?", type: "confirm", default: false }
      ];
    case "preset-rename":
      return [
        { name: "oldName", message: "Old preset name", type: "input", required: true },
        { name: "newName", message: "New preset name", type: "input", required: true },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmRename", message: "Type RENAME to rename the preset JSON file", type: "typed-confirm", phrase: "RENAME", when: isApplyMode }
      ];
    case "preset-delete":
      return [
        { name: "name", message: "Preset name", type: "input", required: true },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmDelete", message: "Type DELETE to delete only the preset JSON file", type: "typed-confirm", phrase: "DELETE", when: isApplyMode }
      ];
    case "preset-apply":
      return [
        { name: "name", message: "Preset name", type: "input", required: true },
        { name: "scope", message: "Scope", type: "select", choices: ["global", "project"], default: "global" },
        { name: "project", message: "Project path (only used for project scope)", type: "input" },
        { name: "client", message: "Client", type: "select", choices: ["all", "claude", "codex"], default: "all" },
        { name: "replace", message: "Replace target manifest buckets?", type: "confirm", default: false },
        { name: "mode", message: "Mode", type: "select", choices: ["preview", "apply"], default: "preview" },
        { name: "confirmPresetApply", message: "Type APPLY to stamp preset entries into manifests", type: "typed-confirm", phrase: "APPLY", when: isApplyMode }
      ];
    default:
      return [];
  }
}

export async function executeTuiAction(action: TuiAction, answers: Answers = {}): Promise<unknown> {
  if (action === "scan") {
    return scan(optionalProject(answers));
  }
  if (action === "import") {
    return importInbox({ dryRun: !confirmed(answers, "confirmImport") });
  }
  if (action === "adopt") {
    return adoptSkill(String(answers.path ?? ""));
  }
  if (action === "migrate") {
    return confirmed(answers, "confirmMigrate") ? migrateApply() : { ...(await migratePlan()), dry_run: true };
  }
  if (action === "state") {
    const client = clientValue(answers);
    const data = Object.fromEntries(await Promise.all(expandClients(client).map(async (current) => [current, await resolveDesired(current, optionalProject(answers))])));
    return client === "all" ? data : data[client];
  }
  if (action === "enable" || action === "disable") {
    const scope = scopeValue(answers);
    const project = scope === "project" ? optionalProject(answers) : {};
    const skillId = await resolveSkillRef(String(answers.skill ?? ""));
    const manifest = await setSkill(scope, skillId, action === "enable", { client: clientValue(answers), ...project, surface: "tui" });
    return { ok: true, enabled: action === "enable", scope, client: clientValue(answers), skill_id: skillId, manifest };
  }
  if (action === "diff") {
    const client = clientValue(answers);
    const data = Object.fromEntries(await Promise.all(expandClients(client).map(async (current) => [current, await materializerDiff(current, optionalProject(answers))])));
    return client === "all" ? data : data[client];
  }
  if (action === "materialize") {
    const client = clientValue(answers);
    return Object.fromEntries(
      await Promise.all(expandClients(client).map(async (current) => [current, await materialize(current, { ...optionalProject(answers), dryRun: !confirmed(answers, "confirmMaterialize"), surface: "tui" })]))
    );
  }
  if (action === "doctor") {
    const scanned = await scan(optionalProject(answers));
    const issues: Array<Record<string, unknown>> = [];
    for (const [location, value] of Object.entries(scanned.locations)) {
      for (const entry of value.entries) {
        if (entry.type === "broken_symlink" || entry.type === "missing_skill_md" || entry.type === "error") {
          issues.push({ location, ...entry });
        }
      }
    }
    for (const client of ["claude", "codex"] as const) {
      const d = await materializerDiff(client, optionalProject(answers));
      for (const conflict of d.conflicts as Array<Record<string, unknown>>) {
        issues.push({ location: `${client}_rendered`, type: "conflict", ...conflict });
      }
    }
    issues.push(...(await validatePresets()));
    return { ok: issues.length === 0, issues };
  }
  if (action === "rollback") {
    if (!confirmed(answers, "confirmRollback")) {
      return { ok: false, cancelled: true, message: "rollback requires typed confirmation" };
    }
    return rollback(String(answers.transaction ?? ""));
  }
  if (action === "backup") {
    const exportPath = String(answers.exportPath || "./agent-skills-backup");
    return confirmed(answers, "confirmBackup") ? exportBackup(exportPath) : dryRunExport(exportPath);
  }
  if (action === "restore") {
    return restoreBackup(String(answers.from ?? ""), { dryRun: !confirmed(answers, "confirmRestore") });
  }
  if (action === "preset-list") {
    return listPresets();
  }
  if (action === "preset-show") {
    return showPreset(String(answers.name ?? ""));
  }
  if (action === "preset-create") {
    return createPreset(String(answers.name ?? ""), { description: String(answers.description ?? ""), dryRun: Boolean(answers.dryRun), surface: "tui" });
  }
  if (action === "preset-capture") {
    const scope = presetScopeValue(answers);
    return capturePreset(String(answers.name ?? ""), scope, { ...(scope === "project" ? optionalProject(answers) : {}), dryRun: Boolean(answers.dryRun), surface: "tui" });
  }
  if (action === "preset-add" || action === "preset-remove") {
    const mode = String(answers.mode ?? "enable") === "disable" ? "disable" : "enable";
    return action === "preset-add"
      ? addEntries(String(answers.name ?? ""), csv(answers, "skills"), { mode, dryRun: Boolean(answers.dryRun), surface: "tui" })
      : removeEntries(String(answers.name ?? ""), csv(answers, "skills"), { mode, dryRun: Boolean(answers.dryRun), surface: "tui" });
  }
  if (action === "preset-rename") {
    return renamePreset(String(answers.oldName ?? ""), String(answers.newName ?? ""), { apply: confirmed(answers, "confirmRename"), surface: "tui" });
  }
  if (action === "preset-delete") {
    return deletePreset(String(answers.name ?? ""), { apply: confirmed(answers, "confirmDelete"), surface: "tui" });
  }
  if (action === "preset-apply") {
    const scope = presetScopeValue(answers);
    return applyPreset(String(answers.name ?? ""), scope, {
      ...(scope === "project" ? optionalProject(answers) : {}),
      client: clientValue(answers),
      replace: Boolean(answers.replace),
      dryRun: !confirmed(answers, "confirmPresetApply"),
      surface: "tui"
    });
  }
  return { ok: true };
}

function promptDefault(prompt: Prompt): string | boolean {
  if (prompt.type === "confirm") {
    return prompt.default ?? false;
  }
  if (prompt.type === "typed-confirm") {
    return "";
  }
  return prompt.default ?? "";
}

function promptApplies(prompt: Prompt, answers: Answers): boolean {
  return prompt.when ? prompt.when(answers) : true;
}

function nextPromptIndex(prompts: Prompt[], start: number, answers: Answers): number {
  for (let index = start; index < prompts.length; index += 1) {
    if (promptApplies(prompts[index]!, answers)) {
      return index;
    }
  }
  return -1;
}

export function visibleOutput(output: string, offset: number, height = 18): string[] {
  const lines = output.split("\n");
  return lines.slice(offset, offset + height);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function keyedCountSummary(value: unknown, counter: (item: unknown) => number): string {
  const entries = Object.entries(asRecord(value));
  if (entries.length === 0) {
    return "No entries.";
  }
  return entries.map(([key, item]) => `${key}: ${counter(item)}`).join(" · ");
}

function arrayFieldCount(value: unknown, field: string): number {
  return asArray(asRecord(value)[field]).length;
}

function summarizeDiffLike(value: unknown): string {
  const data = asRecord(value);
  if ("creates" in data || "removes" in data || "conflicts" in data) {
    return [
      countLabel(arrayFieldCount(data, "creates"), "create"),
      countLabel(arrayFieldCount(data, "removes"), "remove"),
      countLabel(arrayFieldCount(data, "conflicts"), "conflict")
    ].join(" · ");
  }
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return "No diff entries.";
  }
  return entries.map(([client, item]) => `${client}: ${summarizeDiffLike(item)}`).join("\n");
}

function summarizeMaterialize(value: unknown): string {
  const entries = Object.entries(asRecord(value));
  if (entries.length === 0) {
    return "No materialization result.";
  }
  return entries.map(([client, result]) => {
    const current = asRecord(result);
    const status = current.ok === false ? "blocked" : "ok";
    const actions = arrayFieldCount(current, "actions");
    const tx = typeof current.transaction_id === "string" ? ` · tx ${current.transaction_id}` : "";
    const error = typeof current.error === "string" ? ` · ${current.error}` : "";
    return `${client}: ${status} · ${countLabel(actions, "action")}${tx}${error}`;
  }).join("\n");
}

function summarizeScan(value: unknown): string {
  const locations = asRecord(asRecord(value).locations);
  if (Object.keys(locations).length === 0) {
    return "No scan locations returned.";
  }
  return Object.entries(locations).map(([name, location]) => {
    const entries = arrayFieldCount(location, "entries");
    return `${name}: ${countLabel(entries, "entry", "entries")}`;
  }).join(" · ");
}

function summarizeMigrate(value: unknown): string {
  const data = asRecord(value);
  const actions = asArray(data.actions);
  const applied = asArray(data.applied);
  const list = actions.length > 0 ? actions : applied;
  const counts = new Map<string, number>();
  for (const item of list) {
    const kind = String(asRecord(item).kind ?? "item");
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  const byKind = [...counts.entries()].map(([kind, count]) => `${kind}: ${count}`).join(" · ");
  if (actions.length > 0) {
    return `Preview: ${countLabel(actions.length, "migration action")}${byKind ? ` (${byKind})` : ""}.`;
  }
  if (applied.length > 0) {
    return `Applied: ${countLabel(applied.length, "migration action")}${byKind ? ` (${byKind})` : ""}.`;
  }
  return "No migration actions.";
}

function summarizeState(value: unknown): string {
  const countDesired = (item: unknown): number => Object.keys(asRecord(asRecord(item).desired)).length;
  const data = asRecord(value);
  if ("desired" in data) {
    return `Desired: ${countLabel(countDesired(data), "skill")}.`;
  }
  return keyedCountSummary(data, countDesired);
}

function summarizePresetMutation(value: unknown): string {
  const data = asRecord(value);
  const parts: string[] = [];
  for (const field of ["added", "removed", "enable", "disable", "issues"]) {
    if (Array.isArray(data[field])) {
      parts.push(`${field}: ${asArray(data[field]).length}`);
    }
  }
  for (const field of ["written", "would_write", "deleted", "would_delete", "renamed", "would_rename", "applied", "dry_run"]) {
    if (field in data) {
      parts.push(`${field}: ${String(data[field])}`);
    }
  }
  return parts.length > 0 ? parts.join(" · ") : "Preset command completed.";
}

export function summarizeTuiResult(action: TuiAction, result: unknown): string[] {
  const data = asRecord(result);
  if (data.ok === false && typeof data.error === "string") {
    return [`Blocked: ${data.error}`];
  }
  if (data.cancelled === true && typeof data.message === "string") {
    return [`Cancelled: ${data.message}`];
  }

  switch (action) {
    case "scan":
      return [summarizeScan(result)];
    case "import": {
      const candidates = arrayFieldCount(result, "candidates");
      const adopted = arrayFieldCount(result, "adopted");
      return [adopted > 0 ? `Applied: ${countLabel(adopted, "inbox skill")} copied into the managed store.` : `Preview: ${countLabel(candidates, "inbox candidate")} found.`];
    }
    case "adopt":
      return [`Adopted ${String(data.skill_id ?? "skill")} into the managed store. Import/adopt does not enable or render it.`];
    case "migrate":
      return [summarizeMigrate(result), "Migration copies into managed state; it does not enable or materialize skills."];
    case "state":
      return [summarizeState(result)];
    case "enable":
    case "disable":
      return [`${action === "enable" ? "Enabled" : "Disabled"} ${String(data.skill_id ?? "skill")} in ${String(data.scope ?? "scope")} / ${String(data.client ?? "client")}.`, "Desired state changed; run Materialize to render it."];
    case "diff":
      return [summarizeDiffLike(result)];
    case "materialize":
      return [summarizeMaterialize(result)];
    case "doctor": {
      const issues = arrayFieldCount(result, "issues");
      return [data.ok === true ? "Doctor passed: no issues found." : `Doctor found ${countLabel(issues, "issue")}.`];
    }
    case "rollback":
      return [data.ok === true ? "Rollback completed." : "Rollback did not complete."];
    case "backup":
      return [`Backup ${data.exported ? "exported" : "preview"}: ${String(data.target ?? data.path ?? "target unavailable")}.`];
    case "restore":
      return [`Restore ${data.restored ? "applied" : "preview"}: rendered client directories remain metadata-only.`];
    case "preset-list":
      return [`${countLabel(asArray(result).length, "preset")} found.`];
    case "preset-show":
      return [`Preset ${String(data.name ?? "details")} loaded. ${countLabel(arrayFieldCount(result, "issues"), "issue")} found.`];
    case "preset-create":
    case "preset-capture":
    case "preset-add":
    case "preset-remove":
    case "preset-rename":
    case "preset-delete":
    case "preset-apply":
      return [summarizePresetMutation(result)];
    default:
      return ["Command completed."];
  }
}

export function formatTuiOutput(action: TuiAction, result: unknown): string {
  const summary = summarizeTuiResult(action, result).flatMap((line) => line.split("\n"));
  return [
    "Summary",
    ...summary.map((line) => `- ${line}`),
    "",
    "Full JSON",
    ...stableJson(result).trimEnd().split("\n")
  ].join("\n");
}

type SkillsManagerAppProps = {
  initialAction?: TuiAction;
};

export function SkillsManagerApp({ initialAction = "scan" }: SkillsManagerAppProps = {}): ReactElement {
  const { exit } = useApp();
  const initialMenuIndex = Math.max(0, TUI_ACTIONS.findIndex((item) => item.value === initialAction));
  const [screen, setScreen] = useState<Screen>("menu");
  const [menuIndex, setMenuIndex] = useState(initialMenuIndex);
  const [activeAction, setActiveAction] = useState<TuiAction>("scan");
  const [promptIndex, setPromptIndex] = useState(0);
  const [answers, setAnswers] = useState<Answers>({});
  const [inputValue, setInputValue] = useState("");
  const [selectIndex, setSelectIndex] = useState(0);
  const [output, setOutput] = useState("");
  const [scroll, setScroll] = useState(0);
  const [hint, setHint] = useState("");
  const [firstRunHint, setFirstRunHint] = useState("");

  const prompts = useMemo(() => promptsForAction(activeAction), [activeAction]);
  const currentPrompt = prompts[promptIndex];

  useEffect(() => {
    let cancelled = false;
    void allSkills().then((skills) => {
      if (!cancelled && Object.keys(skills).length === 0) {
        setFirstRunHint("Empty managed store detected. Suggested path: scan → backup → import/migrate → enable or preset → materialize → doctor.");
      }
    }).catch(() => {
      if (!cancelled) {
        setFirstRunHint("");
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function resetMenu(): void {
    setScreen("menu");
    setPromptIndex(0);
    setAnswers({});
    setInputValue("");
    setSelectIndex(0);
    setHint("");
  }

  async function runSelected(action: TuiAction, values: Answers): Promise<void> {
    setScreen("running");
    setHint("");
    try {
      const result = await executeTuiAction(action, values);
      setOutput(formatTuiOutput(action, result));
    } catch (error) {
      const result = { ok: false, error: error instanceof Error ? error.message : String(error) };
      setOutput(formatTuiOutput(action, result));
    }
    setScroll(0);
    setScreen("output");
  }

  function startAction(action: TuiAction): void {
    if (action === "quit") {
      exit();
      return;
    }
    setActiveAction(action);
    const nextPrompts = promptsForAction(action);
    setAnswers({});
    setPromptIndex(0);
    setHint("");
    if (nextPrompts.length === 0) {
      void runSelected(action, {});
      return;
    }
    const firstPromptIndex = nextPromptIndex(nextPrompts, 0, {});
    if (firstPromptIndex < 0) {
      void runSelected(action, {});
      return;
    }
    const firstPrompt = nextPrompts[firstPromptIndex]!;
    setPromptIndex(firstPromptIndex);
    setInputValue(String(promptDefault(firstPrompt)));
    setSelectIndex(Math.max(0, firstPrompt.type === "select" ? firstPrompt.choices.indexOf(String(firstPrompt.default ?? firstPrompt.choices[0])) : 0));
    setScreen("prompt");
  }

  function advancePrompt(value: string | boolean): void {
    if (!currentPrompt) {
      return;
    }
    if (currentPrompt.type === "input" && currentPrompt.required && !String(value).trim()) {
      setHint("Required.");
      return;
    }
    const nextAnswers = { ...answers, [currentPrompt.name]: value };
    const nextIndex = nextPromptIndex(prompts, promptIndex + 1, nextAnswers);
    if (nextIndex < 0) {
      setAnswers(nextAnswers);
      void runSelected(activeAction, nextAnswers);
      return;
    }
    const nextPrompt = prompts[nextIndex]!;
    setAnswers(nextAnswers);
    setPromptIndex(nextIndex);
    setHint("");
    setInputValue(String(promptDefault(nextPrompt)));
    setSelectIndex(Math.max(0, nextPrompt.type === "select" ? nextPrompt.choices.indexOf(String(nextPrompt.default ?? nextPrompt.choices[0])) : 0));
  }

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      exit();
      return;
    }
    if (screen === "menu") {
      if (input === "q") {
        exit();
      } else if (key.upArrow || input === "k") {
        setMenuIndex((value) => Math.max(0, value - 1));
      } else if (key.downArrow || input === "j") {
        setMenuIndex((value) => Math.min(TUI_ACTIONS.length - 1, value + 1));
      } else if (key.return) {
        startAction(TUI_ACTIONS[menuIndex]!.value);
      }
      return;
    }
    if (screen === "prompt" && currentPrompt) {
      if (key.escape) {
        resetMenu();
      } else if (currentPrompt.type === "select") {
        if (key.upArrow || input === "k") {
          setSelectIndex((value) => Math.max(0, value - 1));
        } else if (key.downArrow || input === "j") {
          setSelectIndex((value) => Math.min(currentPrompt.choices.length - 1, value + 1));
        } else if (key.return) {
          advancePrompt(currentPrompt.choices[selectIndex] ?? currentPrompt.choices[0] ?? "");
        }
      } else if (currentPrompt.type === "confirm") {
        if (input.toLowerCase() === "y") {
          advancePrompt(true);
        } else if (input.toLowerCase() === "n") {
          advancePrompt(false);
        } else if (key.return) {
          advancePrompt(Boolean(currentPrompt.default));
        }
      } else if (currentPrompt.type === "typed-confirm") {
        if (key.return) {
          const typed = inputValue.trim();
          if (!typed) {
            advancePrompt(false);
          } else if (typed === currentPrompt.phrase) {
            advancePrompt(true);
          } else {
            setHint(`Type ${currentPrompt.phrase} exactly, or leave blank to keep preview/cancel.`);
          }
        } else if (key.backspace || key.delete) {
          setInputValue((value) => value.slice(0, -1));
        } else if (input && !key.ctrl && !key.meta) {
          setInputValue((value) => value + input);
        }
      } else if (currentPrompt.type === "input") {
        if (key.return) {
          advancePrompt(inputValue);
        } else if (key.backspace || key.delete) {
          setInputValue((value) => value.slice(0, -1));
        } else if (input && !key.ctrl && !key.meta) {
          setInputValue((value) => value + input);
        }
      }
      return;
    }
    if (screen === "output") {
      if (input === "q" || key.escape || key.return) {
        resetMenu();
      } else if (key.upArrow || input === "k") {
        setScroll((value) => Math.max(0, value - 1));
      } else if (key.downArrow || input === "j") {
        const total = output.split("\n").length;
        setScroll((value) => Math.min(Math.max(0, total - 1), value + 1));
      } else if (key.pageUp) {
        setScroll((value) => Math.max(0, value - 10));
      } else if (key.pageDown) {
        const total = output.split("\n").length;
        setScroll((value) => Math.min(Math.max(0, total - 1), value + 10));
      }
    }
  });

  const selected = TUI_ACTIONS[menuIndex]!;

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="cyan">skills-manager</Text>
      <Text dimColor>Ink React TUI · arrows navigate · enter selects · esc/back returns · q quits</Text>
      {screen === "menu" && (
        <Box flexDirection="column" marginTop={1}>
          {TUI_ACTIONS.map((item, index) => (
            <Text key={item.value} color={index === menuIndex ? "yellow" : "white"}>
              {index === menuIndex ? "› " : "  "}{item.name}
            </Text>
          ))}
          <Box marginTop={1} flexDirection="column">
            <Text color="cyan">{selected.description}</Text>
            {firstRunHint ? <Text color="yellow">{firstRunHint}</Text> : null}
            <Text dimColor>This TUI calls the TypeScript core directly; no CLI memorization.</Text>
          </Box>
        </Box>
      )}
      {screen === "prompt" && currentPrompt && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold>{TUI_ACTIONS.find((item) => item.value === activeAction)?.name}</Text>
          <Text dimColor>Step {promptIndex + 1}/{prompts.length} · escape cancels</Text>
          <Box marginTop={1} flexDirection="column">
            <Text color="cyan">{currentPrompt.message}</Text>
            {currentPrompt.type === "input" && <Text>› {inputValue}<Text color="yellow">█</Text></Text>}
            {currentPrompt.type === "confirm" && <Text>› y/n default: {currentPrompt.default ? "yes" : "no"}</Text>}
            {currentPrompt.type === "typed-confirm" && <Text>› {inputValue}<Text color="yellow">█</Text> <Text dimColor>(exact phrase: {currentPrompt.phrase}; blank = preview/cancel)</Text></Text>}
            {currentPrompt.type === "select" && currentPrompt.choices.map((choice, index) => (
              <Text key={choice} color={index === selectIndex ? "yellow" : "white"}>{index === selectIndex ? "› " : "  "}{choice}</Text>
            ))}
            {hint ? <Text color="red">{hint}</Text> : null}
          </Box>
        </Box>
      )}
      {screen === "running" && <Text color="yellow">Running {TUI_ACTIONS.find((item) => item.value === activeAction)?.name}…</Text>}
      {screen === "output" && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold>{TUI_ACTIONS.find((item) => item.value === activeAction)?.name} result</Text>
          <Text dimColor>up/down/page scroll · enter/esc/q returns</Text>
          <Box flexDirection="column" marginTop={1}>
            {visibleOutput(output, scroll).map((line, index) => <Text key={`${scroll}-${index}`}>{line || " "}</Text>)}
          </Box>
        </Box>
      )}
    </Box>
  );
}

export async function runTui(): Promise<void> {
  const instance = render(<SkillsManagerApp />, { exitOnCtrlC: true });
  await instance.waitUntilExit();
}
