import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { pickServerToken, readConfigFileToken, resolveServerToken } from "./auth.js";

describe("api / pickServerToken precedence", () => {
  // Mirrors the Python settings precedence the TUI must match:
  // config.json beats env vars. Getting this backwards would make the
  // TUI present a different credential than the Python CLI does for
  // the same server — the symptom would be "blade-ai inject works,
  // TUI gets 401" against one token and not the other.

  it("prefers the config-file value over the env value", () => {
    expect(pickServerToken("cfg-token", "env-token")).toBe("cfg-token");
  });

  it("falls back to the env value when the config value is absent", () => {
    expect(pickServerToken(undefined, "env-token")).toBe("env-token");
  });

  it("treats blank config values as absent", () => {
    expect(pickServerToken("   ", "env-token")).toBe("env-token");
    expect(pickServerToken("", undefined)).toBeUndefined();
  });

  it("returns undefined when neither source has a token", () => {
    expect(pickServerToken(undefined, undefined)).toBeUndefined();
  });

  it("trims surrounding whitespace from the winner", () => {
    expect(pickServerToken("  cfg-token  ", undefined)).toBe("cfg-token");
  });
});

describe("api / readConfigFileToken", () => {
  // Tests redirect the config dir via BLADE_AI_CONFIG_DIR — the same
  // seam Python's _active_config_file() honours, so these tests never
  // touch the developer's real ~/.blade-ai/config.json.
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "blade-auth-test-"));
    process.env["BLADE_AI_CONFIG_DIR"] = dir;
  });

  afterEach(() => {
    delete process.env["BLADE_AI_CONFIG_DIR"];
    rmSync(dir, { recursive: true, force: true });
  });

  it("reads server_token from config.json", () => {
    writeFileSync(
      join(dir, "config.json"),
      JSON.stringify({ server_token: "s3cret", other: 1 }),
    );
    expect(readConfigFileToken()).toBe("s3cret");
  });

  it("returns undefined when the key is missing", () => {
    writeFileSync(join(dir, "config.json"), JSON.stringify({ other: 1 }));
    expect(readConfigFileToken()).toBeUndefined();
  });

  it("returns undefined when the value is not a string", () => {
    writeFileSync(join(dir, "config.json"), JSON.stringify({ server_token: 42 }));
    expect(readConfigFileToken()).toBeUndefined();
  });

  it("fails soft on a missing file", () => {
    expect(readConfigFileToken()).toBeUndefined();
  });

  it("fails soft on corrupt JSON", () => {
    writeFileSync(join(dir, "config.json"), "{ not json");
    expect(readConfigFileToken()).toBeUndefined();
  });
});

describe("api / resolveServerToken", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "blade-auth-test-"));
    process.env["BLADE_AI_CONFIG_DIR"] = dir;
    delete process.env["BLADE_AI_SERVER_TOKEN"];
  });

  afterEach(() => {
    delete process.env["BLADE_AI_CONFIG_DIR"];
    delete process.env["BLADE_AI_SERVER_TOKEN"];
    rmSync(dir, { recursive: true, force: true });
  });

  it("resolves nothing when both sources are empty", () => {
    expect(resolveServerToken()).toBeUndefined();
  });

  it("resolves the env token when the config has none", () => {
    process.env["BLADE_AI_SERVER_TOKEN"] = "env-token";
    expect(resolveServerToken()).toBe("env-token");
  });

  it("resolves the config token over the env token", () => {
    writeFileSync(join(dir, "config.json"), JSON.stringify({ server_token: "cfg" }));
    process.env["BLADE_AI_SERVER_TOKEN"] = "env";
    expect(resolveServerToken()).toBe("cfg");
  });
});
