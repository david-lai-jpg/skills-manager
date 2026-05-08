import { appendFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { logsRoot, type Env } from "./paths.js";
import { stableJson } from "./store.js";

export function actionsPath(env: Env = process.env): string {
  return join(logsRoot(env), "actions.jsonl");
}

export async function appendAction(action: string, fields: Record<string, unknown> = {}, env: Env = process.env): Promise<Record<string, unknown>> {
  const entry = { time: Date.now() / 1000, action, ...fields };
  const path = actionsPath(env);
  await mkdir(dirname(path), { recursive: true });
  await appendFile(path, stableJson(entry).replace(/\n$/, "") + "\n", "utf8");
  return entry;
}

