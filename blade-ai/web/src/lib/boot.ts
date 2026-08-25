/**
 * Boot sequence for the Web UI: reachability probe → session create.
 *
 * Much simpler than the TUI's BootRunner — no server spawn (``blade-ai
 * web`` already started it), no wizard gate (P1 assumes a configured
 * backend; a misconfigured one surfaces per-turn errors in the stream),
 * no recording resume. Same-origin client: the bundle is served by the
 * backend itself, so ``baseUrl`` is the empty string and every request
 * is relative (vite dev proxies ``/api`` to the backend).
 */
import { BladeClient } from "@blade-ai/core";

export interface BootResult {
  client: BladeClient;
  sessionId: string;
}

const HEALTH_TIMEOUT_MS = 10_000;
const HEALTH_POLL_MS = 300;

export async function bootSession(
  signal?: AbortSignal,
): Promise<BootResult> {
  const client = new BladeClient("");

  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  let healthy = false;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    healthy = await client.health();
    if (healthy) break;
    await new Promise((r) => setTimeout(r, HEALTH_POLL_MS));
  }
  if (!healthy) {
    throw new Error(
      `backend did not pass /api/v1/health within ${HEALTH_TIMEOUT_MS / 1000}s`,
    );
  }

  const sessionId = await client.createSession();
  return { client, sessionId };
}
