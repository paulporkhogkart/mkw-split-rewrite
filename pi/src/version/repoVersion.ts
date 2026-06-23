import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// pi/src/version/repoVersion.ts -> repo root is three levels up.
let cached: string | null = null;

/** The repo's single source-of-truth version (root package.json), read + cached once.
 *  On the Pi the clone is checked out at the deployed tag, so this is the deployed build. */
export function repoVersion(): string {
  if (cached !== null) return cached;
  try {
    const p = fileURLToPath(new URL('../../../package.json', import.meta.url));
    cached = (JSON.parse(readFileSync(p, 'utf8')).version as string) ?? 'unknown';
  } catch { cached = 'unknown'; }
  return cached;
}
