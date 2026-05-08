import { cp, mkdir, stat } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { actualRendered } from "./materializer.js";
import { adapter } from "./adapters.js";
import { expandUser, inboxDir, logsRoot, manifestsRoot, presetsRoot, skillsRoot, storeRoot, transactionsRoot, type Env } from "./paths.js";
import { VERSION, writeJson } from "./store.js";

export function backupRoot(exportPath: string, env: Env = process.env): string {
  const path = resolve(expandUser(exportPath, env));
  return basename(path) === "agent-skills-backup" ? path : join(path, "agent-skills-backup");
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function renderedList(client: "claude" | "codex", env: Env = process.env): Promise<unknown[]> {
  return Object.values(await actualRendered(adapter(client, env).globalDir(), env));
}

export async function dryRunExport(exportPath = "./agent-skills-backup", env: Env = process.env): Promise<Record<string, unknown>> {
  return {
    target: backupRoot(exportPath, env),
    include: [skillsRoot(env), manifestsRoot(env), transactionsRoot(env), presetsRoot(env), logsRoot(env), inboxDir(env)],
    rendered_metadata_only: {
      claude: await renderedList("claude", env),
      codex: await renderedList("codex", env)
    }
  };
}

export async function copyIfExists(src: string, dst: string): Promise<void> {
  if (!(await exists(src))) {
    return;
  }
  await mkdir(dirname(dst), { recursive: true });
  await cp(src, dst, { recursive: true, force: true });
}

export async function exportBackup(exportPath: string, env: Env = process.env): Promise<Record<string, unknown>> {
  const root = backupRoot(exportPath, env);
  await mkdir(root, { recursive: true });
  await copyIfExists(skillsRoot(env), join(root, "skills-store", "skills"));
  await copyIfExists(manifestsRoot(env), join(root, "skills-store", "manifests"));
  await copyIfExists(transactionsRoot(env), join(root, "skills-store", "transactions"));
  await copyIfExists(presetsRoot(env), join(root, "skills-store", "presets"));
  await copyIfExists(logsRoot(env), join(root, "skills-store", "logs"));
  await copyIfExists(inboxDir(env), join(root, "inbox", "agents-skills"));
  await writeJson(join(root, "rendered", "claude-skills-list.json"), await renderedList("claude", env));
  await writeJson(join(root, "rendered", "codex-skills-list.json"), await renderedList("codex", env));
  const manifest = { version: VERSION, created_at: Date.now() / 1000, kind: "agent-skills-backup" };
  await writeJson(join(root, "manifest.json"), manifest);
  return { ok: true, backup: root, manifest };
}

export async function normalizeBackup(path: string, env: Env = process.env): Promise<string> {
  const root = resolve(expandUser(path, env));
  if (await exists(join(root, "manifest.json"))) {
    return root;
  }
  if (await exists(join(root, "agent-skills-backup", "manifest.json"))) {
    return join(root, "agent-skills-backup");
  }
  return root;
}

export async function restorePlan(path: string, env: Env = process.env): Promise<Record<string, unknown>> {
  const root = await normalizeBackup(path, env);
  return {
    backup: root,
    exists: await exists(join(root, "manifest.json")),
    copies: [
      { from: join(root, "skills-store", "skills"), to: skillsRoot(env) },
      { from: join(root, "skills-store", "manifests"), to: manifestsRoot(env) },
      { from: join(root, "skills-store", "transactions"), to: transactionsRoot(env) },
      { from: join(root, "skills-store", "presets"), to: presetsRoot(env) },
      { from: join(root, "skills-store", "logs"), to: logsRoot(env) },
      { from: join(root, "inbox", "agents-skills"), to: inboxDir(env) }
    ],
    after: ["skills-manager materialize --client all", "skills-manager doctor"]
  };
}

export async function restoreBackup(path: string, options: { dryRun?: boolean; env?: Env } = {}): Promise<Record<string, unknown>> {
  const env = options.env ?? process.env;
  const plan = await restorePlan(path, env);
  if (!plan.exists) {
    return { ok: false, error: `backup manifest not found: ${plan.backup}`, plan };
  }
  if (options.dryRun ?? true) {
    return { ok: true, dry_run: true, plan };
  }
  for (const item of plan.copies as Array<{ from: string; to: string }>) {
    await copyIfExists(item.from, item.to);
  }
  return { ok: true, dry_run: false, plan, message: "restore copied store/inbox data; run materialize next" };
}

export function currentStoreRoot(env: Env = process.env): string {
  return storeRoot(env);
}
