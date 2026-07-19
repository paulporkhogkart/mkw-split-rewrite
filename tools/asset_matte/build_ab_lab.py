"""A/B eye-test lab: encode sample combos across recipe variants, emit a static page.

  python tools/asset_matte/build_ab_lab.py --src D:\\kartoff\\asset_chips \\
      --out D:\\kartoff\\asset_chips\\ab_lab
Open <out>/index.html in a real browser (file://). Paul's pick locks the recipe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.asset_matte import build_site_pack as bsp              # noqa: E402
from tools.asset_matte import site_pack as sp                     # noqa: E402

# Pivoted to a 60fps candidate 2026-07-19 (Paul locked fps=60 on round 1; fps30
# kept as the rejected-reference cell). Change-one-thing around the candidate.
VARIANTS = [
    {"id": "candidate", "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 5},
    {"id": "fps30",     "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 5},
    {"id": "q75",       "scale": 0.2, "fps": 60, "quality": 75, "alpha_bits": 5},
    {"id": "q50",       "scale": 0.2, "fps": 60, "quality": 50, "alpha_bits": 5},
    {"id": "alpha8",    "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 8},
    {"id": "alpha4",    "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 4},
    {"id": "scale015",  "scale": 0.15, "fps": 60, "quality": 60, "alpha_bits": 5},
]
DEFAULT_COMBOS = ["baby_daisy__base__b_dasher", "bowser__base__bowser_bruiser",
                  "mario__base", "king_boo__base"]
INK_RING = ("filter:drop-shadow(1px 0 0 #101114) drop-shadow(-1px 0 0 #101114) "
            "drop-shadow(0 1px 0 #101114) drop-shadow(0 -1px 0 #101114)")


def _stepper_source() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "lib", "chipSheet.js")
    return re.sub(r"^export ", "", open(p, encoding="utf-8").read(), flags=re.M)


def render_html(combos, variants, manifests, sizes, stepper_js) -> str:
    stepper_js = stepper_js.replace("export ", "")
    data = json.dumps({"combos": combos,
                       "variants": [v["id"] for v in variants],
                       "manifests": manifests,
                       "sizes": {"|".join(k): v for k, v in sizes.items()}})
    # Preload hints double as a literal manifest of every sheet the page will
    # request (variant/combo/anim), so the eye-test never stalls on a cold fetch.
    preloads = "\n".join(
        f'<link rel="preload" as="image" '
        f'href="packs/{variant}/chips/{combo}__{anim}.webp">'
        for (variant, combo, anim) in sizes)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>chip pack A/B lab</title>
{preloads}
<style>
 body{{background:#0b0c0e;color:#f3f4f6;font:13px system-ui;padding:20px}}
 h2{{font-size:13px;margin:26px 0 8px;color:#9a9ca1}}
 .grid{{display:flex;gap:14px;flex-wrap:wrap}}
 .cell{{text-align:center}}
 /* Chips render on CANVAS: drawImage with an integral source rect into a constant
    destination rect, CSS-downscaled as one texture (same path as an <img>). Any
    moving background-position gets pixel-snapped per paint and jitters horizontally
    as columns cycle — measured: the sheet PIXELS are shift-free (dx spread 0.03px,
    same as animated webp), the shake was pure paint snapping. */
 /* Ring is applied per ring-mode from JS (baked = stamped inside the canvas draw;
    css = the card's drop-shadow filter chain; off = none). CSS drop-shadows on
    canvases that invalidate every frame force ~per-cell filter re-raster each
    frame — the suspected cause of the page compositing at ~22Hz. */
 .chip{{margin:0 auto;display:block}}
 .chip.cssring,img.cssring{{{INK_RING}}}
 .lbl{{color:#6b6d73;font-size:10px;margin-top:4px}}
 button{{font-size:10px;margin:2px}}
 .silwrap{{position:relative;width:120px;height:126px;background:#191a1d}}
 .silwrap img{{position:absolute;inset:0;width:100%;height:100%}}
</style></head><body>
<div id="root"></div>
<script>
{stepper_js}
const DATA = {data};
// One shared rAF ticks every player; cells register {{el, player, fw, fh, scaleCss}}.
const cells = [];
const DPR = window.devicePixelRatio || 1;
// Snap every canvas to the device-pixel grid: fractional layout positions (flex
// gaps, centering) resample the texture -> per-cell soft/crisp lottery.
function snapAll() {{
  for (const c of cells) c.el.style.transform = "";
  for (const c of cells) {{
    const r = c.el.getBoundingClientRect();
    const dx = Math.round(r.left * DPR) / DPR - r.left;
    const dy = Math.round(r.top * DPR) / DPR - r.top;
    c.el.style.transform = `translate(${{dx}}px, ${{dy}}px)`;
  }}
}}
window.addEventListener("resize", snapAll);
window.addEventListener("load", snapAll);
function addCell(root, variant, combo, cssH) {{
  const man = DATA.manifests[variant]; if (!man || !man.combos[combo]) return;
  const entry = man.combos[combo];
  const wrap = document.createElement("div"); wrap.className = "cell";
  const s = cssH / man.fh;
  // Canvas backing at DEVICE pixels of the display size: drawImage does one
  // high-quality resample per frame and a baked ring stamps at true device px
  // (matches what the card integration will do). Display size is derived FROM
  // the integral backing (W/dpr) so texels map 1:1 to device pixels — a canvas
  // sized/positioned on fractional device px gets resampled and looks soft,
  // with per-cell variation set by accidental layout phase (see snapAll).
  const el = document.createElement("canvas"); el.className = "chip";
  el.width = Math.round(man.fw * s * DPR); el.height = Math.round(man.fh * s * DPR);
  el.style.width = el.width / DPR + "px"; el.style.height = el.height / DPR + "px";
  // offscreen scratch for the baked ring (silhouette union, tinted)
  const oc = document.createElement("canvas");
  oc.width = el.width; oc.height = el.height;
  // Sheets pinned as ImageBitmaps: a bare Image's decoded pixels live in Chrome's
  // evictable decode cache, and with ~100 large sheets on this page drawImage kept
  // triggering synchronous re-decodes (global jank). createImageBitmap decodes ONCE
  // into a GPU-backed bitmap; draws are pure texture copies. Until a bitmap is
  // ready a draw skips, holding the previous canvas frame -> still no blank flash.
  const bmps = {{}};
  for (const n of Object.keys(entry.anims)) {{
    const im = new Image();
    im.src = `packs/${{variant}}/chips/${{combo}}__${{n}}.webp`;
    im.decode().then(() => createImageBitmap(im))
      .then((b) => {{ bmps[n] = b; }})
      .catch(() => console.warn("sheet decode failed", im.src));
  }}
  const player = createChipPlayer({{entry, fps: man.fps, fw: man.fw, fh: man.fh}});
  const ctx = el.getContext("2d"), octx = oc.getContext("2d");
  ctx.imageSmoothingQuality = "high"; octx.imageSmoothingQuality = "high";
  cells.push({{el, ctx, oc, octx, bmps, player, man, entry, last: "", variant, combo}});
  const kb = (n, f) => {{ const b = document.createElement("button"); b.textContent = n; b.onclick = f; return b; }};
  wrap.append(el, kb("SELECT", () => player.select()), kb("CONFIRM", () => player.confirm()));
  const idleKB = (DATA.sizes[[variant, combo, "idle"].join("|")] / 1024) | 0;
  const lbl = document.createElement("div"); lbl.className = "lbl";
  lbl.textContent = `${{variant}} · idle ${{idleKB}}KB`;
  wrap.append(lbl); root.append(wrap);
}}
// ---- frame-time profiler (HUD top-right; "copy stats" -> paste back for diagnosis) ----
const prof = {{ raf: [], cost: [], holds30: [], holds60: [], lastT: 0, h30: 0, h60: 0 }};
function pushCap(arr, v, cap) {{ arr.push(v); if (arr.length > cap) arr.shift(); }}
function pct(arr, p) {{
  if (!arr.length) return 0;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
}}
function hist(arr) {{
  const b = {{}};
  for (const v of arr) {{ const k = Math.round(v / 8.333) * 8.3; b[k] = (b[k] || 0) + 1; }}
  return Object.keys(b).sort((a, b) => a - b).map((k) => `${{Number(k).toFixed(1)}}ms×${{b[k]}}`).join(" ");
}}
function statsText() {{
  const med = pct(prof.raf, 0.5);
  const long = prof.raf.filter((v) => v > med * 1.5).length;
  return [
    `display: median rAF ${{med.toFixed(2)}}ms (~${{(1000 / med).toFixed(0)}}Hz), ` +
    `p95 ${{pct(prof.raf, 0.95).toFixed(2)}}ms, max ${{Math.max(0, ...prof.raf).toFixed(1)}}ms, ` +
    `long-frames ${{long}}/${{prof.raf.length}}`,
    `tick cost: p50 ${{pct(prof.cost, 0.5).toFixed(2)}}ms, p95 ${{pct(prof.cost, 0.95).toFixed(2)}}ms, ` +
    `max ${{Math.max(0, ...prof.cost).toFixed(2)}}ms`,
    `candidate 30fps frame holds (want ~33.3): ${{hist(prof.holds30)}}`,
    `fps60 frame holds (want ~16.7): ${{hist(prof.holds60)}}`,
  ].join("\\n");
}}
const hud = document.createElement("pre");
hud.style.cssText = "position:fixed;top:6px;right:8px;z-index:9;background:#191a1dee;" +
  "color:#9a9ca1;font-size:10px;padding:6px 8px;margin:0;max-width:520px;white-space:pre-wrap";
const copyBtn = document.createElement("button");
copyBtn.textContent = "copy stats";
copyBtn.onclick = () => {{
  const t = statsText();
  console.log(t);
  navigator.clipboard ? navigator.clipboard.writeText(t) : window.prompt("copy:", t);
}};
document.body.append(hud, copyBtn);
copyBtn.style.cssText = "position:fixed;top:6px;right:8px;z-index:10;font-size:10px";
hud.style.paddingTop = "24px";
for (const [i, m] of ["baked", "css", "off"].entries()) {{
  const b = document.createElement("button"); b.className = "ringbtn";
  b.textContent = "ring: " + m; b.onclick = () => setRing(m);
  b.style.cssText = `position:fixed;top:6px;right:${{90 + i * 78}}px;z-index:10;font-size:10px`;
  document.body.append(b);
}}
setInterval(() => {{ hud.textContent = statsText(); }}, 500);

// ring modes: baked (stamped in-canvas, the production candidate) | css (card's
// filter chain, suspected 22Hz culprit) | off (control). Switching bumps ringGen
// so every cell force-redraws.
let ringMode = "baked", ringGen = 0;
function setRing(mode) {{
  ringMode = mode; ringGen++;
  for (const c of cells) c.el.classList.toggle("cssring", mode === "css");
  document.querySelectorAll("img.animref").forEach((im) =>
    im.classList.toggle("cssring", mode === "css"));
  document.querySelectorAll(".ringbtn").forEach((b) =>
    b.style.fontWeight = b.textContent.endsWith(mode) ? "bold" : "normal");
}}
const RING_OFFS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
function draw(c, bmp, r) {{
  const W = c.el.width, H = c.el.height;
  c.ctx.clearRect(0, 0, W, H);
  if (ringMode === "baked") {{
    const o = c.octx, d = Math.max(1, Math.round(window.devicePixelRatio || 1));
    o.globalCompositeOperation = "source-over";
    o.clearRect(0, 0, W, H);
    for (const [dx, dy] of RING_OFFS)
      o.drawImage(bmp, r.sx, r.sy, c.man.fw, c.man.fh, dx * d, dy * d, W, H);
    o.globalCompositeOperation = "source-in";
    o.fillStyle = "#101114";
    o.fillRect(0, 0, W, H);
    c.ctx.drawImage(c.oc, 0, 0);
  }}
  c.ctx.drawImage(bmp, r.sx, r.sy, c.man.fw, c.man.fh, 0, 0, W, H);
}}
function tickAll(t) {{
  if (prof.lastT) pushCap(prof.raf, t - prof.lastT, 600);
  prof.lastT = t;
  const t0 = performance.now();
  for (const c of cells) {{
    const st = c.player.tick(t);
    const key = st.anim + ":" + st.frame + ":" + ringGen;
    if (key === c.last) continue;                       // same frame, skip redraw
    const bmp = c.bmps[st.anim];
    if (!bmp) continue;                                 // bitmap not ready: hold last frame
    const a = c.entry.anims[st.anim];
    draw(c, bmp, frameRect(st.frame, a.cols, c.man.fw, c.man.fh));
    c.last = key;
    // probe: displayed-frame hold durations for the first candidate and fps60 cells
    if (c === probe30) {{ if (prof.h30) pushCap(prof.holds30, t - prof.h30, 240); prof.h30 = t; }}
    if (c === probe60) {{ if (prof.h60) pushCap(prof.holds60, t - prof.h60, 240); prof.h60 = t; }}
  }}
  pushCap(prof.cost, performance.now() - t0, 600);
  requestAnimationFrame(tickAll);
}}
const root = document.getElementById("root");
for (const combo of DATA.combos) {{
  const h = document.createElement("h2"); h.textContent = combo; root.append(h);
  const g = document.createElement("div"); g.className = "grid"; root.append(g);
  for (const v of DATA.variants) addCell(g, v, combo, 112);
  const ref = document.createElement("div"); ref.className = "cell";
  ref.innerHTML = `<img class="animref" style="height:112px" src="animref/${{combo}}__idle.webp"><div class="lbl">animref (animated webp)</div>`;
  g.append(ref);
  const h2 = document.createElement("h2"); h2.textContent = combo + " · candidate at card sizes"; root.append(h2);
  const g2 = document.createElement("div"); g2.className = "grid"; root.append(g2);
  addCell(g2, "candidate", combo, 92); addCell(g2, "candidate", combo, 76);
}}
const probe30 = cells.find((c) => c.variant === "candidate");
const probe60 = cells.find((c) => c.variant === "fps60");
setRing("baked");
snapAll();
requestAnimationFrame(tickAll);
</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--combos", nargs="*", default=DEFAULT_COMBOS)
    args = ap.parse_args(argv)

    plan = bsp.plan_combos(args.src)
    combos = [c for c in args.combos if c in plan] or sys.exit("no requested combos exist")
    for c in args.combos:
        if c not in plan:
            print(f"warn: {c} not in masters, skipped", file=sys.stderr)

    manifests, sizes = {}, {}
    for v in VARIANTS:
        vout = os.path.join(args.out, "packs", v["id"])
        rc = bsp.main(["--src", args.src, "--out", vout, "--scale", str(v["scale"]),
                       "--fps", str(v["fps"]), "--quality", str(v["quality"]),
                       "--alpha-bits", str(v["alpha_bits"]), "--workers", "4",
                       "--only", *combos])
        if rc:
            return rc
        man = json.load(open(os.path.join(vout, "chips", "manifest.json"), encoding="utf-8"))
        manifests[v["id"]] = man
        for c in combos:
            for anim in man["combos"].get(c, {}).get("anims", {}):
                p = os.path.join(vout, "chips", f"{c}__{anim}.webp")
                sizes[(v["id"], c, anim)] = os.path.getsize(p)

    # animated-webp reference at the candidate recipe (smoothness comparison)
    from PIL import Image
    ref_dir = os.path.join(args.out, "animref")
    os.makedirs(ref_dir, exist_ok=True)
    cand = VARIANTS[0]
    step = sp.subsample_step(cand["fps"])
    for c in combos:
        d = os.path.join(args.src, "matte", f"{c}__idle_frames")
        files = sorted(f for f in os.listdir(d) if f.endswith(".png"))[::step]
        fw, fh = sp.encode_size(*Image.open(os.path.join(d, files[0])).size, cand["scale"])
        frames = [sp.quant_alpha(sp.premul_resize(Image.open(os.path.join(d, f)).convert("RGBA"),
                                                  (fw, fh)), cand["alpha_bits"]) for f in files]
        frames[0].save(os.path.join(ref_dir, f"{c}__idle.webp"), save_all=True,
                       append_images=frames[1:], duration=round(1000 / cand["fps"]),
                       loop=0, quality=cand["quality"], method=4)

    # placeholder sils for visual comparison (best effort)
    sil_dir = os.path.join(args.out, "placeholder_sil")
    os.makedirs(sil_dir, exist_ok=True)
    for k in range(4):
        rel = f"docs/design/site-redesign/sil/paul__idle_k{k}.png"
        try:
            data = subprocess.run(["git", "show", f"site-redesign-p1:{rel}"],
                                  capture_output=True, check=True).stdout
            open(os.path.join(sil_dir, f"paul__idle_k{k}.png"), "wb").write(data)
        except subprocess.CalledProcessError:
            print(f"warn: placeholder sil {rel} unavailable", file=sys.stderr)
            break

    html = render_html(combos, VARIANTS, manifests, sizes, _stepper_source())
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"lab ready: {os.path.join(args.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
