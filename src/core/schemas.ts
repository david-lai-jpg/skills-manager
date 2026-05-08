import { z } from "zod";

export const clientSchema = z.enum(["claude", "codex"]);
export const clientModeSchema = z.enum(["all", "claude", "codex"]);
export const scopeSchema = z.enum(["global", "project", "session"]);

export const manifestSchema = z.object({
  version: z.number().int(),
  inherit: z.boolean(),
  enable: z.array(z.string()),
  disable: z.array(z.string()),
  clients: z.object({
    claude: z.object({
      enable: z.array(z.string()),
      disable: z.array(z.string())
    }),
    codex: z.object({
      enable: z.array(z.string()),
      disable: z.array(z.string())
    })
  })
});

export const presetEntrySchema = z.union([
  z.string(),
  z.object({
    id: z.string(),
    alias: z.string().optional()
  })
]);

export const presetBucketSchema = z.object({
  enable: z.array(presetEntrySchema).default([]),
  disable: z.array(presetEntrySchema).default([])
});

export const presetSchema = z.object({
  version: z.number().int(),
  name: z.string(),
  description: z.string().optional(),
  tags: z.array(z.string()).optional(),
  enable: z.array(presetEntrySchema).default([]),
  disable: z.array(presetEntrySchema).default([]),
  clients: z
    .object({
      claude: presetBucketSchema.default({ enable: [], disable: [] }),
      codex: presetBucketSchema.default({ enable: [], disable: [] })
    })
    .default({
      claude: { enable: [], disable: [] },
      codex: { enable: [], disable: [] }
    })
});

export type Client = z.infer<typeof clientSchema>;
export type ClientMode = z.infer<typeof clientModeSchema>;
export type Scope = z.infer<typeof scopeSchema>;
export type Manifest = z.infer<typeof manifestSchema>;
export type Preset = z.infer<typeof presetSchema>;
