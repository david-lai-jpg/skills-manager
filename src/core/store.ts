import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { access, cp, lstat, mkdir, open, readFile, readdir, readlink, realpath, rename, rm, stat } from "node:fs/promises";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";
import { manifestsRoot, skillsRoot, transactionsRoot, presetsRoot, logsRoot, type Env } from "./paths.js";
import type { Manifest, Scope } from "./schemas.js";

export const VERSION = 1;
const FALLBACK_OWNER_PREFIX = "skill.local";
export const ALLOWED_MANAGED_DOT_ROOTS = new Set([".agents", ".claude", ".codex"]);
export const SKIP_DIRS = new Set([".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules", ".venv", "venv"]);
export const LOCAL_JUNK_FILES = new Set([".DS_Store", ".gitignore", ".gitattributes", ".gitmodules"]);
export const SKIP_FILES = new Set([...LOCAL_JUNK_FILES, "skill.json", ".skills-manager.json"]);

export function shouldSkipLocalJunkName(name: string, options: { allowManagedDotRoot?: boolean } = {}): boolean {
  if (options.allowManagedDotRoot && ALLOWED_MANAGED_DOT_ROOTS.has(name)) {
    return false;
  }
  return name.startsWith(".") || SKIP_DIRS.has(name);
}

export function shouldSkipLocalJunkPath(path: string, options: { root?: string; allowManagedDotRoot?: boolean } = {}): boolean {
  if (options.root && resolve(path) === resolve(options.root)) {
    return false;
  }
  return shouldSkipLocalJunkName(basename(path), { allowManagedDotRoot: options.allowManagedDotRoot ?? false });
}

function loadEnvFiles(): void {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
  loadDotenv({ path: resolve(process.cwd(), ".env"), quiet: true });
  loadDotenv({ path: join(root, ".env"), quiet: true });
}

loadEnvFiles();

export function ownerPrefix(env: Env = process.env): string {
  return String(env.OWNER_PREFIX ?? process.env.OWNER_PREFIX ?? FALLBACK_OWNER_PREFIX)
    .split(".")
    .map(slugify)
    .filter(Boolean)
    .join(".");
}

export const OWNER_PREFIX = ownerPrefix();

export function slugify(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug || "unnamed";
}

export function stableId(slug: string, suffix?: string, env: Env = process.env): string {
  const parts = [ownerPrefix(env), slugify(slug)];
  if (suffix) {
    parts.push(slugify(suffix));
  }
  return parts.join(".");
}

export function idToDir(skillId: string, env: Env = process.env): string {
  return join(skillsRoot(env), skillId);
}

export async function ensureStore(env: Env = process.env): Promise<void> {
  for (const path of [skillsRoot(env), manifestsRoot(env), transactionsRoot(env), presetsRoot(env), logsRoot(env)]) {
    await mkdir(path, { recursive: true });
  }
}

export async function exists(path: string): Promise<boolean> {
  try {
    await access(path, fsConstants.F_OK);
    return true;
  } catch {
    return false;
  }
}

export async function isSkillDir(path: string): Promise<boolean> {
  try {
    const info = await stat(path);
    if (!info.isDirectory()) {
      return false;
    }
    const skillInfo = await stat(join(path, "SKILL.md"));
    return skillInfo.isFile();
  } catch {
    return false;
  }
}

async function collectHashFiles(root: string, current: string, result: string[]): Promise<void> {
  const entries = await readdir(current, { withFileTypes: true });
  const dirs = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .filter((name) => !shouldSkipLocalJunkName(name))
    .sort();
  for (const dir of dirs) {
    await collectHashFiles(root, join(current, dir), result);
  }

  const files = entries
    .filter((entry) => entry.isFile() || entry.isSymbolicLink())
    .map((entry) => entry.name)
    .filter((name) => !SKIP_FILES.has(name) && !shouldSkipLocalJunkName(name))
    .sort();
  for (const file of files) {
    const path = join(current, file);
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      continue;
    }
    if (info.isFile()) {
      result.push(path);
    }
  }
}

export async function iterHashFiles(root: string): Promise<string[]> {
  const resolvedRoot = await realpath(root);
  const result: string[] = [];
  await collectHashFiles(resolvedRoot, resolvedRoot, result);
  return result;
}

function toPosix(path: string): string {
  return path.split(sep).join("/");
}

export async function contentHash(root: string): Promise<string> {
  const hash = createHash("sha256");
  const resolvedRoot = await realpath(root);
  for (const path of await iterHashFiles(resolvedRoot)) {
    const rel = toPosix(relative(resolvedRoot, path));
    hash.update(Buffer.from(`${rel}\0`, "utf8"));
    hash.update(await readFile(path));
    hash.update(Buffer.from("\0", "utf8"));
  }
  return hash.digest("hex");
}

export async function copySkillTree(src: string, dst: string): Promise<void> {
  await cp(src, dst, {
    recursive: true,
    filter: async (source) => {
      if (shouldSkipLocalJunkPath(source, { root: src })) {
        return false;
      }
      return true;
    }
  });
}

export async function readJson<T>(path: string, defaultValue: T): Promise<T> {
  if (!(await exists(path))) {
    return defaultValue;
  }
  return JSON.parse(await readFile(path, "utf8")) as T;
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (value && typeof value === "object" && value.constructor === Object) {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      sorted[key] = sortJson((value as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return value;
}

export function stableJson(data: unknown): string {
  return `${JSON.stringify(sortJson(data), null, 2)}\n`;
}

export async function writeJson(path: string, data: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const tmpPath = join(dirname(path), `${basename(path)}.${process.pid}.${randomUUID()}`);
  const handle = await open(tmpPath, "w");
  try {
    await handle.writeFile(stableJson(data), "utf8");
    await handle.close();
    await rename(tmpPath, path);
  } catch (error) {
    await handle.close().catch(() => undefined);
    await rm(tmpPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

export function manifestTemplate(extra: Partial<Manifest> = {}): Manifest {
  return {
    version: VERSION,
    inherit: true,
    enable: [],
    disable: [],
    clients: {
      claude: { enable: [], disable: [] },
      codex: { enable: [], disable: [] }
    },
    ...extra
  };
}

export async function projectKey(project: string): Promise<string> {
  const resolved = resolve(project);
  return createHash("sha256").update(resolved, "utf8").digest("hex").slice(0, 16);
}

export async function manifestPath(scope: Scope, options: { project?: string; session?: string; env?: Env } = {}): Promise<string> {
  const root = manifestsRoot(options.env ?? process.env);
  if (scope === "global") {
    return join(root, "global.json");
  }
  if (scope === "project") {
    const project = resolve(options.project ?? process.cwd());
    return join(root, "projects", `${await projectKey(project)}.json`);
  }
  if (scope === "session") {
    const name = slugify(options.session ?? options.env?.SKILLS_MANAGER_SESSION ?? process.env.SKILLS_MANAGER_SESSION ?? "default");
    return join(root, "sessions", `${name}.json`);
  }
  throw new Error(`unknown scope: ${scope satisfies never}`);
}

function coerceStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export async function loadManifest(
  scope: Scope,
  options: { project?: string; session?: string; env?: Env } = {}
): Promise<Manifest & { project_path?: string }> {
  const path = await manifestPath(scope, options);
  const data = await readJson<Record<string, unknown>>(path, manifestTemplate());
  const base = manifestTemplate();

  for (const [key, value] of Object.entries(data)) {
    if (key !== "clients") {
      (base as unknown as Record<string, unknown>)[key] = value;
    }
  }

  const clients = (data.clients && typeof data.clients === "object" ? data.clients : {}) as Record<string, unknown>;
  for (const client of ["claude", "codex"] as const) {
    const clientData = (clients[client] && typeof clients[client] === "object" ? clients[client] : {}) as Record<string, unknown>;
    base.clients[client].enable = coerceStringList(clientData.enable);
    base.clients[client].disable = coerceStringList(clientData.disable);
  }

  if (scope === "project") {
    const project = resolve(options.project ?? process.cwd());
    return { ...base, project_path: (data.project_path as string | undefined) ?? project };
  }
  return base;
}

export async function saveManifest(
  scope: Scope,
  manifest: Manifest,
  options: { project?: string; session?: string; env?: Env } = {}
): Promise<string> {
  const path = await manifestPath(scope, options);
  await writeJson(path, manifest);
  return path;
}

export type SkillMeta = {
  id: string;
  aliases: { claude: string; codex: string };
  compatibility: { claude: boolean; codex: boolean };
  source_paths?: string[];
  sources?: string[];
  content_hash?: string;
  [key: string]: unknown;
};

export async function loadSkillMeta(skillId: string, env: Env = process.env): Promise<SkillMeta | null> {
  const path = join(idToDir(skillId, env), "skill.json");
  if (!(await exists(path))) {
    return null;
  }
  return readJson<SkillMeta>(path, {} as SkillMeta);
}

export async function writeSkillMeta(skillId: string, meta: SkillMeta, env: Env = process.env): Promise<void> {
  await writeJson(join(idToDir(skillId, env), "skill.json"), meta);
}

export async function allSkills(env: Env = process.env): Promise<Record<string, SkillMeta>> {
  const result: Record<string, SkillMeta> = {};
  const root = skillsRoot(env);
  if (!(await exists(root))) {
    return result;
  }

  const children: string[] = [];
  const entries = (await readdir(root, { withFileTypes: true })).sort((left, right) => left.name.localeCompare(right.name));
  for (const entry of entries) {
    if (shouldSkipLocalJunkName(entry.name)) {
      continue;
    }
    const childPath = join(root, entry.name);
    if (entry.isDirectory() || (entry.isSymbolicLink() && (await isSkillDir(childPath)))) {
      children.push(entry.name);
    }
  }

  for (const child of children) {
    const childPath = join(root, child);
    const metaPath = join(childPath, "skill.json");
    let meta = (await exists(metaPath)) ? await readJson<SkillMeta>(metaPath, {} as SkillMeta) : ({} as SkillMeta);
    if (!meta || Object.keys(meta).length === 0) {
      meta = {
        id: child,
        aliases: { claude: child, codex: child },
        compatibility: { claude: true, codex: true },
        source_paths: [],
        content_hash: (await isSkillDir(childPath)) ? await contentHash(childPath) : ""
      };
    }
    result[meta.id ?? child] = meta;
  }
  return result;
}

export function markerData(skillId: string): { manager: "skills-manager"; version: number; skill_id: string } {
  return { manager: "skills-manager", version: VERSION, skill_id: skillId };
}

export async function pathUnder(path: string, parent: string): Promise<boolean> {
  try {
    const resolvedParent = await resolveExistingPrefix(parent);
    const resolvedPath = await resolveExistingPrefix(path);
    return resolvedPath === resolvedParent || resolvedPath.startsWith(`${resolvedParent}${sep}`);
  } catch {
    return false;
  }
}

async function resolveExistingPrefix(path: string): Promise<string> {
  try {
    return await realpath(path);
  } catch {
    const parent = dirname(path);
    if (parent === path) {
      return resolve(path);
    }
    return join(await resolveExistingPrefix(parent), basename(path));
  }
}

export async function symlinkTarget(path: string): Promise<string | null> {
  try {
    return await readlink(path);
  } catch {
    return null;
  }
}

export function currentFilePath(metaUrl: string): string {
  return fileURLToPath(metaUrl);
}
