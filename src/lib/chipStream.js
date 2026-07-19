// Chip asset access for LiveCard: manifest (memoized) + per-combo ImageBitmap cache.
// Binding rules (site-pack spec Playback): pre-decode a combo's sheets into ImageBitmaps
// (pinned, GPU-backed); if one isn't ready at draw time, the caller skips the draw and
// holds the previous frame — never blank.

const TTL = 5 * 60 * 1000;
let manifestMemo = new Map(); // base -> { at, p }

export function _resetManifestCache() { manifestMemo = new Map(); }

export async function loadManifest(chipsBase, fetchFn = globalThis.fetch) {
  const hit = manifestMemo.get(chipsBase);
  if (hit && Date.now() - hit.at < TTL) return hit.p;
  const p = (async () => {
    try {
      const r = await fetchFn(`${chipsBase}manifest.json`);
      return r.ok ? await r.json() : null;
    } catch { return null; }
  })();
  manifestMemo.set(chipsBase, { at: Date.now(), p });
  const v = await p;
  if (v === null) manifestMemo.delete(chipsBase); // failed fetch: retry next mount
  return v;
}

export const sheetUrl = (m, combo, anim) => `${m.base}${combo}__${anim}.webp`;
export const silUrl = (m, combo, anim, k) => `${m.base}${combo}__${anim}__sil_k${k}.png`;

/** Default loader: fetch-decode a sheet into a pinned ImageBitmap. DOM-only, untested. */
export async function defaultBitmapLoader(url) {
  const img = new Image();
  img.src = url;
  await img.decode();
  return await createImageBitmap(img);
}

export function createBitmapCache(limit = 12, loader = defaultBitmapLoader) {
  const combos = new Map(); // combo -> { bitmaps: {anim: bitmap|null} }
  function evict() {
    while (combos.size > limit) {
      const [oldest, entry] = combos.entries().next().value;
      combos.delete(oldest);
      for (const b of Object.values(entry.bitmaps)) b && b.close && b.close();
    }
  }
  return {
    get(manifest, combo) {
      let entry = combos.get(combo);
      if (entry) { combos.delete(combo); combos.set(combo, entry); } // LRU touch
      else {
        entry = { bitmaps: {} };
        combos.set(combo, entry);
        const def = manifest.combos[combo];
        if (def) for (const anim of Object.keys(def.anims)) {
          entry.bitmaps[anim] = null;
          loader(sheetUrl(manifest, combo, anim))
            .then((b) => { if (combos.has(combo)) entry.bitmaps[anim] = b; else b && b.close && b.close(); })
            .catch(() => {}); // missing sheet: stays null, draw skips forever (chipless)
        }
        evict();
      }
      return { bitmaps: entry.bitmaps, ready: (anim) => !!entry.bitmaps[anim] };
    },
  };
}
