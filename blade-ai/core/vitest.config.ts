/**
 * Vitest config for @blade-ai/core — pure unit tests under src/**\/*.test.ts.
 *
 * Mirrors tui/vitest.config.ts: node environment, zh dictionary pinned via
 * BLADE_AI_LANG so string assertions are host-locale-independent (the i18n
 * module auto-detects from process.env at import time under Node; see
 * src/i18n/index.ts). No DOM / Ink rendering here — component tests stay
 * in the frontend packages.
 */

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
    bail: 0,
    reporters: ["default"],
    env: {
      BLADE_AI_LANG: "zh",
    },
  },
});
