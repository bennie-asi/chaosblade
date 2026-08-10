import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BladeClient } from "./client.js";

describe("api / BladeClient bearer-token wiring", () => {
  // Guards the remote-TUI contract: when the local config carries a
  // server_token, EVERY request the client makes must present it as an
  // ``Authorization: Bearer`` header — otherwise a token-guarded
  // ``blade-ai server`` 401-blocks the TUI at /health and the user
  // just sees "backend did not pass /health within 10s" with no hint
  // that auth is the cause.

  let dir: string;
  const realFetch = globalThis.fetch;

  /** Minimal Response stand-in for the endpoints under test. */
  const okResponse = (body: unknown = {}) => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => body,
  });

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "blade-client-auth-"));
    process.env["BLADE_AI_CONFIG_DIR"] = dir;
    delete process.env["BLADE_AI_SERVER_TOKEN"];
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    delete process.env["BLADE_AI_CONFIG_DIR"];
    delete process.env["BLADE_AI_SERVER_TOKEN"];
    rmSync(dir, { recursive: true, force: true });
  });

  it("attaches the bearer header when a token is configured", async () => {
    writeFileSync(join(dir, "config.json"), JSON.stringify({ server_token: "s3cret" }));
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) => okResponse() as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await client.health();

    const init = spy.mock.calls[0]![1];
    const headers = init?.headers as Record<string, string>;
    expect(headers["authorization"]).toBe("Bearer s3cret");
  });

  it("sends no authorization header without a token", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) => okResponse() as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await client.health();

    const init = spy.mock.calls[0]![1];
    // With no token _fetch passes the init through untouched — no
    // header object invented.
    expect(init?.headers).toBeUndefined();
  });

  it("merges the bearer header with existing request headers", async () => {
    process.env["BLADE_AI_SERVER_TOKEN"] = "env-token";
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        okResponse({ status: "success", data: {} }) as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await client.setConfig("timeout_blade", "45").catch(() => undefined);

    const init = spy.mock.calls[0]![1];
    const headers = init?.headers as Record<string, string>;
    // The pre-existing content-type must survive the merge.
    expect(headers["content-type"]).toBe("application/json");
    expect(headers["authorization"]).toBe("Bearer env-token");
  });
});
