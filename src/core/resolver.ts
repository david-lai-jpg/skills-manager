import { resolve as resolvePath } from "node:path";
import { appendAction } from "./action-log.js";
import { allSkills, loadManifest, saveManifest, type SkillMeta } from "./store.js";
import type { Client } from "./schemas.js";
import type { Env } from "./paths.js";

export const SCOPES = ["global", "project", "session"] as const;
export type ResolverScope = (typeof SCOPES)[number];
export type DesiredSkill = {
  id: string;
  alias: string;
  meta: SkillMeta;
  reasons: string[];
};

export type ResolvedSkillSummary = {
  enabled: boolean;
  alias?: string;
  compatible: boolean;
  reasons: string[];
};

export async function resolveSkillRef(ref: string, env: Env = process.env): Promise<string> {
  if (ref.startsWith("skill.")) {
    return ref;
  }
  const managed = await allSkills(env);
  const matches = Object.entries(managed)
    .filter(([skillId, meta]) => ref === skillId.split(".").at(-1) || Object.values(meta.aliases).includes(ref))
    .map(([skillId]) => skillId);
  return matches.length === 1 ? matches[0]! : ref;
}

function applyList(effective: Set<string>, values: string[], op: "enable" | "disable", reasons: Map<string, string[]>, reason: string): void {
  for (const value of values) {
    if (op === "enable") {
      effective.add(value);
    } else {
      effective.delete(value);
    }
    const existing = reasons.get(value) ?? [];
    existing.push(reason);
    reasons.set(value, existing);
  }
}

async function manifestSequence(options: { project?: string; env?: Env }): Promise<Array<[ResolverScope, Awaited<ReturnType<typeof loadManifest>>]>> {
  const sequence: Array<[ResolverScope, Awaited<ReturnType<typeof loadManifest>>]> = [];
  for (const scope of SCOPES) {
    if (scope === "project" && !options.project) {
      continue;
    }
    sequence.push([scope, await loadManifest(scope, manifestOptions(options))]);
  }
  return sequence;
}

function manifestOptions(options: { project?: string; env?: Env }): { project?: string; env?: Env } {
  return {
    ...(options.project ? { project: options.project } : {}),
    ...(options.env ? { env: options.env } : {})
  };
}

export async function resolveDesired(client: Client, options: { project?: string; env?: Env } = {}): Promise<{
  client: Client;
  desired: Record<string, DesiredSkill>;
  skills: Record<string, ResolvedSkillSummary>;
  unknown_enabled_ids: string[];
}> {
  const managed = await allSkills(options.env);
  const effective = new Set<string>();
  const reasons = new Map<string, string[]>();

  for (const [scope, manifest] of await manifestSequence(options)) {
    applyList(effective, manifest.enable, "enable", reasons, `${scope}:enable`);
    applyList(effective, manifest.clients[client].enable, "enable", reasons, `${scope}:${client}:enable`);
    applyList(effective, manifest.disable, "disable", reasons, `${scope}:disable`);
    applyList(effective, manifest.clients[client].disable, "disable", reasons, `${scope}:${client}:disable`);
  }

  const desired: Record<string, DesiredSkill> = {};
  const unknown_enabled_ids = [...effective].filter((skillId) => !managed[skillId]).sort();
  for (const skillId of [...effective].sort()) {
    const meta = managed[skillId];
    if (!meta) {
      continue;
    }
    if (!meta.compatibility[client]) {
      reasons.set(skillId, [...(reasons.get(skillId) ?? []), `${client}:incompatible`]);
      continue;
    }
    const alias = meta.aliases[client] || skillId.split(".").at(-1) || skillId;
    desired[skillId] = { id: skillId, alias, meta, reasons: reasons.get(skillId) ?? [] };
  }

  const skills: Record<string, ResolvedSkillSummary> = {};
  for (const [skillId, meta] of Object.entries(managed)) {
    const alias = meta.aliases[client];
    skills[skillId] = {
      enabled: skillId in desired,
      ...(alias === undefined ? {} : { alias }),
      compatible: meta.compatibility[client],
      reasons: reasons.get(skillId) ?? []
    };
  }

  return { client, desired, skills, unknown_enabled_ids };
}

export async function setSkill(
  scope: ResolverScope,
  skillId: string,
  enabled: boolean,
  options: { client?: Client | "all"; project?: string; surface?: string; env?: Env } = {}
): Promise<string> {
  const client = options.client ?? "all";
  const manifest = await loadManifest(scope, manifestOptions(options));
  const target = client === "all" ? manifest : manifest.clients[client];
  const addKey = enabled ? "enable" : "disable";
  const removeKey = enabled ? "disable" : "enable";

  if (!target[addKey].includes(skillId)) {
    target[addKey].push(skillId);
  }
  target[removeKey] = target[removeKey].filter((value) => value !== skillId);

  const path = await saveManifest(scope, manifest, manifestOptions(options));
  await appendAction(
    enabled ? "enable" : "disable",
    {
      surface: options.surface ?? "core",
      scope,
      client,
      skill_id: skillId,
      manifest_path: path,
      project_path: options.project ? resolvePath(options.project) : null
    },
    options.env
  );
  return path;
}
