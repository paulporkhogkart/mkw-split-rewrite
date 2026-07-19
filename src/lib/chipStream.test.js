import { describe, it, expect, vi } from "vitest";
import { loadManifest, sheetUrl, silUrl, createBitmapCache, _resetManifestCache } from "./chipStream.js";

const MANIFEST = { fw: 205, fh: 216, fps: 60, base: "http://chips.localhost/chips-v1/",
  combos: { "mario__base": { kart: false, idle_resume: 0, anims: { idle: { frames: 8, cols: 3, rows: 3 } } } } };

describe("loadManifest", () => {
  it("fetches and memoizes per base", async () => {
    _resetManifestCache();
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => MANIFEST });
    expect(await loadManifest("http://chips.localhost/", f)).toEqual(MANIFEST);
    await loadManifest("http://chips.localhost/", f);
    expect(f).toHaveBeenCalledTimes(1);
  });
  it("null on failure (chipless cards, never a throw)", async () => {
    _resetManifestCache();
    const f = vi.fn().mockRejectedValue(new Error("offline"));
    expect(await loadManifest("http://chips.localhost/", f)).toBeNull();
  });
});

describe("urls", () => {
  it("builds tagged urls from the manifest base", () => {
    expect(sheetUrl(MANIFEST, "mario__base", "idle"))
      .toBe("http://chips.localhost/chips-v1/mario__base__idle.webp");
    expect(silUrl(MANIFEST, "mario__base", "idle", 2))
      .toBe("http://chips.localhost/chips-v1/mario__base__idle__sil_k2.png");
  });
});

describe("createBitmapCache", () => {
  it("loads each anim once, LRU-evicts and closes", async () => {
    const closed = [];
    const pending = [];
    const loader = vi.fn((url) => {
      const p = Promise.resolve({ url, close: () => closed.push(url) });
      pending.push(p);
      return p;
    });
    const cache = createBitmapCache(2, loader);
    const a = cache.get(MANIFEST, "mario__base");
    await Promise.all(pending);
    expect(loader).toHaveBeenCalledTimes(1);           // one anim in this combo
    expect(a.ready("idle")).toBe(true);
    const m2 = { ...MANIFEST, combos: { ...MANIFEST.combos, x__base: MANIFEST.combos["mario__base"], y__base: MANIFEST.combos["mario__base"] } };
    cache.get(m2, "x__base"); cache.get(m2, "y__base"); // 3rd combo evicts mario
    await Promise.all(pending);
    expect(closed.some((u) => u.includes("mario__base"))).toBe(true);
  });
  it("not-ready before decode resolves (skip-draw-hold contract)", () => {
    let resolve; const loader = () => new Promise((r) => (resolve = r));
    const cache = createBitmapCache(2, loader);
    const h = cache.get(MANIFEST, "mario__base");
    expect(h.ready("idle")).toBe(false);
  });
  it("evicted-then-reacquired combo closes the late bitmap instead of leaking it", async () => {
    const resolvers = new Map(); const closed = [];
    const loader = (url) => new Promise((res) => {
      const arr = resolvers.get(url) || [];
      arr.push(res);
      resolvers.set(url, arr);
    });
    const cache = createBitmapCache(1, loader);
    const m2 = { ...MANIFEST, combos: { ...MANIFEST.combos, x__base: MANIFEST.combos["mario__base"] } };
    const a = cache.get(MANIFEST, "mario__base");         // entry A, decode in flight
    cache.get(m2, "x__base");                              // evicts A (limit 1) mid-decode
    const b = cache.get(m2, "mario__base");                // re-acquire: NEW entry B (evicts x)
    const firstUrl = [...resolvers.keys()][0];
    resolvers.get(firstUrl)[0]({ url: firstUrl, close: () => closed.push(firstUrl) }); // A's late resolve
    await Promise.resolve(); await Promise.resolve();
    expect(closed).toContain(firstUrl);                    // late bitmap released, not leaked
    expect(b.ready("idle")).toBe(false);                   // new entry untouched by A's resolve
    expect(a.ready("idle")).toBe(false);                   // stale handle never flips ready
  });
  it("dispose closes every resolved bitmap, clears entries, and late in-flight loads self-close without double-close", async () => {
    const closeCounts = new Map();
    const resolvers = new Map();
    const loader = (url) => new Promise((res) => {
      const arr = resolvers.get(url) || [];
      arr.push(res);
      resolvers.set(url, arr);
    });
    const closeFor = (url) => () => closeCounts.set(url, (closeCounts.get(url) || 0) + 1);
    const cache = createBitmapCache(12, loader);
    const m2 = { ...MANIFEST, combos: { ...MANIFEST.combos, x__base: MANIFEST.combos["mario__base"] } };
    cache.get(MANIFEST, "mario__base"); // entry A, resolves before dispose
    cache.get(m2, "x__base");           // entry B, resolves after dispose
    const urlA = sheetUrl(MANIFEST, "mario__base", "idle");
    const urlB = sheetUrl(m2, "x__base", "idle");
    resolvers.get(urlA)[0]({ url: urlA, close: closeFor(urlA) }); // resolves before dispose
    await Promise.resolve(); await Promise.resolve();

    cache.dispose();

    resolvers.get(urlB)[0]({ url: urlB, close: closeFor(urlB) }); // resolves after dispose
    await Promise.resolve(); await Promise.resolve();

    expect(closeCounts.get(urlA)).toBe(1); // closed once by dispose
    expect(closeCounts.get(urlB)).toBe(1); // closed once by its own late-resolve else-branch
  });

  it("unknown combo returns never-ready handle without burning an LRU slot", async () => {
    const closed = [];
    const loader = (url) => Promise.resolve({ url, close: () => closed.push(url) });
    const cache = createBitmapCache(1, loader);
    const a = cache.get(MANIFEST, "mario__base");
    await Promise.resolve(); await Promise.resolve();
    expect(a.ready("idle")).toBe(true);
    const u = cache.get(MANIFEST, "does_not_exist__base");
    expect(u.ready("idle")).toBe(false);
    // still ready: unknown combo didn't evict the real one
    expect(a.ready("idle")).toBe(true);
    expect(closed.length).toBe(0);
  });
});
