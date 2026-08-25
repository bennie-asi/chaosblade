import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// vitest/config's defineConfig extends Vite's UserConfig with `test`.
import { defineConfig } from "vitest/config";

const pkg = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
) as { version: string };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    // Baked at build time; shown by /doctor as the host version (the
    // web counterpart of the TUI's PKG_VERSION ctx wiring).
    __BLADE_WEB_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Local single-machine form: the Python backend runs on a random
    // loopback port (spawned by `blade-ai web`); BLADE_AI_SERVER tells
    // the dev server where to proxy. Falls back to the conventional
    // 8080 for manual `blade-ai server` runs.
    proxy: {
      "/api": {
        target: process.env.BLADE_AI_SERVER ?? "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    // Recharts / @xyflow/react arrive in P3 and must stay out of the
    // first-paint bundle — route-level lazy loading is enforced by the
    // router; manualChunks keeps the vendor split explicit.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          tanstack: ["@tanstack/react-router", "@tanstack/react-query"],
          // GFM stack (react-markdown + remark/unified deps). It IS
          // first-paint — the chat surface needs it — but as its own
          // vendor chunk it downloads in parallel and caches
          // independently of app code (it changes ~never).
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
