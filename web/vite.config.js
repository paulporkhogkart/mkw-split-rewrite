import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Standalone public website (no Tauri). Reuses the desktop card components from ../src,
// so the dev server must be allowed to read one directory up. `vite build` (used on the
// Pi) follows imports anywhere and is unaffected. outDir defaults to dist -> web/dist.
export default defineConfig({
  plugins: [svelte()],
  server: { port: 1430, strictPort: true, fs: { allow: [".."] } },
  test: { include: ["**/*.test.js"] },
  build: { target: "chrome105" },
});
