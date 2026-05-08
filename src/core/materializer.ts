import { cp, lstat, mkdir, readdir, readlink, symlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { appendAction } from "./action-log.js";
import { adapter, type ClientName } from "./adapters.js";
import { resolveDesired } from "./resolver.js";
import { skillsRoot } from "./paths.js";
import { idToDir, markerData, pathUnder, readJson, writeJson } from "./store.js";
import { markTransaction, newTransaction, removeManagerCreated, type TransactionAction } from "./transactions.js";
import type { Env } from "./paths.js";

export type RenderedEntry = {
  path: string;
  managed_skill_id: string | null;
  type: "symlink" | "dir" | "file";
};

async function existsByLstat(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch {
    return false;
  }
}

async function resolvedLinkTarget(path: string): Promise<string | null> {
  try {
    const target = await readlink(path);
    return resolve(dirname(path), target);
  } catch {
    return null;
  }
}

export async function managedRenderedId(path: string, env: Env = process.env): Promise<string | null> {
  try {
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      const target = await resolvedLinkTarget(path);
      if (target && (await pathUnder(target, skillsRoot(env)))) {
        return basename(target);
      }
      return null;
    }
    if (info.isDirectory()) {
      const data = await readJson<Record<string, unknown>>(join(path, ".skills-manager.json"), {});
      return data.manager === "skills-manager" && typeof data.skill_id === "string" ? data.skill_id : null;
    }
  } catch {
    return null;
  }
  return null;
}

export async function actualRendered(renderedDir: string, env: Env = process.env): Promise<Record<string, RenderedEntry>> {
  const out: Record<string, RenderedEntry> = {};
  if (!(await existsByLstat(renderedDir))) {
    return out;
  }
  for (const name of (await readdir(renderedDir)).sort()) {
    const path = join(renderedDir, name);
    const info = await lstat(path);
    out[name] = {
      path,
      managed_skill_id: await managedRenderedId(path, env),
      type: info.isSymbolicLink() ? "symlink" : info.isDirectory() ? "dir" : "file"
    };
  }
  return out;
}

export async function diff(client: ClientName, options: { project?: string; env?: Env } = {}): Promise<Record<string, unknown>> {
  const env = options.env ?? process.env;
  const renderedDir = adapter(client, env).renderedDir(options.project);
  const resolved = await resolveDesired(client, options);
  const actual = await actualRendered(renderedDir, env);
  const desiredAliases = Object.fromEntries(Object.entries(resolved.desired).map(([skillId, item]) => [item.alias, skillId]));
  const creates: Array<Record<string, unknown>> = [];
  const removes: Array<Record<string, unknown>> = [];
  const conflicts: Array<Record<string, unknown>> = [];

  for (const [alias, skillId] of Object.entries(desiredAliases)) {
    const current = actual[alias];
    if (!current) {
      creates.push({ alias, skill_id: skillId });
    } else if (current.managed_skill_id !== skillId) {
      conflicts.push({
        alias,
        path: current.path,
        actual_managed_skill_id: current.managed_skill_id,
        desired_skill_id: skillId
      });
    }
  }

  for (const [alias, info] of Object.entries(actual)) {
    if (info.managed_skill_id && !(alias in desiredAliases)) {
      removes.push({ alias, skill_id: info.managed_skill_id, path: info.path });
    }
  }

  return { client, rendered_dir: renderedDir, creates, removes, conflicts, desired: desiredAliases, actual };
}

export async function planActions(client: ClientName, options: { project?: string; env?: Env } = {}): Promise<[Record<string, unknown>, TransactionAction[]]> {
  const d = await diff(client, options);
  const actions: TransactionAction[] = [];
  for (const item of d.removes as Array<{ path: string; skill_id: string }>) {
    actions.push({
      op: "remove_rendered",
      client,
      target: item.path,
      source: idToDir(item.skill_id, options.env),
      skill_id: item.skill_id
    });
  }
  for (const item of d.creates as Array<{ alias: string; skill_id: string }>) {
    const source = idToDir(item.skill_id, options.env);
    actions.push({
      op: "create_symlink",
      client,
      target: join(d.rendered_dir as string, item.alias),
      source,
      skill_id: item.skill_id,
      alias: item.alias
    });
  }
  return [d, actions];
}

export async function applyAction(action: TransactionAction, env: Env = process.env): Promise<void> {
  if (action.op === "remove_rendered") {
    await removeManagerCreated(action.target, env);
    return;
  }
  if (action.op === "create_symlink") {
    if (await existsByLstat(action.target)) {
      throw new Error(`target already exists: ${action.target}`);
    }
    await mkdir(dirname(action.target), { recursive: true });
    try {
      await symlink(action.source, action.target, "dir");
    } catch {
      await cp(action.source, action.target, { recursive: true });
      await writeJson(join(action.target, ".skills-manager.json"), markerData(action.skill_id));
      action.op = "create_copy";
    }
  }
}

export async function materialize(
  client: ClientName,
  options: { project?: string; dryRun?: boolean; surface?: string; env?: Env } = {}
): Promise<Record<string, unknown>> {
  const [d, actions] = await planActions(client, options);
  if ((d.conflicts as unknown[]).length > 0) {
    return { ok: false, client, dry_run: options.dryRun ?? false, diff: d, error: "unmanaged or mismatched rendered conflicts" };
  }
  if (options.dryRun) {
    return { ok: true, client, dry_run: true, diff: d, actions };
  }
  const tx = await newTransaction("materialize", actions, options.env);
  try {
    for (const action of actions) {
      await applyAction(action, options.env);
    }
    await markTransaction(tx, "committed", options.env);
    await appendAction(
      "materialize",
      {
        surface: options.surface ?? "core",
        client,
        project_path: options.project ? resolve(options.project) : null,
        transaction_id: tx.id,
        rendered_dir: d.rendered_dir
      },
      options.env
    );
    return { ok: true, client, dry_run: false, transaction_id: tx.id, actions };
  } catch (error) {
    await markTransaction(tx, "failed", options.env, error instanceof Error ? error.message : String(error));
    return { ok: false, client, transaction_id: tx.id, error: error instanceof Error ? error.message : String(error), actions };
  }
}
