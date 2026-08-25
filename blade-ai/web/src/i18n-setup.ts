/**
 * i18n bootstrap — MUST be the first import in main.tsx.
 *
 * ESM imports hoist: every module-level evaluation in the import graph
 * runs before main.tsx's own statements. Doing ``configureI18n`` here
 * (as the first import) guarantees any module that snapshots translated
 * strings at load time sees the browser-detected language, not the
 * core default (``en`` when ``process`` is absent).
 *
 * Resolution chain: localStorage explicit choice > navigator.language
 * > "en". The settings page (design §7.5) writes ``LANG_STORAGE_KEY``
 * and re-runs ``configureI18n`` through the BootContext's
 * ``setLangChoice`` — this module owns the one-time boot resolution;
 * runtime switching lives in RootLayout.
 */
import { configureI18n, type LangCode } from "@blade-ai/core";

/** localStorage key for the explicit interface-language choice.
 *  Values: "zh" | "en". Absent (or removed) = follow the browser. */
export const LANG_STORAGE_KEY = "blade_ai_lang";

/** The three positions of the settings-page language control. */
export type LangChoice = "browser" | LangCode;

/** Read the persisted choice ("browser" when unset / storage denied). */
export function readLangChoice(): LangChoice {
  try {
    const v = localStorage.getItem(LANG_STORAGE_KEY);
    if (v === "zh" || v === "en") return v;
  } catch {
    // Storage disabled (private mode etc.) — session-scoped "browser".
  }
  return "browser";
}

/** Resolve a choice to a concrete dictionary. "browser" re-derives
 *  from ``navigator.language`` so the control reflects the truth. */
export function resolveLang(choice: LangChoice): LangCode {
  if (choice !== "browser") return choice;
  return navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

configureI18n(resolveLang(readLangChoice()));
