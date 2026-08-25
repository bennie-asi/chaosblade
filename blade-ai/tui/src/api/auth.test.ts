/**
 * IO-leg tests for the TUI host's auth resolver. The pure precedence
 * rule (pickServerToken) is tested in @blade-ai/core; here we cover
 * the Node-specific config-file read and the combined resolution.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { readConfigFileToken, resolveServerToken } from "./auth.js";

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
