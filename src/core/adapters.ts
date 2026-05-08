import { join, resolve } from "node:path";
import { expandUser, skillsManagerHome, type Env } from "./paths.js";

export const CLIENTS = ["claude", "codex"] as const;
export type ClientName = (typeof CLIENTS)[number];
export type ClientMode = ClientName | "all";

export class ClientAdapter {
  readonly client: ClientName;
  readonly env: Env;

  constructor(client: ClientName, env: Env = process.env) {
    this.client = client;
    this.env = env;
  }

  globalDir(): string {
    if (this.client === "claude") {
      return join(skillsManagerHome(this.env), ".claude", "skills");
    }
    const base = this.env.CODEX_HOME ? resolve(expandUser(this.env.CODEX_HOME, this.env)) : join(skillsManagerHome(this.env), ".codex");
    return join(base, "skills");
  }

  projectDir(project: string): string {
    const base = resolve(expandUser(project, this.env));
    return join(base, `.${this.client}`, "skills");
  }

  renderedDir(project?: string): string {
    return project ? this.projectDir(project) : this.globalDir();
  }
}

export function expandClients(client: ClientMode): ClientName[] {
  if (client === "all") {
    return [...CLIENTS];
  }
  if (!CLIENTS.includes(client)) {
    throw new Error(`unknown client: ${client}`);
  }
  return [client];
}

export function adapter(client: ClientName, env: Env = process.env): ClientAdapter {
  return new ClientAdapter(client, env);
}

