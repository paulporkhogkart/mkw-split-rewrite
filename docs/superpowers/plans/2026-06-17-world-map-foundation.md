# World Map SP1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an interactive, correctly-labeled "World Map" view in the `web/` site — the hi-res MKW map with all 30 courses as living, hover-able icons, framed like the OBS feed inside the pbenguin UI.

**Architecture:** A committed Python build script turns the source art into static assets (`web/public/map/base.jpg`, `sprites/<slug>.png`, `manifest.json`). The Svelte site adds a hash-routed `World Map` view; `WorldMap.svelte` fetches the manifest and renders absolutely-positioned course sprites over the base, with calm-at-rest styling and a grow-dominant hover lift. No server calls in SP1.

**Tech Stack:** Python + OpenCV + ffmpeg (asset build, dev-time only); Svelte 4 + Vite + Vitest (`web/`); pbenguin theme tokens from `src/theme.css` (already imported by `web/src/main.js`).

**Spec:** `docs/superpowers/specs/2026-06-17-world-map-foundation-design.md`

---

## File Structure

**Created:**
- `scripts/map/sources/` — committed source art (2 AVIF layers, `highresmap.jpeg`, RR WebP).
- `scripts/map/labels.json` — `[{slug, cx, cy}]`, the finished icon→course centers (drives slug assignment).
- `scripts/map/build_map_assets.py` — the deterministic asset builder.
- `tests/test_map_assets.py` — validates the built manifest + assets.
- `web/public/map/base.jpg`, `web/public/map/sprites/<slug>.png` (×30), `web/public/map/manifest.json` — committed build outputs.
- `web/src/lib/map.js` — pure positioning/URL helpers.
- `web/src/lib/map.test.js` — unit tests for the helpers.
- `web/src/lib/view.js` — `viewFromHash()` router helper.
- `web/src/lib/view.test.js` — unit tests for the router helper.
- `web/src/WorldMap.svelte` — the map view component.

**Modified:**
- `web/src/App.svelte` — add the header nav + hash-routed view switch (Live | World Map).

Source-of-truth note: course slugs + display names come from `server/courses.py:CANONICAL_COURSES` (imported by the build script — do not hardcode the list).

---

## Task 1: Promote sources + generate labels.json

**Files:**
- Create: `scripts/map/sources/*` (copied), `scripts/map/labels.json`

- [ ] **Step 1: Copy the four source files into the repo**

```bash
mkdir -p scripts/map/sources
cp temp/map/MarioKartWorld_World_Map_Inner.webp \
   temp/map/MarioKartWorld_World_Map_Stages.webp \
   temp/map/highresmap.jpeg \
   temp/map/MKWorld_Icon_Rainbow_Road.webp \
   scripts/map/sources/
ls scripts/map/sources/
```
Expected: the four files listed.

- [ ] **Step 2: Generate `scripts/map/labels.json` from the completed labeling**

Run:
```bash
python - <<'PY'
import json
m = json.load(open("temp/map/mockup/manifest2.json"))
labels = [{"slug": c["slug"],
           "cx": round(c["hit"]["x"] + c["hit"]["w"]/2, 5),
           "cy": round(c["hit"]["y"] + c["hit"]["h"]/2, 5)} for c in m["courses"]]
assert len(labels) == 30 and len({l["slug"] for l in labels}) == 30, "expected 30 unique slugs"
json.dump(labels, open("scripts/map/labels.json", "w"), indent=1)
print("wrote", len(labels), "labels")
PY
```
Expected: `wrote 30 labels`.

- [ ] **Step 3: Commit**

```bash
git add scripts/map/sources scripts/map/labels.json
git commit -m "feat(map): commit world-map source art + course labels"
```

---

## Task 2: The asset build script

**Files:**
- Create: `scripts/map/build_map_assets.py`
- Output: `web/public/map/{base.jpg, sprites/<slug>.png, manifest.json}`

- [ ] **Step 1: Write `scripts/map/build_map_assets.py`**

```python
#!/usr/bin/env python3
"""Build the World Map web assets from the committed source art (deterministic).

Outputs web/public/map/{base.jpg, sprites/<slug>.png, manifest.json}.
Requires ffmpeg on PATH to decode the AVIF source layers. Only needed to *regenerate*
assets; building the website itself does not need ffmpeg.

  python scripts/map/build_map_assets.py
"""
import importlib.util, json, subprocess, tempfile
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "sources"
LABELS = Path(__file__).resolve().parent / "labels.json"
OUT = ROOT / "web" / "public" / "map"

DISPLAY_W = 1100          # CSS width the map renders at
BASE_W = DISPLAY_W * 2    # 2x export for retina crispness
RR_CENTER = (0.500, 0.734)
RR_WIDTH = 0.089          # normalized width of the Rainbow Road sprite


def load_canonical():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def decode_avif(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), str(dst)], check=True)


def smoothstep_alpha(crop, lo=16.0, hi=64.0):
    b = crop.max(axis=2).astype(np.float32)
    a = np.clip((b - lo) / (hi - lo), 0, 1)
    return (a * a * (3 - 2 * a) * 255).astype(np.uint8)


def detect_boxes(stages):
    H, W = stages.shape[:2]
    m = (stages.max(axis=2) > 50).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    boxes = [(int(x), int(y), int(w), int(h)) for i in range(1, n)
             for x, y, w, h, a in [st[i]] if a >= 3000 and w < W * 0.55 and h < H * 0.55]
    boxes.sort(key=lambda b: (round((b[1] + b[3] / 2) / 120), b[0]))
    return boxes


def orb_transform(a, b):
    o = cv2.ORB_create(6000)
    ka, da = o.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), None)
    kb, db = o.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), None)
    mm = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db),
                key=lambda z: z.distance)[:500]
    src = np.float32([ka[z.queryIdx].pt for z in mm]).reshape(-1, 1, 2)
    dst = np.float32([kb[z.trainIdx].pt for z in mm]).reshape(-1, 1, 2)
    M, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    return M


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sprites").mkdir(exist_ok=True)
    canon = load_canonical()
    labels = json.loads(LABELS.read_text())

    with tempfile.TemporaryDirectory() as td:
        inner_p, stages_p = Path(td) / "inner.png", Path(td) / "stages.png"
        decode_avif(SRC / "MarioKartWorld_World_Map_Inner.webp", inner_p)
        decode_avif(SRC / "MarioKartWorld_World_Map_Stages.webp", stages_p)
        inner, stages = cv2.imread(str(inner_p)), cv2.imread(str(stages_p))
    hires = cv2.imread(str(SRC / "highresmap.jpeg"))
    rr = cv2.imread(str(SRC / "MKWorld_Icon_Rainbow_Road.webp"), cv2.IMREAD_UNCHANGED)
    Hs, Ws = stages.shape[:2]; Hh, Wh = hires.shape[:2]

    M = orb_transform(inner, hires)
    scale = float(np.hypot(M[0, 0], M[0, 1]))

    def to_hi(x, y):
        p = M @ np.array([x, y, 1.0]); return float(p[0]), float(p[1])

    courses = []  # {sprite(BGRA), hit[x,y,w,h], spr[x,y,w,h]} in normalized base coords
    for (x, y, w, h) in detect_boxes(stages):
        PAD = 10
        px0, py0 = max(0, x - PAD), max(0, y - PAD)
        px1, py1 = min(Ws, x + w + PAD), min(Hs, y + h + PAD)
        crop = stages[py0:py1, px0:px1]
        mask = smoothstep_alpha(crop)
        tw, th = round((px1 - px0) * scale), round((py1 - py0) * scale)
        tmpl = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
        tmask = cv2.resize(mask, (tw, th), interpolation=cv2.INTER_AREA)
        pcx, pcy = to_hi((px0 + px1) / 2, (py0 + py1) / 2)
        S = 70
        rx0, ry0 = max(0, int(pcx - tw / 2 - S)), max(0, int(pcy - th / 2 - S))
        rx1, ry1 = min(Wh, int(pcx + tw / 2 + S)), min(Hh, int(pcy + th / 2 + S))
        res = cv2.matchTemplate(hires[ry0:ry1, rx0:rx1], tmpl, cv2.TM_CCOEFF_NORMED, mask=tmask)
        res[~np.isfinite(res)] = 0
        _, _, _, loc = cv2.minMaxLoc(res)
        tlx, tly = rx0 + loc[0], ry0 + loc[1]
        sprite = cv2.cvtColor(hires[tly:tly + th, tlx:tlx + tw], cv2.COLOR_BGR2BGRA)
        sprite[:, :, 3] = tmask[:sprite.shape[0], :sprite.shape[1]]
        bcx, bcy = to_hi(x + w / 2, y + h / 2)
        hcx, hcy = bcx + (tlx + tw / 2 - pcx), bcy + (tly + th / 2 - pcy)
        cw, ch = w * scale, h * scale
        courses.append({"sprite": sprite,
                        "hit": [(hcx - cw / 2) / Wh, (hcy - ch / 2) / Hh, cw / Wh, ch / Hh],
                        "spr": [tlx / Wh, tly / Hh, tw / Wh, th / Hh]})

    # Rainbow Road: not on the base art; placed from the supplied icon at the locked rect.
    rrw = round(RR_WIDTH * Wh); rrh = round(rrw * rr.shape[0] / rr.shape[1])
    cx, cy, wn = *RR_CENTER, RR_WIDTH
    hn = wn * (Wh / Hh) * (rr.shape[0] / rr.shape[1])
    courses.append({"sprite": cv2.resize(rr, (rrw, rrh), interpolation=cv2.INTER_AREA),
                    "hit": [cx - wn * 0.21, cy - hn * 0.24, wn * 0.42, hn * 0.42],
                    "spr": [cx - wn / 2, cy - hn / 2, wn, hn]})

    # Assign each course its slug by the nearest labeled center.
    manifest = {"base": {"w": BASE_W, "h": round(Hh * BASE_W / Wh)}, "courses": []}
    for c in courses:
        ccx, ccy = c["hit"][0] + c["hit"][2] / 2, c["hit"][1] + c["hit"][3] / 2
        slug = min(labels, key=lambda L: (L["cx"] - ccx) ** 2 + (L["cy"] - ccy) ** 2)["slug"]
        cv2.imwrite(str(OUT / "sprites" / f"{slug}.png"), c["sprite"])
        manifest["courses"].append({
            "slug": slug, "name": canon[slug],
            "hit": {k: round(v, 5) for k, v in zip("xywh", c["hit"])},
            "spr": {k: round(v, 5) for k, v in zip("xywh", c["spr"])}})

    base = cv2.resize(hires, (BASE_W, manifest["base"]["h"]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "base.jpg"), base, [cv2.IMWRITE_JPEG_QUALITY, 88])
    manifest["courses"].sort(key=lambda c: c["slug"])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))

    slugs = [c["slug"] for c in manifest["courses"]]
    assert len(slugs) == 30 and len(set(slugs)) == 30 and set(slugs) == set(canon), \
        f"slug mismatch: {set(slugs) ^ set(canon)}"
    print(f"built {len(slugs)} courses -> {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the build**

Run: `python scripts/map/build_map_assets.py`
Expected: `built 30 courses -> .../web/public/map`

- [ ] **Step 3: Eyeball the outputs**

Run: `ls web/public/map web/public/map/sprites | head -40`
Expected: `base.jpg`, `manifest.json`, and 30 `<slug>.png` files (e.g. `bowsers_castle.png`, `rainbow_road.png`).
Open `web/public/map/base.jpg` and a couple sprites to confirm they look right.

- [ ] **Step 4: Commit**

```bash
git add scripts/map/build_map_assets.py web/public/map
git commit -m "feat(map): asset build script + generated world-map assets"
```

---

## Task 3: Manifest validation test

**Files:**
- Test: `tests/test_map_assets.py`

- [ ] **Step 1: Write the test**

```python
import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "web" / "public" / "map"


def _canon():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def _manifest():
    return json.loads((MAP / "manifest.json").read_text())


def test_manifest_is_30_unique_canonical_courses():
    slugs = [c["slug"] for c in _manifest()["courses"]]
    assert len(slugs) == 30
    assert len(set(slugs)) == 30
    assert set(slugs) == set(_canon())


def test_every_course_has_a_sprite_and_sane_rects():
    for c in _manifest()["courses"]:
        assert (MAP / "sprites" / f"{c['slug']}.png").exists(), c["slug"]
        for r in (c["hit"], c["spr"]):
            assert r["w"] > 0 and r["h"] > 0
            assert r["x"] + r["w"] <= 1.02 and r["y"] + r["h"] <= 1.02
            assert r["x"] >= -0.02 and r["y"] >= -0.02


def test_names_match_canonical_display_names():
    canon = _canon()
    for c in _manifest()["courses"]:
        assert c["name"] == canon[c["slug"]]
```

- [ ] **Step 2: Run it (assets already built in Task 2)**

Run: `python -m pytest tests/test_map_assets.py -v`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_map_assets.py
git commit -m "test(map): validate generated world-map manifest"
```

---

## Task 4: Frontend map helpers (TDD)

**Files:**
- Create: `web/src/lib/map.js`
- Test: `web/src/lib/map.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
import { describe, it, expect } from "vitest";
import { hitStyle, spriteStyle, spriteUrl, baseUrl, manifestUrl } from "./map.js";

describe("map helpers", () => {
  it("hitStyle formats a normalized rect as percentages", () => {
    expect(hitStyle({ x: 0.5, y: 0.25, w: 0.1, h: 0.2 }))
      .toBe("left:50.000%;top:25.000%;width:10.000%;height:20.000%");
  });

  it("spriteStyle positions the sprite relative to its hit box", () => {
    const hit = { x: 0.4, y: 0.4, w: 0.1, h: 0.1 };
    expect(spriteStyle(hit, hit)).toBe("left:0.000%;top:0.000%;width:100.000%;height:100.000%");
    const spr = { x: 0.38, y: 0.36, w: 0.14, h: 0.18 };
    expect(spriteStyle(hit, spr)).toBe("left:-20.000%;top:-40.000%;width:140.000%;height:180.000%");
  });

  it("URL builders point at the public /map assets", () => {
    expect(spriteUrl("rainbow_road")).toBe("/map/sprites/rainbow_road.png");
    expect(baseUrl()).toBe("/map/base.jpg");
    expect(manifestUrl()).toBe("/map/manifest.json");
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd web && npm test -- src/lib/map.test.js`
Expected: FAIL — cannot resolve `./map.js`.

- [ ] **Step 3: Implement `web/src/lib/map.js`**

```javascript
// Pure helpers for the World Map. Assets live in web/public/map/ (served at /map/*).
const MAP_DIR = "/map";
export const manifestUrl = () => `${MAP_DIR}/manifest.json`;
export const baseUrl = () => `${MAP_DIR}/base.jpg`;
export const spriteUrl = (slug) => `${MAP_DIR}/sprites/${slug}.png`;

const pct = (v) => (v * 100).toFixed(3) + "%";

// Absolute placement of a course hit box, as % of the map frame.
export function hitStyle(hit) {
  return `left:${pct(hit.x)};top:${pct(hit.y)};width:${pct(hit.w)};height:${pct(hit.h)}`;
}

// The sprite image is placed RELATIVE to its hit box, so a CSS :hover on the hit
// can transform the child. hit/spr are both normalized to the frame.
export function spriteStyle(hit, spr) {
  const l = (spr.x - hit.x) / hit.w, t = (spr.y - hit.y) / hit.h;
  return `left:${pct(l)};top:${pct(t)};width:${pct(spr.w / hit.w)};height:${pct(spr.h / hit.h)}`;
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd web && npm test -- src/lib/map.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/map.js web/src/lib/map.test.js
git commit -m "feat(web): world-map positioning + URL helpers"
```

---

## Task 5: Hash-route helper (TDD)

**Files:**
- Create: `web/src/lib/view.js`
- Test: `web/src/lib/view.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("defaults to the live card wall", () => {
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("#/unknown")).toBe("live");
  });
  it("returns map for the map hash", () => {
    expect(viewFromHash("#/map")).toBe("map");
    expect(viewFromHash("#map")).toBe("map");
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd web && npm test -- src/lib/view.test.js`
Expected: FAIL — cannot resolve `./view.js`.

- [ ] **Step 3: Implement `web/src/lib/view.js`**

```javascript
// Two views, selected by the location hash. Unknown hashes fall back to "live".
export function viewFromHash(hash) {
  return (hash || "").replace(/^#\/?/, "") === "map" ? "map" : "live";
}
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd web && npm test -- src/lib/view.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/view.js web/src/lib/view.test.js
git commit -m "feat(web): hash-route helper for live/map views"
```

---

## Task 6: WorldMap.svelte

**Files:**
- Create: `web/src/WorldMap.svelte`

- [ ] **Step 1: Write the component**

```svelte
<script>
  import { onMount } from "svelte";
  import { baseUrl, manifestUrl, spriteUrl, hitStyle, spriteStyle } from "./lib/map.js";

  let manifest = null;
  let error = false;

  onMount(async () => {
    try {
      const r = await fetch(manifestUrl(), { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      manifest = await r.json();
    } catch (e) {
      console.error("world map: manifest load failed", e);
      error = true;
    }
  });
</script>

<div class="map-view">
  <div class="frame">
    {#if error}
      <div class="msg">Map unavailable.</div>
    {:else if manifest}
      <div class="stage">
        <img class="base" src={baseUrl()} alt="Mario Kart World map" />
        <!-- SP2 (territory) draws here, between the base and the icons -->
        <div class="territory" aria-hidden="true"></div>
        <div class="icons">
          {#each manifest.courses as c (c.slug)}
            <div class="hit" data-slug={c.slug} title={c.name} style={hitStyle(c.hit)}>
              <img class="spr" src={spriteUrl(c.slug)} alt={c.name}
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
            </div>
          {/each}
        </div>
        <!-- SP3 (hover popup) mounts here -->
        <div class="popups" aria-hidden="true"></div>
      </div>
    {:else}
      <div class="msg">Loading map…</div>
    {/if}
  </div>
</div>

<style>
  .map-view { padding: 16px; }
  .frame {
    position: relative; max-width: 1100px; margin: 0 auto;
    background: var(--feed-bg); border: 1px solid var(--bd);
    border-radius: var(--r); overflow: hidden;
  }
  .frame::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    border-radius: var(--r); box-shadow: inset 0 0 60px 10px rgba(0,0,0,.45);
  }
  .stage { position: relative; width: 100%; }
  /* calm at rest: the whole map sits muted so SP2's territory colour can lead */
  .base { display: block; width: 100%; height: auto; filter: saturate(.82) brightness(.9); }
  .territory, .popups { position: absolute; inset: 0; pointer-events: none; }
  .icons { position: absolute; inset: 0; }
  .hit { position: absolute; cursor: pointer; }
  .spr {
    position: absolute; pointer-events: none; transform-origin: 50% 70%;
    filter: saturate(.78) brightness(.86);
    transition: transform .15s ease, filter .15s ease; will-change: transform;
  }
  .hit:hover { z-index: 50; }
  /* grow-dominant lift: the enlarged sprite always covers its own footprint */
  .hit:hover .spr {
    transform: scale(1.18) translateY(-4%);
    filter: drop-shadow(0 7px 9px rgba(0,0,0,.6)) brightness(1.08) saturate(1.05);
  }
  .msg { padding: 4rem; text-align: center; color: var(--tx-dim); }
  @media (max-width: 560px) { .map-view { padding: 8px; } }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): WorldMap component (calm-at-rest, living-icon hover)"
```

---

## Task 7: Wire the view switch into App.svelte

**Files:**
- Modify: `web/src/App.svelte` (full replacement below)

- [ ] **Step 1: Replace `web/src/App.svelte` with the nav + routed version**

```svelte
<script>
  import { onMount } from "svelte";
  import { presence } from "../../src/lib/stores.js";
  import { viewFromHash } from "./lib/view.js";
  import CardWall from "./CardWall.svelte";
  import WorldMap from "./WorldMap.svelte";

  $: vals = Object.values($presence);
  $: online = vals.filter((p) => p.online).length;
  $: racing = vals.filter((p) => p.online && p.screen === "RACING" && !p.final_time).length;

  let view = viewFromHash(typeof location !== "undefined" ? location.hash : "");
  onMount(() => {
    const sync = () => (view = viewFromHash(location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  });
</script>

<header class="top">
  <div class="brand"><span class="a">the</span><span class="b">kartoff</span></div>
  <nav class="nav">
    <a class="tab" class:on={view === "live"} href="#/">Live</a>
    <a class="tab" class:on={view === "map"} href="#/map">World Map</a>
  </nav>
  <div class="live"><span class="dot"></span><b>{online}</b>&nbsp;online&nbsp;·&nbsp;<b>{racing}</b>&nbsp;racing</div>
</header>
<main>
  {#if view === "map"}<WorldMap />{:else}<CardWall />{/if}
</main>

<style>
  .top{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:18px;
       padding:13px 22px;background:var(--panel);border-bottom:1px solid var(--bd);}
  .brand{display:flex;align-items:baseline;gap:2px;font-size:16px;font-weight:700;letter-spacing:.01em;}
  .brand .a{color:var(--tx);} .brand .b{color:var(--accent);}
  .nav{display:flex;gap:2px;}
  .tab{font-size:12.5px;font-weight:600;color:var(--tx-mut);text-decoration:none;
       padding:6px 11px;border-radius:var(--r);}
  .tab:hover{color:var(--tx);background:var(--panel-2);}
  .tab.on{color:var(--tx);background:var(--panel-2);box-shadow:inset 0 -2px 0 var(--accent);}
  .live{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:10.5px;letter-spacing:.09em;
        color:var(--tx-mut);text-transform:uppercase;}
  .live .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);position:relative;}
  .live .dot::after{content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);
                    animation:pulse 1.8s ease-out infinite;}
  @keyframes pulse{0%{transform:scale(1);opacity:.5;}100%{transform:scale(2.6);opacity:0;}}
  .live b{color:var(--tx);font-weight:600;}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web/src/App.svelte
git commit -m "feat(web): header nav + hash-routed Live/World Map views"
```

---

## Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the web unit tests**

Run: `cd web && npm test`
Expected: all tests pass, including `map.test.js`, `view.test.js` (and the existing `presenceClient`/`serve` tests).

- [ ] **Step 2: Type/template check**

Run: `cd web && npx svelte-check --threshold error`
Expected: 0 errors. (If `svelte-check` is not installed in `web/`, rely on Step 3's build, which fails on template/script errors.)

- [ ] **Step 3: Production build**

Run: `cd web && npm run build`
Expected: build succeeds; `web/dist/map/` contains `base.jpg`, `manifest.json`, `sprites/`.

- [ ] **Step 4: Manual smoke (dev server)**

Run: `cd web && npm run dev` then open the printed URL and visit `#/map`.
Confirm: the map renders framed in the dark UI; all 30 courses are present (Rainbow Road lower-center); hovering a course lifts/grows/glows it; the map sits calm at rest; nav switches Live ↔ World Map; layout holds when the window narrows. Stop the dev server when done.

- [ ] **Step 5: Final commit (if anything changed during verification)**

```bash
git add -A
git commit -m "chore(web): world-map SP1 verification fixups" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Map foundation view → Tasks 6, 7. ✓
- Production asset pipeline (2× res, slug-named sprites, no-inpaint base, ffmpeg decode, committed under web/) → Tasks 1, 2. ✓
- Course identity / labeling baked into manifest → Tasks 1, 2 (labels.json + nearest-center assignment), validated in Task 3. ✓
- Calm-at-rest + grow-dominant living-icon hover → Task 6. ✓
- OBS-feed framing in pbenguin tokens → Task 6 (`--feed-bg`, `--bd`, `--r`, vignette). ✓
- Hash routing (Live default, World Map additive) → Tasks 5, 7. ✓
- Layer seams for SP2 (territory) / SP3 (popups) → Task 6 placeholders. ✓
- Testing (build validation + frontend unit + build/manual) → Tasks 3, 4, 5, 8. ✓
- RR at (0.500, 0.734), width 0.089 → Task 2 constants. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; the `.territory`/`.popups` divs are intentional, documented seams, not unfinished work.

**Type/name consistency:** Helpers `hitStyle` / `spriteStyle` / `spriteUrl` / `baseUrl` / `manifestUrl` are defined in Task 4 and consumed identically in Task 6. `viewFromHash` defined in Task 5, used in Task 7. Manifest shape `{ base:{w,h}, courses:[{slug,name,hit{x,y,w,h},spr{x,y,w,h}}] }` is produced in Task 2, validated in Task 3, consumed in Task 6 — consistent. `data-slug` is set in Task 6 for SP3's later use.

**Note for executor:** Run asset/Python steps from the repo root; run `npm` steps from `web/`. ffmpeg is required only for Task 2 (asset regeneration).
