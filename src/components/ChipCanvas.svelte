<script>
  // Canvas sprite-sheet chip per the BINDING Playback rules (site-pack spec):
  // drawImage const-dest-rect (never background-position), ImageBitmap-only sources,
  // skip-draw-hold-last, ink ring baked in the draw. One rAF per canvas; five cards'
  // draws are texture copies, cheap.
  import { onDestroy } from "svelte";
  import { createChipPlayer, frameRect } from "../lib/chipSheet.js";

  export let manifest = null;
  export let bitmapCache = null;
  export let combo = null;
  export let action = null;      // "select" | "confirm" | "idle" | null
  export let actionSeq = 0;      // bump to re-fire the same action
  export let ink = "#101114";
  export let height = 92;

  let canvas, raf = 0, player = null, handle = null, lastSeq = -1, curCombo = null;
  let scratch = null;

  $: entry = manifest && combo ? manifest.combos[combo] : null;
  $: fw = manifest?.fw ?? 205;
  $: fh = manifest?.fh ?? 216;

  // Re-get the handle whenever the combo changes: chipStream handles are per-combo, and a
  // handle from an evicted combo stays not-ready forever (it's a snapshot of a dead entry).
  $: if (entry && bitmapCache && combo !== curCombo) {
    curCombo = combo;
    handle = bitmapCache.get(manifest, combo);
    player = createChipPlayer({ entry, fps: manifest.fps, fw, fh });
    lastSeq = -1; // a fresh combo consumes the pending action below
  }
  $: if (player && actionSeq !== lastSeq && action) {
    lastSeq = actionSeq;
    if (action === "select") player.select();
    else if (action === "confirm") player.confirm();
    else player.idle();
  }

  function draw() {
    raf = requestAnimationFrame(draw);
    if (!canvas || !player || !handle || !entry) return;
    const { anim, frame } = player.tick();
    if (!handle.ready(anim)) return;                 // skip + hold, never blank
    const bmp = handle.bitmaps[anim];
    const a = entry.anims[anim];
    const { sx, sy } = frameRect(frame, a.cols, fw, fh);
    if (!scratch) { scratch = document.createElement("canvas"); scratch.width = fw; scratch.height = fh; }
    const s = scratch.getContext("2d");
    // ink ring: 4-way ±1px stamp, source-in ink, frame on top (spec: == the lab's draw())
    s.clearRect(0, 0, fw, fh);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]])
      s.drawImage(bmp, sx, sy, fw, fh, dx, dy, fw, fh);
    s.globalCompositeOperation = "source-in";
    s.fillStyle = ink; s.fillRect(0, 0, fw, fh);
    s.globalCompositeOperation = "source-over";
    s.drawImage(bmp, sx, sy, fw, fh, 0, 0, fw, fh);
    const c = canvas.getContext("2d");
    c.imageSmoothingQuality = "high";
    c.clearRect(0, 0, fw, fh);
    c.drawImage(scratch, 0, 0);
  }
  // Arm the rAF loop once (raf stays truthy once running, so this never re-fires); the loop
  // then runs for the component's whole life, self-tolerating combo/canvas coming and going
  // (draw() bails to a no-op above when unbound, holding whatever was last painted).
  $: if (canvas && entry && !raf) raf = requestAnimationFrame(draw);
  onDestroy(() => cancelAnimationFrame(raf));
</script>

{#if entry}
  <canvas bind:this={canvas} width={fw} height={fh}
    style="height:{height}px;width:{(height * fw / fh).toFixed(1)}px" />
{/if}
