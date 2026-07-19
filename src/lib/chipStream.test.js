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
});
