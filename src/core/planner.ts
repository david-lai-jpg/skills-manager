import { resolve } from "node:path";
import { adapter } from "./adapters.js";
import { scanDir, type ScanEntry } from "./scanner.js";
import {
  allSkills,
  contentHash,
  copySkillTree,
  ensureStore,
  exists,
  idToDir,
  isSkillDir,
  loadSkillMeta,
  slugify,
  stableId,
  writeSkillMeta,
  type SkillMeta
} from "./store.js";
import { inboxDir, type Env } from "./paths.js";

type Compatibility = { claude: boolean; codex: boolean };

function meta(skillId: string, alias: string, compatibility: Compatibility, sourcePaths: string[], digest: string): SkillMeta {
  return {
    id: skillId,
    aliases: { claude: alias, codex: alias },
    compatibility,
    source_paths: sourcePaths,
    content_hash: digest
  };
}

export async function chooseSkillId(alias: string, digest: string, options: { preferredSuffix?: string; env?: Env } = {}): Promise<string> {
  const base = stableId(alias, options.preferredSuffix, options.env);
  const existing = await loadSkillMeta(base, options.env);
  if (!existing) {
    return base;
  }
  if (existing.content_hash === digest) {
    return base;
  }
  if (options.preferredSuffix) {
    return stableId(alias, `${options.preferredSuffix}-${digest.slice(0, 8)}`, options.env);
  }
  return stableId(alias, `fork${digest.slice(0, 8)}`, options.env);
}

function optionalOptions<T extends Record<string, unknown>>(values: T): Partial<T> {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined)) as Partial<T>;
}

export async function adoptSkill(
  src: string,
  options: { dryRun?: boolean; suffix?: string; compatibility?: Compatibility; env?: Env } = {}
): Promise<Record<string, unknown>> {
  await ensureStore(options.env);
  const srcPath = resolve(src);
  if (!(await isSkillDir(srcPath))) {
    return { ok: false, error: `not a skill directory with SKILL.md: ${srcPath}` };
  }
  const alias = slugify(srcPath.split(/[\\/]/).at(-1) ?? "unnamed");
  const digest = await contentHash(srcPath);
  const skillId = await chooseSkillId(alias, digest, optionalOptions({ preferredSuffix: options.suffix, env: options.env }));
  const dst = idToDir(skillId, options.env);
  const compatibility = options.compatibility ?? { claude: true, codex: true };
  const nextMeta = meta(skillId, alias, compatibility, [srcPath], digest);
  const result: Record<string, unknown> = {
    ok: true,
    skill_id: skillId,
    alias,
    source: srcPath,
    target: dst,
    content_hash: digest,
    dry_run: options.dryRun ?? false
  };
  if (options.dryRun) {
    result.would_copy = !(await exists(dst));
    return result;
  }
  if (await exists(dst)) {
    const existingHash = (await isSkillDir(dst)) ? await contentHash(dst) : "";
    if (existingHash !== digest) {
      return { ok: false, error: `target exists with different content: ${dst}` };
    }
  } else {
    await copySkillTree(srcPath, dst);
  }
  const existingMeta = (await loadSkillMeta(skillId, options.env)) ?? nextMeta;
  const sourcePaths = Array.from(new Set([...(existingMeta.source_paths ?? []), srcPath])).sort();
  await writeSkillMeta(skillId, { ...existingMeta, ...nextMeta, source_paths: sourcePaths }, options.env);
  return result;
}

export async function importInbox(options: { dryRun?: boolean; env?: Env } = {}): Promise<Record<string, unknown>> {
  const entries = (await scanDir(inboxDir(options.env))).entries;
  const managedHashes = new Set(Object.values(await allSkills(options.env)).map((item) => item.content_hash));
  const candidates = entries.filter(
    (entry) => (entry.type === "skill_dir" || entry.type === "symlink_skill") && !managedHashes.has(entry.content_hash)
  );
  if (options.dryRun ?? true) {
    return { dry_run: true, candidates, message: `${candidates.length} unmanaged inbox skill(s) detected` };
  }
  return { dry_run: false, adopted: await Promise.all(candidates.map((entry) => adoptSkill(entry.path, optionalOptions({ env: options.env })))) };
}

type MigrationEntry = ScanEntry & { client: "claude" | "codex"; alias: string; content_hash: string };

export async function migratePlan(options: { env?: Env } = {}): Promise<{ actions: Array<Record<string, unknown>> }> {
  const locations = {
    claude: adapter("claude", options.env).globalDir(),
    codex: adapter("codex", options.env).globalDir()
  } as const;
  const found: Record<string, MigrationEntry[]> = {};

  for (const [client, path] of Object.entries(locations) as Array<["claude" | "codex", string]>) {
    for (const entry of (await scanDir(path)).entries) {
      if ((entry.type === "skill_dir" || entry.type === "symlink_skill") && entry.content_hash) {
        const alias = slugify(entry.name);
        (found[alias] ??= []).push({ ...entry, client, alias, content_hash: entry.content_hash });
      }
    }
  }

  const actions: Array<Record<string, unknown>> = [];
  for (const [alias, entries] of Object.entries(found).sort(([left], [right]) => left.localeCompare(right))) {
    const hashes = new Set(entries.map((entry) => entry.content_hash));
    if (hashes.size === 1) {
      const digest = [...hashes][0]!;
      const clients = new Set(entries.map((entry) => entry.client));
      actions.push({
        kind: entries.length > 1 ? "merge" : "copy",
        alias,
        skill_id: await chooseSkillId(alias, digest, optionalOptions({ env: options.env })),
        sources: entries.map((entry) => entry.path),
        compatibility: { claude: clients.has("claude"), codex: clients.has("codex") },
        content_hash: digest
      });
    } else {
      for (const entry of entries) {
        actions.push({
          kind: "fork",
          alias,
          skill_id: await chooseSkillId(alias, entry.content_hash, optionalOptions({ preferredSuffix: entry.client, env: options.env })),
          sources: [entry.path],
          compatibility: { claude: entry.client === "claude", codex: entry.client === "codex" },
          content_hash: entry.content_hash,
          client: entry.client
        });
      }
    }
  }
  return { actions };
}

export async function migrateApply(options: { env?: Env } = {}): Promise<Record<string, unknown>> {
  const plan = await migratePlan(options);
  const results = [];
  for (const action of plan.actions) {
    const sources = action.sources as string[];
    const compatibility = action.compatibility as Compatibility;
    const suffix = action.kind === "fork" ? (action.client as string) : undefined;
    const result = await adoptSkill(sources[0]!, {
      compatibility,
      ...(suffix ? { suffix } : {}),
      ...(options.env ? { env: options.env } : {})
    });
    results.push(result);
    const skillId = (result.skill_id ?? action.skill_id) as string;
    const current = await loadSkillMeta(skillId, options.env);
    if (current) {
      await writeSkillMeta(
        skillId,
        { ...current, source_paths: Array.from(new Set([...(current.source_paths ?? []), ...sources])).sort(), compatibility },
        options.env
      );
    }
  }
  return { applied: results, plan };
}
