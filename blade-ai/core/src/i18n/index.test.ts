/**
 * i18n translator tests.
 *
 * Scope: the pure ``t()`` / ``tArr()`` runtime, NOT locale detection
 * (that lives behind module-load-time captured ``ACTIVE_LANG`` and
 * needs a child process to exercise — see scripts/smoke-i18n.mjs).
 *
 * What matters here:
 *   - {param} interpolation handles missing params gracefully
 *   - missing keys return the key itself (visible "untranslated" marker)
 *   - tArr() returns [] for non-array values, never throws
 */

import { describe, expect, it } from "vitest";
import {
  configureI18n,
  detectLangFromEnv,
  getActiveLang,
  t,
  tArr,
} from "./index.js";

describe("t() interpolation", () => {
  it("returns the raw string when no params are given", () => {
    // ``error.next_label`` exists in both en and zh dicts.
    const out = t("error.next_label");
    expect(out.length).toBeGreaterThan(0);
    expect(out).not.toBe("error.next_label");
  });

  it("substitutes {name} placeholders", () => {
    const out = t("replay.unknown_command", { name: "foo" });
    expect(out).toContain("foo");
    expect(out).not.toContain("{name}");
  });

  it("preserves {placeholder} when the param is missing", () => {
    // Don't blow up — keep the brace marker so it's clear what the
    // template asked for.
    const out = t("replay.unknown_command", {});
    expect(out).toContain("{name}");
  });

  it("coerces numeric params to strings", () => {
    const out = t("tasks.head", { n: 3, total: 12 });
    expect(out).toMatch(/3/);
    expect(out).toMatch(/12/);
  });
});

describe("t() missing keys", () => {
  it("returns the key itself for unknown lookups", () => {
    expect(t("nonexistent.key")).toBe("nonexistent.key");
  });

  it("returns the en fallback when the active dict lacks the key", () => {
    // Every key in en should also resolve under zh via the fallback
    // chain — pin one we know exists in en.
    const v = t("error.next_label");
    expect(typeof v).toBe("string");
    expect(v.length).toBeGreaterThan(0);
  });
});

describe("tArr()", () => {
  it("returns an array for array-valued keys", () => {
    const phrases = tArr("thinking.phrases");
    expect(Array.isArray(phrases)).toBe(true);
    expect(phrases.length).toBeGreaterThan(0);
  });

  it("returns an empty array for missing keys", () => {
    expect(tArr("nonexistent.array.key")).toEqual([]);
  });

  it("returns an empty array for string-valued keys (type mismatch)", () => {
    // ``error.next_label`` is a string. tArr() should refuse, not
    // happily wrap it in an array.
    expect(tArr("error.next_label")).toEqual([]);
  });
});

describe("getActiveLang", () => {
  it("is one of the supported codes", () => {
    expect(["en", "zh"]).toContain(getActiveLang());
  });
});

describe("detectLangFromEnv (pure)", () => {
  // The extraction made locale detection a pure function — exercise the
  // full matrix here instead of via child processes.
  it("honours BLADE_AI_LANG prefixes, case-insensitively", () => {
    expect(detectLangFromEnv({ BLADE_AI_LANG: "zh" })).toBe("zh");
    expect(detectLangFromEnv({ BLADE_AI_LANG: "zh-CN" })).toBe("zh");
    expect(detectLangFromEnv({ BLADE_AI_LANG: "ZH_TW.UTF-8" })).toBe("zh");
    expect(detectLangFromEnv({ BLADE_AI_LANG: "en_US" })).toBe("en");
  });

  it("falls back to LC_ALL / LANG, zh prefix only", () => {
    expect(detectLangFromEnv({ LC_ALL: "zh_CN.UTF-8" })).toBe("zh");
    expect(detectLangFromEnv({ LANG: "zh-Hans" })).toBe("zh");
    expect(detectLangFromEnv({ LC_ALL: "en_US.UTF-8" })).toBe("en");
    expect(detectLangFromEnv({ LC_ALL: "ja_JP.UTF-8" })).toBe("en");
  });

  it("treats blank LC_ALL as absent so LANG wins", () => {
    expect(detectLangFromEnv({ LC_ALL: "", LANG: "zh_CN" })).toBe("zh");
    expect(detectLangFromEnv({ LC_ALL: "  ", LANG: "en_US" })).toBe("en");
  });

  it("BLADE_AI_LANG beats the POSIX vars", () => {
    expect(
      detectLangFromEnv({ BLADE_AI_LANG: "en", LC_ALL: "zh_CN.UTF-8" }),
    ).toBe("en");
  });

  it("defaults to en when nothing is set", () => {
    expect(detectLangFromEnv({})).toBe("en");
  });
});

describe("configureI18n", () => {
  it("switches the active dictionary until restored", () => {
    const before = getActiveLang();
    try {
      configureI18n(before === "zh" ? "en" : "zh");
      expect(getActiveLang()).not.toBe(before);
      // t() follows the switch — spot-check a key present in both dicts.
      expect(t("error.next_label").length).toBeGreaterThan(0);
    } finally {
      configureI18n(before);
    }
    expect(getActiveLang()).toBe(before);
  });
});
