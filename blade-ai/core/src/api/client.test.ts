import { afterEach, describe, expect, it, vi } from "vitest";
import { BladeClient } from "./client.js";

describe("api / BladeClient bearer-token wiring", () => {
  // Guards the token-gate contract: when the host supplies a token via
  // ``ClientOptions.getAuthToken``, EVERY request the client makes must
  // present it as an ``Authorization: Bearer`` header — otherwise a
  // token-guarded ``blade-ai server`` 401-blocks the frontend at
  // /health and the user just sees "backend did not pass /health
  // within 10s" with no hint that auth is the cause.
  //
  // Token RESOLUTION (config.json + env) is the host's job — the TUI
  // implements it in ``tui/src/api/auth.ts`` and tests it there.

  const realFetch = globalThis.fetch;

  /** Minimal Response stand-in for the endpoints under test. */
  const okResponse = (body: unknown = {}) => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => body,
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("attaches the bearer header when the host supplies a token", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) => okResponse() as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999", {
      getAuthToken: () => "s3cret",
    });
    await client.health();

    const init = spy.mock.calls[0]![1];
    const headers = init?.headers as Record<string, string>;
    expect(headers["authorization"]).toBe("Bearer s3cret");
  });

  it("sends no authorization header when no provider is given", async () => {
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

  it("treats a blank provider result as no token", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) => okResponse() as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999", {
      getAuthToken: () => undefined,
    });
    await client.health();

    const init = spy.mock.calls[0]![1];
    expect(init?.headers).toBeUndefined();
  });

  it("deleteSession passes keepalive through only when asked", async () => {
    // The web host calls deleteSession from a pagehide listener — the
    // keepalive flag lets the DELETE outlive the unloading document.
    // The default path (TUI exit) must stay keepalive-free: Node's
    // undici rejects keepalive on some versions, and the TUI never
    // needs it.
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        okResponse() as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await client.deleteSession("s1");
    await client.deleteSession("s2", { keepalive: true });

    expect(spy.mock.calls[0]![1]?.keepalive).toBeUndefined();
    expect(spy.mock.calls[1]![1]?.keepalive).toBe(true);
    // Both calls keep the 3s hard-timeout guard.
    expect(spy.mock.calls[0]![1]?.signal).toBeDefined();
    expect(spy.mock.calls[1]![1]?.signal).toBeDefined();
  });

  it("merges the bearer header with existing request headers", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        okResponse({ status: "success", data: {} }) as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999", {
      getAuthToken: () => "env-token",
    });
    await client.setConfig("timeout_blade", "45").catch(() => undefined);

    const init = spy.mock.calls[0]![1];
    const headers = init?.headers as Record<string, string>;
    // The pre-existing content-type must survive the merge.
    expect(headers["content-type"]).toBe("application/json");
    expect(headers["authorization"]).toBe("Bearer env-token");
  });
});

describe("api / BladeClient.getTaskMetric envelope contract", () => {
  // The single-task metric endpoint answers HTTP 200 for BOTH found
  // and not-found — the not-found case is a ``status: "fail"``
  // envelope (ResponseCode.TASK_NOT_FOUND). These tests pin that the
  // client surfaces the envelope failure as a thrown error carrying
  // the server's message, and unwraps ``data`` on success.

  const realFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("unwraps the success envelope's data payload", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        ({
          ok: true,
          status: 200,
          json: async () => ({
            status: "success",
            data: { task_id: "inject-abc", phase: "done" },
          }),
        }) as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    const data = await client.getTaskMetric("inject-abc");
    expect(data["task_id"]).toBe("inject-abc");
    expect(data["phase"]).toBe("done");
  });

  it("throws the server message on a fail envelope (task not found)", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        ({
          ok: true,
          status: 200,
          json: async () => ({
            status: "fail",
            code: 2001,
            message: "Task not found: nope",
          }),
        }) as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await expect(client.getTaskMetric("nope")).rejects.toThrow(
      "Task not found: nope",
    );
  });

  it("URL-encodes the task id path segment", async () => {
    const spy = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        ({
          ok: true,
          status: 200,
          json: async () => ({ status: "success", data: {} }),
        }) as unknown as Response,
    );
    globalThis.fetch = spy;

    const client = new BladeClient("http://127.0.0.1:9999");
    await client.getTaskMetric("a/b c");
    expect(String(spy.mock.calls[0]![0])).toContain(
      "/api/v1/metric/a%2Fb%20c",
    );
  });
});
