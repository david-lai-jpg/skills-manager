import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

export async function makeFixtureSkill(path: string, body = "# Skill\n"): Promise<void> {
  await mkdir(path, { recursive: true });
  await writeFile(join(path, "SKILL.md"), body);
}

