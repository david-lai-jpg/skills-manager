import { homedir } from "node:os";
import { join, resolve } from "node:path";

export type Env = Record<string, string | undefined>;

export function expandUser(path: string, env: Env = process.env): string {
  if (path === "~") {
    return env.HOME ?? homedir();
  }
  if (path.startsWith("~/")) {
    return join(env.HOME ?? homedir(), path.slice(2));
  }
  return path;
}

export function skillsManagerHome(env: Env = process.env): string {
  return resolve(expandUser(env.SKILLS_MANAGER_HOME ?? env.HOME ?? homedir(), env));
}

export function agentsDir(env: Env = process.env): string {
  return join(skillsManagerHome(env), ".agents");
}

export function inboxDir(env: Env = process.env): string {
  return join(agentsDir(env), "skills");
}

export function storeRoot(env: Env = process.env): string {
  return resolve(expandUser(env.SKILLS_MANAGER_STORE ?? join(agentsDir(env), "skills-store"), env));
}

export function skillsRoot(env: Env = process.env): string {
  return join(storeRoot(env), "skills");
}

export function manifestsRoot(env: Env = process.env): string {
  return join(storeRoot(env), "manifests");
}

export function transactionsRoot(env: Env = process.env): string {
  return join(storeRoot(env), "transactions");
}

export function presetsRoot(env: Env = process.env): string {
  return join(storeRoot(env), "presets");
}

export function logsRoot(env: Env = process.env): string {
  return join(storeRoot(env), "logs");
}
