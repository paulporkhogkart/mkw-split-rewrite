// Frame-exact playback over chip sprite sheets (site pack manifest shape).
// Pure logic: callers own the DOM/rAF; tick(now) returns {anim, frame, bg}.
// Handoffs per the chip-site-pack spec: spawn once -> idle@0; flourish once ->
// idle@idle_resume for karts, idle@0 (hard cut) for chars; select() restarts
// spawn (interruptible) or idle when the combo has no spawn.

export function bgPos(index, cols, fw, fh) {
  const c = index % cols, r = Math.floor(index / cols);
  return `${c === 0 ? "0px" : `-${c * fw}px`} ${r === 0 ? "0px" : `-${r * fh}px`}`;
}

// Source rect of a frame in its sheet, for canvas drawImage rendering. Canvas is the
// jitter-free way to show a frame: a moving background-position gets pixel-snapped
// per paint under scaling (visible horizontal shake as columns cycle), while
// drawImage keeps a constant destination rect — nothing to re-snap.
export function frameRect(index, cols, fw, fh) {
  return { sx: (index % cols) * fw, sy: Math.floor(index / cols) * fh };
}

export function sheetCss(entry, anim, fw, fh) {
  const a = entry.anims[anim];
  return {
    width: `${fw}px`, height: `${fh}px`,
    backgroundSize: `${a.cols * fw}px ${a.rows * fh}px`,
  };
}

export function createChipPlayer({ entry, fps, fw, fh, now = () => performance.now() }) {
  let anim = "idle", start = 0, startFrame = 0, once = false, next = null;

  function set(name, opts = {}) {
    if (!entry.anims[name]) name = "idle";
    anim = name;
    start = now();
    startFrame = opts.startFrame ?? 0;
    once = !!opts.once;
    next = opts.next ?? null;
  }

  set("idle");
  return {
    select() { entry.anims.spawn ? set("spawn", { once: true, next: { anim: "idle", startFrame: 0 } })
                                 : set("idle"); },
    confirm() {
      const resume = entry.kart ? (entry.idle_resume ?? 0) : 0;
      set("flourish", { once: true, next: { anim: "idle", startFrame: resume } });
    },
    idle() { set("idle"); },
    tick(t = now()) {
      const a = entry.anims[anim];
      let frame = startFrame + Math.floor((t - start) * fps / 1000);
      if (once && frame >= a.frames) {
        const n = next; // play-once finished: hand off
        set(n.anim, { startFrame: n.startFrame });
        // re-enter as the new anim at its start frame, anchored at t
        start = t; frame = startFrame;
      } else if (!once) {
        frame %= a.frames;
      } else {
        frame = Math.min(frame, a.frames - 1);
      }
      const cur = entry.anims[anim];
      return { anim, frame, bg: bgPos(frame, cur.cols, fw, fh) };
    },
  };
}
