import { defineConfig } from 'vitest/config';

// The pi server package owns its own test run, independent of the repo-root
// vite.config.js (which is the Svelte frontend's). `npm --prefix pi test` resolves
// this config (nearest to the pi/ CWD), so server tests never depend on - or get
// scoped out by - the frontend config. Includes resolve relative to pi/.
export default defineConfig({
  test: { include: ['src/**/*.test.ts'] },
});
