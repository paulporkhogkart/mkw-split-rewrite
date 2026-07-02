import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    // Watch only the frontend. The repo carries huge non-frontend trees (temp/ ML
    // venvs + clips = ~170k files, captures, engine images, pi/, web/); chokidar
    // registers a watcher per directory SYNCHRONOUSLY at startup, which blocked the
    // dev server's event loop ~10s and left the app window on a white screen.
    watch: {
      ignored: [
        "**/src-tauri/**",
        "**/temp/**",
        "**/captures/**",
        "**/captures_sdr/**",
        "**/images/**",
        "**/debug_laps/**",
        "**/replays/**",
        "**/old_assets/**",
        "**/pi/**",
        "**/web/**",
        "**/dist-ui/**",
      ],
    },
    // Pre-transform the whole module graph as soon as the dev server starts
    // (i.e. during the ~10s cargo build in `tauri dev`), instead of paying the
    // 130-module on-demand waterfall while the app window sits on a white screen.
    warmup: { clientFiles: ["./src/main.js"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  // Frontend suite only - the `pi/` server package owns its own tests (`npm --prefix pi test`).
  test: { include: ["src/**/*.test.js"] },
  build: {
    outDir: "dist-ui",
    target: "chrome105",
    minify: !process.env.TAURI_ENV_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
  },
});
