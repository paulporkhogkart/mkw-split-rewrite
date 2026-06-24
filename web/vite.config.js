import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// The deployed site version = the root package.json version (the Pi builds web/ from the same
// tagged clone). Baked in so the #/version page can report its own bundle's build.
const pkg = JSON.parse(readFileSync(fileURLToPath(new URL("../package.json", import.meta.url)), "utf8"));

// Standalone public website (no Tauri). Reuses the desktop card components from ../src,
// so the dev server must be allowed to read one directory up. `vite build` (used on the
// Pi) follows imports anywhere and is unaffected. outDir defaults to dist -> web/dist.
export default defineConfig({
  plugins: [svelte()],
  define: { __SITE_VERSION__: JSON.stringify(pkg.version) },
  server: { host: "127.0.0.1", port: 1430, strictPort: true, fs: { allow: [".."] } },
  test: { include: ["**/*.test.js"] },
  build: { target: "chrome105" },
});
