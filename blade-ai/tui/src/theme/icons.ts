/**
 * Single source of truth for glyphs used across the TUI.
 *
 * Two palettes — Unicode and ASCII fallback — selected once at module
 * load based on the user's locale + ``BLADE_AI_ASCII`` override. Mirrors
 * the Python TUI's ``theme.py`` decision so the two front-ends look
 * the same on a glyph-poor terminal.
 *
 * Design rules:
 *   - One glyph per concept (no ✓ ✔ ✅ duplicates).
 *   - Width-stable: every Unicode entry occupies 1 terminal cell on
 *     fonts that follow the EastAsianWidth table. No emoji variation
 *     selectors (VS-16) — they double-width on iTerm.
 */

function asciiMode(): boolean {
  const forced = (process.env["BLADE_AI_ASCII"] ?? "").toLowerCase().trim();
  if (["1", "true", "yes", "on"].includes(forced)) return true;
  if (["0", "false", "no", "off"].includes(forced)) return false;

  const locale = (
    process.env["LC_ALL"] ??
    process.env["LANG"] ??
    ""
  ).toLowerCase();
  if (!locale) return true; // empty locale (cron, minimal containers) → ASCII
  return !locale.includes("utf-8") && !locale.includes("utf8");
}

const _ASCII = asciiMode();

export interface IconSet {
  success: string;
  fail: string;
  warning: string;
  pending: string;
  active: string;
  agent: string;
  thinking: string;
  user: string;
  /** Standalone caret used as the input-prompt leader (``❯``). Distinct
   * from ``user`` so the role-tag glyph and the input prompt can
   * evolve independently. */
  prompt: string;
  system: string;
  /** Document / report artefact marker (postmortem card title). */
  doc: string;
  tree: string;
  bullet: string;
  arrow: string;
  /** Reticle leader for node-progress readouts
   *  (``◎ Checking target health...``) — the system sighting the
   *  target and taking a reading. Double-ring hollow: distinct from
   *  the agent's filled ⏺, pending ○ and active ◉. Painted in
   *  ``forge.gold`` (molten gold) — same warm family as the ⏺
   *  leader but hue-shifted so the two speakers stay distinct; an
   *  earlier desaturated ``forge.dim`` tone read muddy and hard to
   *  see on light terminals. */
  scope: string;
}

const Unicode: IconSet = {
  // Status
  success: "✓", // U+2713
  fail: "✗", // U+2717
  warning: "⚠", // U+26A0 (no VS-16)
  pending: "○", // U+25CB
  active: "◉", // U+25C9
  // Roles / leaders
  agent: "⏺", // U+23FA — agent reply marker
  thinking: "✻", // U+273B — thinking marker (decorative)
  user: ">",
  prompt: "❯", // U+276F — heavier-weight caret for the input prompt
  system: "ℹ", // U+2139
  doc: "≡", // U+2261 — reads as stacked report lines
  // Inline tree branch
  tree: "⎿", // U+23BF
  // Visual decorations
  bullet: "•",
  arrow: "→",
  scope: "◎", // U+25CE bullseye — instrument reticle
};

const Ascii: IconSet = {
  success: "+",
  fail: "x",
  warning: "!",
  pending: "o",
  active: "*",
  agent: "*",
  thinking: "*",
  user: ">",
  prompt: ">",
  system: "i",
  doc: "=",
  tree: "\\",
  bullet: "*",
  arrow: "->",
  scope: "@",
};

export const Icons: IconSet = _ASCII ? Ascii : Unicode;

/** Whether the active palette is the ASCII fallback. */
export const isAsciiMode = _ASCII;
