import { describe, expect, it } from "vitest";
import { pickServerToken } from "./auth.js";

describe("api / pickServerToken precedence", () => {
  // Mirrors the Python settings precedence every frontend must match:
  // config.json beats env vars. Getting this backwards would make the
  // frontend present a different credential than the Python CLI does
  // for the same server — the symptom would be "blade-ai inject works,
  // TUI gets 401" against one token and not the other.
  //
  // The IO legs (readConfigFileToken / resolveServerToken) live in the
  // TUI host — tui/src/api/auth.test.ts covers them against a tmpdir.

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
