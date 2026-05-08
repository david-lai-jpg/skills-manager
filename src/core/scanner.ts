import { lstat, realpath, readdir, readlink, stat } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { adapter } from "./adapters.js";
import { contentHash, isSkillDir } from "./store.js";
import { inboxDir, skillsRoot, type Env } from "./paths.js";

export type ScanEntry = {
  path: string;
  name: string;
  type: "broken_symlink" | "symlink_skill" | "symlink_non_skill" | "skill_dir" | "missing_skill_md" | "file" | "error";
  link_target?: string;
  resolved?: string;
  content_hash?: string;
  error?: string;
};

export type ScanDirResult = {
  path: string;
  exists: boolean;
  entries: ScanEntry[];
  error?: string;
};

export async function classifyEntry(path: string): Promise<ScanEntry> {
  const item: ScanEntry = { path, name: basename(path), type: "file" };
  try {
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      const target = await readlink(path);
      item.link_target = target;
      try {
        await stat(path);
      } catch {
        item.type = "broken_symlink";
        return item;
      }
      item.resolved = await realpath(path);
      item.type = (await isSkillDir(path)) ? "symlink_skill" : "symlink_non_skill";
    } else if (info.isDirectory()) {
      item.type = (await isSkillDir(path)) ? "skill_dir" : "missing_skill_md";
    } else {
      item.type = "file";
    }
    if (item.type === "skill_dir" || item.type === "symlink_skill") {
      item.content_hash = await contentHash(path);
    }
  } catch (error) {
    item.type = "error";
    item.error = error instanceof Error ? error.message : String(error);
  }
  return item;
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

export async function scanDir(path: string): Promise<ScanDirResult> {
  const result: ScanDirResult = { path, exists: await exists(path), entries: [] };
  if (!result.exists) {
    return result;
  }
  const info = await stat(path);
  if (!info.isDirectory()) {
    result.error = "not a directory";
    return result;
  }
  const entries = (await readdir(path)).sort();
  result.entries = await Promise.all(entries.map((entry) => classifyEntry(join(path, entry))));
  return result;
}

export type ScanResult = {
  locations: Record<string, ScanDirResult>;
  duplicates: {
    names: Record<string, string[]>;
    content_hashes: Record<string, string[]>;
  };
};

export async function scan(options: { project?: string; env?: Env } = {}): Promise<ScanResult> {
  const env = options.env ?? process.env;
  const locations: Record<string, string> = {
    inbox: inboxDir(env),
    store: skillsRoot(env),
    claude_global: adapter("claude", env).globalDir(),
    codex_global: adapter("codex", env).globalDir()
  };
  if (options.project) {
    locations.claude_project = adapter("claude", env).projectDir(options.project);
    locations.codex_project = adapter("codex", env).projectDir(options.project);
  }

  const scannedEntries = await Promise.all(Object.entries(locations).map(async ([name, path]) => [name, await scanDir(path)] as const));
  const scanned = Object.fromEntries(scannedEntries);
  const names: Record<string, string[]> = {};
  const hashes: Record<string, string[]> = {};

  for (const [locationName, location] of Object.entries(scanned)) {
    for (const entry of location.entries) {
      if (entry.type === "skill_dir" || entry.type === "symlink_skill") {
        (names[entry.name] ??= []).push(`${locationName}:${entry.path}`);
        if (entry.content_hash) {
          (hashes[entry.content_hash] ??= []).push(`${locationName}:${entry.path}`);
        }
      }
    }
  }

  return {
    locations: scanned,
    duplicates: {
      names: Object.fromEntries(Object.entries(names).filter(([, value]) => value.length > 1)),
      content_hashes: Object.fromEntries(Object.entries(hashes).filter(([, value]) => value.length > 1))
    }
  };
}
