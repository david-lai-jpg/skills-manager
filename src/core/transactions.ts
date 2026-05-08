import { randomUUID } from "node:crypto";
import { lstat, mkdir, readlink, rm, symlink } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { skillsRoot, transactionsRoot, type Env } from "./paths.js";
import { pathUnder, readJson, writeJson } from "./store.js";

export type TransactionAction = {
  op: "create_symlink" | "create_copy" | "remove_rendered";
  client: string;
  target: string;
  source: string;
  skill_id: string;
  alias?: string;
};

export type Transaction = {
  id: string;
  kind: string;
  status: "planned" | "committed" | "failed" | "rolled_back";
  created_at: number;
  updated_at?: number;
  rolled_back_at?: number;
  error?: string;
  actions: TransactionAction[];
};

export function transactionPath(txId: string, env: Env = process.env): string {
  return join(transactionsRoot(env), `${txId}.json`);
}

function transactionId(): string {
  const now = new Date();
  const stamp =
    `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}` +
    `-${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}${String(now.getSeconds()).padStart(2, "0")}`;
  return `${stamp}-${randomUUID().slice(0, 8)}`;
}

export async function newTransaction(kind: string, actions: TransactionAction[], env: Env = process.env): Promise<Transaction> {
  await mkdir(transactionsRoot(env), { recursive: true });
  const tx: Transaction = { id: transactionId(), kind, status: "planned", created_at: Date.now() / 1000, actions };
  await writeJson(transactionPath(tx.id, env), tx);
  return tx;
}

export async function markTransaction(tx: Transaction, status: Transaction["status"], env: Env = process.env, error?: string): Promise<void> {
  tx.status = status;
  tx.updated_at = Date.now() / 1000;
  if (error) {
    tx.error = error;
  }
  await writeJson(transactionPath(tx.id, env), tx);
}

async function resolvedLinkTarget(path: string): Promise<string | null> {
  try {
    const target = await readlink(path);
    return resolve(dirname(path), target);
  } catch {
    return null;
  }
}

export async function isManagerCreated(path: string, env: Env = process.env): Promise<boolean> {
  try {
    const info = await lstat(path);
    if (info.isSymbolicLink()) {
      const target = await resolvedLinkTarget(path);
      return target ? pathUnder(target, skillsRoot(env)) : false;
    }
    if (info.isDirectory()) {
      const data = await readJson<Record<string, unknown>>(join(path, ".skills-manager.json"), {});
      return data.manager === "skills-manager";
    }
    return false;
  } catch {
    return false;
  }
}

export async function removeManagerCreated(path: string, env: Env = process.env): Promise<void> {
  let info;
  try {
    info = await lstat(path);
  } catch {
    return;
  }
  if (!(await isManagerCreated(path, env))) {
    throw new Error(`refusing to remove unmanaged path: ${path}`);
  }
  if (info.isDirectory() && !info.isSymbolicLink()) {
    await rm(path, { recursive: true });
    return;
  }
  await rm(path);
}

export async function restoreLink(action: TransactionAction): Promise<void> {
  try {
    await lstat(action.target);
    return;
  } catch {
    // Target missing is the only case rollback should recreate.
  }
  await mkdir(dirname(action.target), { recursive: true });
  await symlink(action.source, action.target, "dir");
}

export async function rollback(txId: string, env: Env = process.env): Promise<Record<string, unknown>> {
  const path = transactionPath(txId, env);
  const tx = await readJson<Transaction | null>(path, null);
  if (!tx) {
    return { ok: false, error: `transaction not found: ${txId}` };
  }
  const results: Array<Record<string, unknown>> = [];
  for (const action of [...tx.actions].reverse()) {
    try {
      if (action.op === "create_symlink" || action.op === "create_copy") {
        await removeManagerCreated(action.target, env);
        results.push({ op: "removed", target: action.target });
      } else if (action.op === "remove_rendered") {
        await restoreLink(action);
        results.push({ op: "restored", target: action.target });
      }
    } catch (error) {
      results.push({ op: "error", target: action.target, error: error instanceof Error ? error.message : String(error) });
    }
  }
  tx.status = "rolled_back";
  tx.rolled_back_at = Date.now() / 1000;
  await writeJson(path, tx);
  return { ok: true, transaction: txId, results };
}
