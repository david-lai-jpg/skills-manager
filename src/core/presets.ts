import { readFile, readdir, rm } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { appendAction } from "./action-log.js";
import { presetsRoot, type Env } from "./paths.js";
import { allSkills, loadManifest, manifestPath, saveManifest, slugify, stableJson, VERSION, writeJson, type SkillMeta } from "./store.js";
import type { Client } from "./schemas.js";

type PresetEntry = { id: string; alias: string };
type Preset = {
  version: number;
  name: string;
  description: string;
  tags: string[];
  enable: PresetEntry[];
  disable: PresetEntry[];
  clients: {
    claude: { enable: PresetEntry[]; disable: PresetEntry[] };
    codex: { enable: PresetEntry[]; disable: PresetEntry[] };
  };
};

export function presetPath(name: string, env: Env = process.env): string {
  return join(presetsRoot(env), `${slugify(name)}.json`);
}

export async function listPresets(env: Env = process.env): Promise<string[]> {
  try {
    return (await readdir(presetsRoot(env))).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -5)).sort();
  } catch {
    return [];
  }
}

function normalizeEntries(values: unknown): PresetEntry[] {
  if (!Array.isArray(values)) {
    return [];
  }
  return values.flatMap((value) => {
    if (typeof value === "string") {
      return [{ id: value, alias: "" }];
    }
    if (value && typeof value === "object" && typeof (value as Record<string, unknown>).id === "string") {
      return [{ id: (value as { id: string }).id, alias: String((value as Record<string, unknown>).alias ?? "") }];
    }
    return [];
  });
}

export function normalizePreset(data: Record<string, unknown>, name: string): Preset {
  const clients = data.clients && typeof data.clients === "object" ? (data.clients as Record<string, unknown>) : {};
  const claude = clients.claude && typeof clients.claude === "object" ? (clients.claude as Record<string, unknown>) : {};
  const codex = clients.codex && typeof clients.codex === "object" ? (clients.codex as Record<string, unknown>) : {};
  return {
    version: typeof data.version === "number" ? data.version : VERSION,
    name: String(data.name || name),
    description: String(data.description || ""),
    tags: Array.isArray(data.tags) ? data.tags.map(String) : [],
    enable: normalizeEntries(data.enable),
    disable: normalizeEntries(data.disable),
    clients: {
      claude: { enable: normalizeEntries(claude.enable), disable: normalizeEntries(claude.disable) },
      codex: { enable: normalizeEntries(codex.enable), disable: normalizeEntries(codex.disable) }
    }
  };
}

export function emptyPreset(name: string, description = "", tags: string[] = []): Preset {
  return normalizePreset({ version: VERSION, name: slugify(name), description, tags }, slugify(name));
}

export async function loadPreset(name: string, env: Env = process.env): Promise<Preset> {
  const path = presetPath(name, env);
  const raw = JSON.parse(await readFile(path, "utf8"));
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("preset must be a JSON object");
  }
  return normalizePreset(raw as Record<string, unknown>, basename(path, ".json"));
}

export async function writePreset(name: string, preset: Preset, env: Env = process.env): Promise<string> {
  const path = presetPath(name, env);
  await writeJson(path, preset);
  return path;
}

export async function createPreset(
  name: string,
  options: { description?: string; tags?: string[]; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const preset = emptyPreset(name, options.description ?? "", options.tags ?? []);
  const path = presetPath(name, options.env);
  const result: Record<string, unknown> = { ok: true, dry_run: options.dryRun ?? false, preset, path };
  if (options.dryRun) {
    result.would_write = true;
    return result;
  }
  await writePreset(name, preset, options.env);
  await appendAction("preset_create", { surface: options.surface ?? "core", preset_name: preset.name, target_path: path }, options.env);
  result.written = true;
  return result;
}

export async function capturePreset(
  name: string,
  scope: "global" | "project",
  options: { project?: string; description?: string; tags?: string[]; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const manifest = await loadManifest(scope, { ...(options.project ? { project: options.project } : {}), ...(options.env ? { env: options.env } : {}) });
  const managed = await allSkills(options.env);
  const preset = emptyPreset(name, options.description ?? "", options.tags ?? []);
  preset.enable = manifest.enable.map((skillId) => presetEntry(skillId, managed));
  preset.disable = manifest.disable.map((skillId) => presetEntry(skillId, managed));
  for (const client of ["claude", "codex"] as const) {
    preset.clients[client].enable = manifest.clients[client].enable.map((skillId) => presetEntry(skillId, managed));
    preset.clients[client].disable = manifest.clients[client].disable.map((skillId) => presetEntry(skillId, managed));
  }

  const path = presetPath(name, options.env);
  const result: Record<string, unknown> = { ok: true, dry_run: options.dryRun ?? false, preset, path, source_scope: scope };
  if (scope === "project") {
    result.project = resolve(options.project ?? process.cwd());
  }
  if (options.dryRun) {
    result.would_write = true;
    return result;
  }
  await writePreset(name, preset, options.env);
  await appendAction(
    "preset_capture",
    { surface: options.surface ?? "core", preset_name: preset.name, scope, target_path: path, project_path: result.project },
    options.env
  );
  result.written = true;
  return result;
}

function presetEntry(skillId: string, managed: Record<string, SkillMeta>): PresetEntry {
  const aliases: Partial<Record<Client, string>> = managed[skillId]?.aliases ?? {};
  return { id: skillId, alias: aliases.claude || aliases.codex || skillId.split(".").at(-1) || skillId };
}

export async function resolveSkillRefs(refs: string[], env: Env = process.env): Promise<Record<string, unknown>> {
  const managed = await allSkills(env);
  const resolved: PresetEntry[] = [];
  const errors: Array<Record<string, unknown>> = [];
  for (const ref of refs) {
    if (managed[ref]) {
      resolved.push(presetEntry(ref, managed));
      continue;
    }
    const matches = Object.entries(managed)
      .filter(([skillId, meta]) => ref === skillId.split(".").at(-1) || Object.values(meta.aliases).includes(ref))
      .map(([skillId]) => skillId);
    if (matches.length === 1) {
      resolved.push(presetEntry(matches[0]!, managed));
    } else if (matches.length > 1) {
      errors.push({ ref, error: "ambiguous", candidates: matches.sort() });
    } else {
      errors.push({ ref, error: "unknown" });
    }
  }
  return errors.length ? { ok: false, errors } : { ok: true, entries: resolved };
}

export async function addEntries(
  name: string,
  refs: string[],
  options: { mode?: "enable" | "disable"; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const mode = options.mode ?? "enable";
  const path = presetPath(name, options.env);
  const resolved = await resolveSkillRefs(refs, options.env);
  if (!resolved.ok) {
    return { ok: false, dry_run: options.dryRun ?? false, path, errors: resolved.errors };
  }
  const preset = await loadPreset(name, options.env);
  const bucket = preset[mode];
  const existing = new Set(bucket.map((entry) => entry.id));
  for (const entry of resolved.entries as PresetEntry[]) {
    if (!existing.has(entry.id)) {
      bucket.push(entry);
      existing.add(entry.id);
    }
  }
  const result: Record<string, unknown> = { ok: true, dry_run: options.dryRun ?? false, preset, path, added: resolved.entries };
  if (options.dryRun) {
    result.would_write = true;
    return result;
  }
  await writePreset(name, preset, options.env);
  await appendAction(
    "preset_add",
    { surface: options.surface ?? "core", preset_name: preset.name, mode, target_path: path, skill_ids: (resolved.entries as PresetEntry[]).map((entry) => entry.id) },
    options.env
  );
  result.written = true;
  return result;
}

export async function removeEntries(
  name: string,
  refs: string[],
  options: { mode?: "enable" | "disable"; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const mode = options.mode ?? "enable";
  const path = presetPath(name, options.env);
  const resolved = await resolveSkillRefs(refs, options.env);
  if (!resolved.ok) {
    return { ok: false, dry_run: options.dryRun ?? false, path, errors: resolved.errors };
  }
  const preset = await loadPreset(name, options.env);
  const removeIds = new Set((resolved.entries as PresetEntry[]).map((entry) => entry.id));
  preset[mode] = preset[mode].filter((entry) => !removeIds.has(entry.id));
  const result: Record<string, unknown> = { ok: true, dry_run: options.dryRun ?? false, preset, path, removed: [...removeIds].sort() };
  if (options.dryRun) {
    result.would_write = true;
    return result;
  }
  await writePreset(name, preset, options.env);
  await appendAction("preset_remove", { surface: options.surface ?? "core", preset_name: preset.name, mode, target_path: path, skill_ids: [...removeIds].sort() }, options.env);
  result.written = true;
  return result;
}

export async function renamePreset(oldName: string, newName: string, options: { apply?: boolean; surface?: string; env?: Env } = {}): Promise<Record<string, unknown>> {
  const oldPath = presetPath(oldName, options.env);
  const newPath = presetPath(newName, options.env);
  const result: Record<string, unknown> = { ok: true, dry_run: !(options.apply ?? false), from: oldPath, to: newPath };
  try {
    await readFile(oldPath);
  } catch {
    return { ...result, ok: false, error: `preset not found: ${oldName}` };
  }
  try {
    await readFile(newPath);
    return { ...result, ok: false, error: `preset already exists: ${newName}` };
  } catch {
    // desired: target does not exist
  }
  if (!options.apply) {
    result.would_rename = true;
    return result;
  }
  const preset = await loadPreset(oldName, options.env);
  preset.name = slugify(newName);
  await writePreset(newName, preset, options.env);
  await rm(oldPath, { force: true });
  await appendAction("preset_rename", { surface: options.surface ?? "core", preset_name: preset.name, old_name: slugify(oldName), target_path: newPath }, options.env);
  result.renamed = true;
  return result;
}

export async function deletePreset(name: string, options: { apply?: boolean; surface?: string; env?: Env } = {}): Promise<Record<string, unknown>> {
  const path = presetPath(name, options.env);
  const result: Record<string, unknown> = { ok: true, dry_run: !(options.apply ?? false), path };
  try {
    await readFile(path);
  } catch {
    return { ...result, ok: false, error: `preset not found: ${name}` };
  }
  if (!options.apply) {
    result.would_delete = true;
    return result;
  }
  await rm(path);
  await appendAction("preset_delete", { surface: options.surface ?? "core", preset_name: slugify(name), target_path: path }, options.env);
  result.deleted = true;
  return result;
}

function entryIds(entries: PresetEntry[]): string[] {
  return entries.map((entry) => entry.id);
}

function iterBuckets(preset: Preset): Array<[string, PresetEntry[]]> {
  return [
    ["enable", preset.enable],
    ["disable", preset.disable],
    ["claude:enable", preset.clients.claude.enable],
    ["claude:disable", preset.clients.claude.disable],
    ["codex:enable", preset.clients.codex.enable],
    ["codex:disable", preset.clients.codex.disable]
  ];
}

function stampBucket(
  bucket: { enable: string[]; disable: string[] },
  enableIds: string[],
  disableIds: string[],
  replace: boolean,
  changes: Array<Record<string, unknown>>,
  bucketName: string
): void {
  if (replace) {
    changes.push({ bucket: bucketName, op: "clear", before: { enable: [...bucket.enable], disable: [...bucket.disable] } });
    bucket.enable = [];
    bucket.disable = [];
  }
  for (const skillId of enableIds) {
    if (!bucket.enable.includes(skillId)) {
      bucket.enable.push(skillId);
      changes.push({ bucket: bucketName, op: "add_enable", skill_id: skillId });
    }
    if (bucket.disable.includes(skillId)) {
      bucket.disable = bucket.disable.filter((value) => value !== skillId);
      changes.push({ bucket: bucketName, op: "remove_disable", skill_id: skillId });
    }
  }
  for (const skillId of disableIds) {
    if (!bucket.disable.includes(skillId)) {
      bucket.disable.push(skillId);
      changes.push({ bucket: bucketName, op: "add_disable", skill_id: skillId });
    }
    if (bucket.enable.includes(skillId)) {
      bucket.enable = bucket.enable.filter((value) => value !== skillId);
      changes.push({ bucket: bucketName, op: "remove_enable", skill_id: skillId });
    }
  }
}

export async function applyPreset(
  name: string,
  scope: "global" | "project",
  options: { project?: string; client?: Client | "all"; replace?: boolean; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const preset = await loadPreset(name, options.env);
  const managed = await allSkills(options.env);
  const unknown = Array.from(new Set(iterBuckets(preset).flatMap(([, entries]) => entries.map((entry) => entry.id)).filter((skillId) => !managed[skillId]))).sort();
  const manifest = await loadManifest(scope, { ...(options.project ? { project: options.project } : {}), ...(options.env ? { env: options.env } : {}) });
  const before = JSON.parse(stableJson(manifest));
  const path = await manifestPath(scope, { ...(options.project ? { project: options.project } : {}), ...(options.env ? { env: options.env } : {}) });
  const client = options.client ?? "all";
  const result: Record<string, unknown> = {
    ok: unknown.length === 0,
    dry_run: options.dryRun ?? false,
    scope,
    client,
    replace: options.replace ?? false,
    preset: name,
    manifest: path,
    before
  };
  if (scope === "project") {
    result.project = resolve(options.project ?? process.cwd());
  }
  if (unknown.length > 0) {
    result.errors = unknown.map((skillId) => ({ type: "unknown_id", skill_id: skillId }));
    result.after = before;
    result.changes = [];
    return result;
  }
  const after = JSON.parse(stableJson(manifest));
  const changes: Array<Record<string, unknown>> = [];
  if (client === "all") {
    stampBucket(after, entryIds(preset.enable), entryIds(preset.disable), options.replace ?? false, changes, "all");
    for (const specific of ["claude", "codex"] as const) {
      stampBucket(after.clients[specific], entryIds(preset.clients[specific].enable), entryIds(preset.clients[specific].disable), options.replace ?? false, changes, specific);
    }
  } else {
    stampBucket(
      after.clients[client],
      [...entryIds(preset.enable), ...entryIds(preset.clients[client].enable)],
      [...entryIds(preset.disable), ...entryIds(preset.clients[client].disable)],
      options.replace ?? false,
      changes,
      client
    );
  }
  result.after = after;
  result.changes = changes;
  if (options.dryRun) {
    result.would_write = true;
    return result;
  }
  await saveManifest(scope, after, { ...(options.project ? { project: options.project } : {}), ...(options.env ? { env: options.env } : {}) });
  await appendAction("preset_apply", { surface: options.surface ?? "core", preset_name: name, scope, client, manifest_path: path, project_path: result.project, replace: options.replace ?? false }, options.env);
  result.written = true;
  return result;
}

export async function showPreset(name: string, env: Env = process.env): Promise<Record<string, unknown>> {
  const preset = await loadPreset(name, env);
  const managed = await allSkills(env);
  const enrich = (entry: PresetEntry) => {
    const meta = managed[entry.id];
    const currentAliases = meta?.aliases ?? {};
    const issues = [];
    if (!meta) {
      issues.push("unknown_id");
    } else if (entry.alias && !Object.values(currentAliases).includes(entry.alias)) {
      issues.push("alias_drift");
    }
    return { id: entry.id, stored_alias: entry.alias, exists: Boolean(meta), current_aliases: currentAliases, issues };
  };
  const result: Record<string, unknown> = {
    version: preset.version,
    name: preset.name,
    description: preset.description,
    tags: preset.tags,
    enable: preset.enable.map(enrich),
    disable: preset.disable.map(enrich),
    clients: {
      claude: { enable: preset.clients.claude.enable.map(enrich), disable: preset.clients.claude.disable.map(enrich) },
      codex: { enable: preset.clients.codex.enable.map(enrich), disable: preset.clients.codex.disable.map(enrich) }
    }
  };
  const issueSet = new Set<string>();
  for (const [, entries] of iterBuckets(preset)) {
    for (const entry of entries) {
      for (const issue of (enrich(entry).issues as string[])) {
        issueSet.add(issue);
      }
    }
  }
  result.issues = [...issueSet].sort();
  return result;
}

function rawBucket(data: Record<string, unknown>, client: Client | null, mode: "enable" | "disable"): unknown {
  if (client === null) {
    return data[mode] ?? [];
  }
  const clients = data.clients && typeof data.clients === "object" ? (data.clients as Record<string, unknown>) : {};
  const clientData = clients[client] && typeof clients[client] === "object" ? (clients[client] as Record<string, unknown>) : {};
  return clientData[mode] ?? [];
}

function schemaIssues(path: string, data: unknown): Array<Record<string, unknown>> {
  const location = `preset:${basename(path, ".json")}`;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return [{ location, type: "preset_malformed", path, message: "preset must be a JSON object" }];
  }
  const raw = data as Record<string, unknown>;
  const issues: Array<Record<string, unknown>> = [];
  for (const client of [null, "claude", "codex"] as const) {
    for (const mode of ["enable", "disable"] as const) {
      const bucket = rawBucket(raw, client, mode);
      const bucketName = client === null ? mode : `${client}:${mode}`;
      if (!Array.isArray(bucket)) {
        issues.push({ location, type: "preset_malformed", path, bucket: bucketName, message: "bucket must be a list" });
        continue;
      }
      bucket.forEach((entry, index) => {
        if (!entry || typeof entry !== "object" || typeof (entry as Record<string, unknown>).id !== "string") {
          issues.push({
            location,
            type: "preset_malformed",
            path,
            bucket: bucketName,
            index,
            message: "entry must be an object with string id"
          });
        }
      });
    }
  }
  return issues;
}

export async function validatePresets(env: Env = process.env): Promise<Array<Record<string, unknown>>> {
  const issues: Array<Record<string, unknown>> = [];
  const managed = await allSkills(env);
  let files: string[];
  try {
    files = (await readdir(presetsRoot(env))).filter((name) => name.endsWith(".json")).sort();
  } catch {
    return issues;
  }
  for (const file of files) {
    const path = join(presetsRoot(env), file);
    const location = `preset:${basename(file, ".json")}`;
    let raw: unknown;
    try {
      raw = JSON.parse(await readFile(path, "utf8"));
    } catch (error) {
      issues.push({ location, type: "preset_malformed", path, message: error instanceof Error ? error.message : String(error) });
      continue;
    }
    const malformed = schemaIssues(path, raw);
    if (malformed.length > 0) {
      issues.push(...malformed);
      continue;
    }
    const preset = normalizePreset(raw as Record<string, unknown>, basename(file, ".json"));
    for (const [bucketName, entries] of iterBuckets(preset)) {
      const seen = new Set<string>();
      for (const entry of entries) {
        if (seen.has(entry.id)) {
          issues.push({ location, type: "preset_duplicate_entry", path, bucket: bucketName, skill_id: entry.id });
        }
        seen.add(entry.id);
        const meta = managed[entry.id];
        if (!meta) {
          issues.push({ location, type: "preset_unknown_id", path, bucket: bucketName, skill_id: entry.id });
          continue;
        }
        const currentAliases = Object.values(meta.aliases).sort();
        if (entry.alias && !currentAliases.includes(entry.alias)) {
          issues.push({
            location,
            type: "preset_alias_drift",
            path,
            bucket: bucketName,
            skill_id: entry.id,
            stored_alias: entry.alias,
            current_aliases: currentAliases
          });
        }
      }
    }
  }
  return issues;
}
