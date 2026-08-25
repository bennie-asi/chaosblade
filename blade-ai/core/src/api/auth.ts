/**
 * Server-auth token precedence — the pure rule, shared by frontends.
 *
 * When a ``blade-ai server`` instance is started with ``server_token``
 * configured, TokenAuthMiddleware rejects every request that lacks an
 * ``Authorization: Bearer <token>`` header.
 *
 * This module holds ONLY the pure precedence rule. The Node-specific
 * resolution (reading ``~/.blade-ai/config.json`` from disk, env vars)
 * lives in the TUI host (``tui/src/api/auth.ts``); the web host supplies
 * its own token source (settings UI / storage). Both feed the result
 * into ``BladeClient`` via ``ClientOptions.getAuthToken``.
 *
 * Precedence mirrors the Python settings (config.json beats env vars):
 *   1. ``server_token`` from the local ``~/.blade-ai/config.json``
 *   2. the ``BLADE_AI_SERVER_TOKEN`` env var
 */

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
