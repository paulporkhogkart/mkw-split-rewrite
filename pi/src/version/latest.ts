import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export interface LatestVersions { tag: string | null; app: string | null; fetched_at: number; errors: string[]; }
export type LatestFn = (force?: boolean) => Promise<LatestVersions>;

export function parseSemver(v: string): [number, number, number] | null {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec((v ?? '').trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** -1/0/1; 0 when either side is unparseable (callers treat that as "can't tell").
 *  NOTE: web/src/lib/version.js has a parallel variant that returns null instead of 0 —
 *  pi returns 0 because it only ever compares already-parsed/normalized tag strings. */
export function compareSemver(a: string, b: string): number {
  const pa = parseSemver(a), pb = parseSemver(b);
  if (!pa || !pb) return 0;
  for (let i = 0; i < 3; i++) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  return 0;
}

/** Highest semver among the tag names (leading v stripped). GitHub's tag order isn't
 *  guaranteed, so we sort ourselves. Assumes < 100 tags (current reality). */
export function pickLatestTag(names: string[]): string | null {
  let best: string | null = null;
  for (const n of names) {
    const p = parseSemver(n);
    if (!p) continue;
    const norm = `${p[0]}.${p[1]}.${p[2]}`;
    if (best === null || compareSemver(norm, best) > 0) best = norm;
  }
  return best;
}

/** Resolve the GitHub repo slug + updater manifest URL. Env overrides win; otherwise both are
 *  derived from tauri.conf.json's updater endpoint. */
export function resolveRelease(): { repo: string | null; manifest: string | null } {
  const envRepo = process.env.MKW_RELEASE_REPO || null;
  const envManifest = process.env.MKW_UPDATER_MANIFEST || null;
  let endpoint: string | null = envManifest;
  if (!endpoint || !envRepo) {
    try {
      const p = fileURLToPath(new URL('../../../src-tauri/tauri.conf.json', import.meta.url));
      const conf = JSON.parse(readFileSync(p, 'utf8'));
      endpoint = endpoint || conf?.plugins?.updater?.endpoints?.[0] || null;
    } catch { /* fall through to whatever we have */ }
  }
  let repo = envRepo;
  if (!repo && endpoint) {
    const m = /github\.com\/([^/]+\/[^/]+?)(?:\.git)?\//.exec(endpoint);
    repo = m ? m[1] : null;
  }
  return { repo, manifest: envManifest || endpoint };
}

async function fetchJson(url: string, headers: Record<string, string>, fetchImpl: typeof fetch): Promise<any> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 5000);
  try {
    const res = await fetchImpl(url, { headers, signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally { clearTimeout(timer); }
}

/** A cached latest-version fetcher. The two lookups degrade independently to the last-good
 *  value; failures are recorded in `errors` and never throw. */
export function makeLatestFetcher(opts: {
  ttlMs?: number; now?: () => number; fetchImpl?: typeof fetch; repo?: string | null; manifest?: string | null;
} = {}): LatestFn {
  const ttl = opts.ttlMs ?? (Number(process.env.MKW_VERSION_CACHE_MS) || 600000);
  const now = opts.now ?? Date.now;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const resolved = (opts.repo !== undefined || opts.manifest !== undefined)
    ? { repo: opts.repo ?? null, manifest: opts.manifest ?? null }
    : resolveRelease();
  let cache: LatestVersions | null = null;

  return async function getLatest(force = false): Promise<LatestVersions> {
    if (!force && cache && now() - cache.fetched_at < ttl) return cache;
    const errors: string[] = [];
    let tag = cache?.tag ?? null;
    let app = cache?.app ?? null;
    if (resolved.repo) {
      try {
        const headers: Record<string, string> = { Accept: 'application/vnd.github+json', 'User-Agent': 'mkw-version' };
        if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
        const tags = await fetchJson(`https://api.github.com/repos/${resolved.repo}/tags?per_page=100`, headers, fetchImpl);
        if (!Array.isArray(tags)) throw new Error('unexpected tags response');
        const picked = pickLatestTag((tags as { name: string }[]).map((t) => t.name));
        if (picked) tag = picked;
      } catch (e) { errors.push(`tags: ${(e as Error).message}`); }
    } else errors.push('tags: no repo configured');
    if (resolved.manifest) {
      try {
        const mf = await fetchJson(resolved.manifest, { 'User-Agent': 'mkw-version' }, fetchImpl);
        if (mf?.version) app = String(mf.version);
      } catch (e) { errors.push(`app: ${(e as Error).message}`); }
    } else errors.push('app: no manifest configured');
    cache = { tag, app, fetched_at: now(), errors };
    return cache;
  };
}
