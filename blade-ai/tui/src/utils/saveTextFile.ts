/**
 * TUI host implementation of ``SlashCommandContext.saveTextFile``
 * (used by ``/recordings export``).
 *
 * The core registry stays browser-clean precisely by delegating this
 * write; here we do the Node things it can't: ``~`` expansion,
 * parent-dir creation, and the no-overwrite guarantee.
 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

export async function saveTextFile(
  outPath: string,
  content: string,
): Promise<
  | { saved: true; absPath: string; bytes: number }
  | { saved: false; alreadyExists: true; absPath: string }
> {
  const expanded = outPath.startsWith("~")
    ? `${process.env.HOME ?? ""}${outPath.slice(1)}`
    : outPath;
  const abs = resolve(expanded);
  if (existsSync(abs)) {
    return { saved: false, alreadyExists: true, absPath: abs };
  }
  const parent = dirname(abs);
  if (!existsSync(parent)) {
    mkdirSync(parent, { recursive: true });
  }
  writeFileSync(abs, content, "utf-8");
  return { saved: true, absPath: abs, bytes: Buffer.byteLength(content, "utf-8") };
}
