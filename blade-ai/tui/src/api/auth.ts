/**
 * Server-auth token resolution for the TUI's HTTP clients.
 *
 * When a ``blade-ai server`` instance is started with ``server_token``
 * configured, TokenAuthMiddleware rejects every request that lacks an
 * ``Authorization: Bearer <token>`` header. The TUI therefore needs the
 * same credential the Python CLI already sends (``AgentClient
 * ._auth_headers``).
 *
 * Resolution mirrors the Python settings precedence (config.json beats
 * env vars):
 *   1. ``server_token`` from the local ``~/.blade-ai/config.json``
 *      (or ``$BLADE_AI_CONFIG_DIR/config.json`` — mirrors Python's
 *      ``_active_config_file`` override);
 *   2. the ``BLADE_AI_SERVER_TOKEN`` env var.
 *
 * Blank / whitespace-only values are treated as absent. Against the
 * TUI's own embedded server the header is harmless — that entry point
 * bypasses the gate (loopback-only, spawned by the TUI itself) — and
 * against unguarded servers an extra header is ignored.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * Pure precedence rule, separated from IO for unit tests: returns the
 * first non-blank candidate, or undefined when both are absent.
 */
export function pickServerToken(
  configFileValue: string | undefined,
  envValue: string | undefined,
): string | undefined {
  const fromConfig = configFileValue?.trim();
  if (fromConfig) return fromConfig;
  const fromEnv = envValue?.trim();
  if (fromEnv) return fromEnv;
  return undefined;
}

/** Best-effort read of ``server_token`` from the local config file. */
export function readConfigFileToken(): string | undefined {
  try {
    let dir = process.env["BLADE_AI_CONFIG_DIR"]?.trim() || join(homedir(), ".blade-ai");
    // Mirror Python's Path(...).expanduser(): an override may be
    // written as ``~/alt-config``.
    if (dir.startsWith("~")) {
      dir = join(homedir(), dir.slice(1));
    }
    const raw = readFileSync(join(dir, "config.json"), "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const v = parsed["server_token"];
    return typeof v === "string" ? v : undefined;
  } catch {
    // Missing file, corrupt JSON, unreadable — all fine: fall back to
    // the env var (or no token at all).
    return undefined;
  }
}

/** Resolve the bearer token to present to the server, if any. */
export function resolveServerToken(): string | undefined {
  return pickServerToken(
    readConfigFileToken(),
    process.env["BLADE_AI_SERVER_TOKEN"],
  );
}
