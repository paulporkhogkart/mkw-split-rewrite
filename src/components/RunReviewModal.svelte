<script>
  // RunReviewModal.svelte — review-and-complete a detected run before it uploads.
  //
  // A run is "complete enough" to upload once it has its identity + total:
  //   • every run  → course, character, kart
  //   • finished   → + total time
  // Per-lap splits/coins/mushrooms are best-effort: never required, never hand-entered.
  // PB-ness is re-checked live against the picked course (pbBest), so a PB whose course
  // wasn't auto-detected is still recognised; when its laps WERE captured the grid is
  // shown for an optional once-over (non-blocking).
  // Anything missing holds the run here for the user to fill (or Discard) — nothing
  // uploads until they act. The popup is deliberately part of the app's existing
  // OBS-style system (same tokens / modal idiom), not a separate look.
  //
  // Stateless wrt the queue: shows ONE run; the parent advances on submit/discard.
  //   props : run, isPb, pbBest, options{courses,characters,karts,costumes}, queueIndex, queueCount
  //   events: submit(filledRun) · discard({attempt_id})
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { fade, scale } from "svelte/transition";
  import { quintOut } from "svelte/easing";
  import snd from "../assets/run-review.wav";
  import { isValidTime, parseTimeMs, isPbTime, isValidInt, isValidCount, buildLaps, lapsComplete } from "../lib/runReview.js";

  export let run;                       // { attempt_id, status, course, character, kart, costume, total_time, total_laps, laps[] }
  export let isPb = false;
  export let options = { courses: [], characters: [], karts: [], costumes: [], costumesByCharacter: {} };
  export let queueIndex = 0;            // 0-based position in the review queue
  export let queueCount = 1;            // total runs awaiting review
  export let playSound = true;
  export let isGhost = false;      // ghost-imported run: needs an extra submit confirm
  // Live PB lookup: (course) => Promise<bestMs|null>. Asked whenever the chosen course
  // changes, so a run whose course wasn't auto-detected can still be recognised as a
  // PB once you pick it. Defaults to "no record on file" when not supplied.
  export let pbBest = async () => null;

  const dispatch = createEventDispatcher();

  // ── Working copy, (re)loaded only when the run identity changes ────────────────
  let course, character, kart, costume, totalTime, laps;
  let loadedId = null;
  let confirmingDiscard = false;     // two-step Discard: first click asks "are you sure?"
  let confirmingSubmit = false;    // two-step submit for ghost imports
  let dialogEl, firstInvalidEl;

  $: if (run && run.attempt_id !== loadedId) {
    loadedId  = run.attempt_id;
    confirmingDiscard = false;       // reset the prompt when the queue advances
    confirmingSubmit = false;
    liveBest = undefined; pbQueryKey = null;   // re-evaluate PB for the new run
    course    = run.course    ?? "";
    character = run.character ?? "";
    kart      = run.kart      ?? "";
    costume   = run.costume   ?? "Base";
    totalTime = run.total_time ?? "";
    const n = run.total_laps ?? (run.laps?.length ?? 0);
    const seenT = new Map((run.laps ?? []).map((l) => [l.lap, l.time_str ?? ""]));
    const seenC = new Map((run.laps ?? []).map((l) => [l.lap, l.coins]));
    const seenS = new Map((run.laps ?? []).map((l) => [l.lap, l.shrooms]));
    // coins/shrooms stay strings for the inputs; 0 must render as "0", null as "".
    const numStr = (x) => (x == null ? "" : String(x));
    laps = Array.from({ length: n }, (_, i) => ({
      lap: i + 1,
      time:    seenT.get(i + 1) ?? "",
      coins:   numStr(seenC.get(i + 1)),
      shrooms: numStr(seenS.get(i + 1)),
    }));
  }

  // ── Live PB detection ─────────────────────────────────────────────────────────
  // A run whose course wasn't auto-detected reaches here flagged as a non-PB (the
  // engine couldn't look it up). Re-evaluate against the cached best for whatever
  // course the user picks, so the popup recognises and collects a real PB.
  let liveBest;            // undefined = not looked up; null = no record; number = best ms
  let pbQueryKey = null;   // course we last queried (don't re-query on every keystroke)
  async function lookupPb(c) {
    pbQueryKey = c;
    if (!c) { liveBest = undefined; return; }
    try { liveBest = await pbBest(c); } catch { liveBest = undefined; }
  }

  // ── What this run needs, and what's still missing ─────────────────────────────
  $: isFinished  = run?.status === "finished";
  $: needTotal   = isFinished;

  $: totalMs     = isValidTime(totalTime) ? parseTimeMs(totalTime) : null;
  $: pbCourse    = isFinished && course && totalMs != null ? course : null;
  $: if (pbCourse !== pbQueryKey) lookupPb(pbCourse);
  $: isPbLive    = isFinished
       ? (liveBest === undefined ? !!isPb : isPbTime(totalMs, liveBest))
       : false;

  // The per-lap grid shows only for a PB whose laps were FULLY captured (all
  // total_laps). Splits are all-or-nothing - one untracked lap drops the whole set
  // (the coin deltas / mushroom counts for the rest would be meaningless), so a
  // partial capture shows no grid and uploads no per-lap data.
  $: totalLaps   = run?.total_laps ?? (run?.laps?.length ?? 0);
  $: needSplits  = isFinished && isPbLive && lapsComplete(run?.laps, totalLaps);

  $: missCourse  = !course;
  $: missChar    = !character;
  $: missKart    = !kart;
  $: missTotal   = needTotal && !isValidTime(totalTime);
  // Only identity + total gate submit; per-lap data never blocks.
  $: canSubmit   = !missCourse && !missChar && !missKart && !missTotal;

  // Costume is optional; restricted to the selected character's valid costumes
  // (engine KNOWN_COSTUMES). "Base" (no costume) is always first; an unknown or
  // costume-less character yields just "Base".
  $: costumeOptions = ["Base", ...((options.costumesByCharacter?.[character]) ?? []).filter((c) => c !== "Base")];
  // If the current costume isn't valid for the selected character, fall back to Base.
  $: if (costume && !costumeOptions.includes(costume)) costume = "Base";

  function submit() {
    if (!canSubmit) return;
    if (isGhost && !confirmingSubmit) { confirmingSubmit = true; return; }
    dispatch("submit", {
      attempt_id: run.attempt_id,
      course, character, kart, costume,
      total_time: needTotal ? totalTime.trim() : (run.total_time ?? null),
      // The grid (when shown) holds a full captured set; forward it, else forward
      // whatever the engine captured. A partial/edited-incomplete set is dropped
      // wholesale at the upload boundary (Rust laps_complete), never partially kept.
      laps: needSplits ? buildLaps(laps) : (run.laps ?? []),
    });
  }
  const discard = () => dispatch("discard", { attempt_id: run.attempt_id });

  // ── Draggable: grab the header to move the dialog aside, so it can't cover the
  // feed / info you're reading to fill the form. Handlers live on the dialog (which
  // carries role="dialog", so no a11y noise), but a drag only STARTS from the header -
  // the body inputs stay fully clickable. Resets to centre when the popup reopens.
  let dragX = 0, dragY = 0, dragging = false;
  let _sx = 0, _sy = 0, _bx = 0, _by = 0;
  function startDrag(e) {
    if (e.button !== 0 || !(e.target instanceof Element) || !e.target.closest(".rv-head")) return;
    dragging = true;
    _sx = e.clientX; _sy = e.clientY; _bx = dragX; _by = dragY;
    dialogEl?.setPointerCapture?.(e.pointerId);
  }
  function onDrag(e) {
    if (!dragging) return;
    let nx = _bx + (e.clientX - _sx);
    let ny = _by + (e.clientY - _sy);
    if (dialogEl) {                                   // keep >=48px on screen so it can't be lost
      const r = dialogEl.getBoundingClientRect();
      const limX = Math.max(0, (window.innerWidth  + r.width)  / 2 - 48);
      const limY = Math.max(0, (window.innerHeight + r.height) / 2 - 48);
      nx = Math.max(-limX, Math.min(limX, nx));
      ny = Math.max(-limY, Math.min(limY, ny));
    }
    dragX = nx; dragY = ny;
  }
  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    try { dialogEl?.releasePointerCapture?.(e.pointerId); } catch (_) { /* wasn't captured */ }
  }

  onMount(async () => {
    if (playSound) { try { await new Audio(snd).play(); } catch (_) { /* autoplay blocked */ } }
    await tick();
    (firstInvalidEl ?? dialogEl)?.focus?.();
  });
</script>

<div class="rv-backdrop" transition:fade={{ duration: 120 }}>
  <div
    class="rv-dialog"
    bind:this={dialogEl}
    tabindex="-1"
    role="dialog"
    aria-modal="true"
    aria-labelledby="rv-title"
    in:scale={{ duration: 170, start: 0.97, opacity: 0, easing: quintOut }}
    style={dragX || dragY ? `transform: translate(${dragX}px, ${dragY}px)` : ""}
    on:pointerdown={startDrag}
    on:pointermove={onDrag}
    on:pointerup={endDrag}
    on:pointercancel={endDrag}
  >
    <header class="rv-head">
      <div class="rv-head-l">
        <h2 id="rv-title" class="rv-head-title">Run needs review</h2>
        {#if isPbLive}<span class="rv-pb" title="This run is a new personal best">PB</span>{/if}
        {#if isGhost}<span class="rv-pb" title="Imported from an in-game ghost">GHOST</span>{/if}
      </div>
      {#if queueCount > 1}
        <span class="rv-queue" title="Runs awaiting review">{queueIndex + 1}<span class="rv-queue-sep">/</span>{queueCount}</span>
      {/if}
    </header>

    <div class="rv-body">
      <!-- Identity — required on every run, finished or reset -->
      <label class="rv-row" class:rv-miss={missCourse}>
        <span class="rv-label">Course{#if missCourse}<i class="rv-flag" aria-label="missing">!</i>{/if}</span>
        <select class="rv-ctrl" bind:value={course} bind:this={firstInvalidEl} class:rv-ctrl-miss={missCourse}>
          <option value="" disabled>Select course…</option>
          {#each options.courses as c}<option value={c}>{c}</option>{/each}
        </select>
      </label>

      <label class="rv-row" class:rv-miss={missChar}>
        <span class="rv-label">Character{#if missChar}<i class="rv-flag" aria-label="missing">!</i>{/if}</span>
        <select class="rv-ctrl" bind:value={character} class:rv-ctrl-miss={missChar}>
          <option value="" disabled>Select character…</option>
          {#each options.characters as c}<option value={c}>{c}</option>{/each}
        </select>
      </label>

      <label class="rv-row" class:rv-miss={missKart}>
        <span class="rv-label">Kart{#if missKart}<i class="rv-flag" aria-label="missing">!</i>{/if}</span>
        <select class="rv-ctrl" bind:value={kart} class:rv-ctrl-miss={missKart}>
          <option value="" disabled>Select kart…</option>
          {#each options.karts as k}<option value={k}>{k}</option>{/each}
        </select>
      </label>

      <label class="rv-row">
        <span class="rv-label rv-label-opt">Costume</span>
        <select class="rv-ctrl" bind:value={costume}>
          {#each costumeOptions as c}<option value={c}>{c}</option>{/each}
        </select>
      </label>

      {#if needTotal}
        <div class="rv-divider"></div>
        <label class="rv-row" class:rv-miss={missTotal}>
          <span class="rv-label">Total{#if missTotal}<i class="rv-flag" aria-label="missing">!</i>{/if}</span>
          <input class="rv-ctrl rv-time" class:rv-ctrl-miss={missTotal}
                 bind:value={totalTime} placeholder="0:00.000" spellcheck="false" autocomplete="off" />
        </label>
      {/if}

      {#if needSplits}
        <div class="rv-divider"></div>
        <div class="rv-laphead">
          <span class="rv-lh rv-lh-lap">Lap</span>
          <span class="rv-lh">Time</span>
          <span class="rv-lh rv-lh-num">Coins</span>
          <span class="rv-lh rv-lh-num">Mush</span>
        </div>
        {#each laps as lap (lap.lap)}
          <div class="rv-laprow">
            <span class="rv-lap-no">{lap.lap}</span>
            <input class="rv-ctrl rv-time" class:rv-ctrl-miss={!isValidTime(lap.time)}
                   bind:value={lap.time} placeholder="0:00.000" spellcheck="false" autocomplete="off" />
            <input class="rv-ctrl rv-time rv-num" class:rv-ctrl-miss={!isValidInt(lap.coins)}
                   bind:value={lap.coins} placeholder="0" inputmode="numeric" spellcheck="false" autocomplete="off" />
            <input class="rv-ctrl rv-time rv-num" class:rv-ctrl-miss={!isValidCount(lap.shrooms)}
                   bind:value={lap.shrooms} placeholder="0" inputmode="numeric" spellcheck="false" autocomplete="off" />
          </div>
        {/each}
      {/if}
    </div>

    <footer class="rv-foot">
      {#if confirmingDiscard}
        <!-- Confirm step: Cancel sits where "Discard run" was (so a stray double-click
             backs out), the destructive action is pushed to the far right. -->
        <button class="rv-btn rv-btn-ghost" on:click={() => (confirmingDiscard = false)}>Cancel</button>
        <div class="rv-foot-right">
          <span class="rv-hint">This run won't be uploaded.</span>
          <button class="rv-btn rv-btn-danger" on:click={discard}>Discard</button>
        </div>
      {:else}
        <button class="rv-btn rv-btn-ghost" on:click={() => (confirmingDiscard = true)}>Discard run</button>
        <div class="rv-foot-right">
          <span class="rv-hint">
            {confirmingSubmit ? "Submit this as one of your runs?"
              : canSubmit ? "Ready to submit" : "Fill the flagged fields to submit"}
          </span>
          {#if confirmingSubmit}
            <button class="rv-btn rv-btn-ghost" on:click={() => (confirmingSubmit = false)}>Cancel</button>
          {/if}
          <button class="rv-btn rv-btn-primary" on:click={submit} disabled={!canSubmit}>
            {confirmingSubmit ? "Yes, submit" : "Submit"}
          </button>
        </div>
      {/if}
    </footer>
  </div>
</div>

<style>
  .rv-backdrop {
    position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, .62);
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }
  .rv-dialog {
    width: 100%; max-width: 392px; max-height: calc(100vh - 48px);
    display: flex; flex-direction: column; overflow: hidden;
    background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r);
    box-shadow: 0 16px 44px rgba(0, 0, 0, .5);
    outline: none;
  }

  /* Header — left-aligned title + status, optional queue counter on the right */
  .rv-head {
    display: flex; align-items: center; justify-content: space-between; gap: .75rem;
    padding: .6rem .85rem;
    border-bottom: 1px solid var(--bd-soft);
    cursor: move; user-select: none; touch-action: none;   /* drag handle */
  }
  .rv-head-l { display: flex; align-items: center; gap: .45rem; min-width: 0; }
  .rv-head-title { font-size: .82rem; font-weight: 600; color: var(--tx); letter-spacing: .01em; }
  /* New-PB chip: subtle outlined accent (functional highlight, not a fill). */
  .rv-pb {
    flex-shrink: 0; font-size: .6rem; font-weight: 700; letter-spacing: .04em;
    color: var(--accent); border: 1px solid var(--accent);
    border-radius: var(--r-sm); padding: .06rem .32rem; line-height: 1.3;
  }
  .rv-queue {
    flex-shrink: 0; font-size: .66rem; color: var(--tx-mut);
    background: var(--panel-2); border: 1px solid var(--bd);
    border-radius: var(--r-sm); padding: .12rem .38rem; line-height: 1.4;
  }
  .rv-queue-sep { color: var(--tx-dim); margin: 0 .12em; }

  /* Body — label/control rows */
  .rv-body {
    padding: .6rem .85rem; overflow-y: auto;
    display: flex; flex-direction: column; gap: .34rem;
  }
  .rv-row {
    display: grid; grid-template-columns: 78px 1fr; align-items: center; gap: .55rem;
  }
  .rv-label {
    font-size: .7rem; color: var(--tx-mut);
    display: inline-flex; align-items: center; gap: .3rem;
  }
  .rv-label-opt { color: var(--tx-dim); }

  /* The "missing required" affordance: a small amber marker + amber field border.
     Functional color only — no decorative tints (matches the app's restraint). */
  .rv-flag {
    font-style: normal; font-weight: 700; font-size: .62rem; line-height: 1;
    color: var(--warn);
  }

  .rv-ctrl {
    width: 100%; min-width: 0;
    /* background-color (not the `background` shorthand) so the app-wide <select>
       chevron image from theme.css survives on the dropdowns. */
    background-color: var(--panel-2); color: var(--tx);
    border: 1px solid var(--bd); border-radius: var(--r-sm);
    font-family: var(--ui); font-size: .74rem;
    padding: .26rem .5rem;
    transition: border-color .12s, background-color .12s;
  }
  /* Reserve room for that chevron so the course/character/kart text never runs under it. */
  select.rv-ctrl { padding-right: 1.6rem; cursor: pointer; }
  .rv-ctrl:focus { outline: none; border-color: var(--accent); }
  .rv-ctrl-miss { border-color: var(--warn); }
  .rv-ctrl-miss:focus { border-color: var(--warn); }

  /* Time fields read as data: monospace + tabular figures so digits line up. */
  .rv-time {
    font-family: var(--mono); letter-spacing: .01em; font-variant-numeric: tabular-nums;
  }
  .rv-time::placeholder { color: var(--tx-dim); }

  /* Per-lap PB grid: lap no. | time | coins | mush. Columns line up with the header. */
  .rv-laphead, .rv-laprow {
    display: grid;
    grid-template-columns: 30px 1fr 52px 52px;
    align-items: center; gap: .4rem;
  }
  .rv-laphead { margin-top: .05rem; }
  .rv-lh { font-size: .6rem; color: var(--tx-dim); text-transform: uppercase; letter-spacing: .04em; }
  .rv-lh-lap { text-align: left; }
  .rv-lh-num { text-align: right; }
  .rv-lap-no {
    font-size: .7rem; color: var(--tx-dim);
    font-variant-numeric: tabular-nums; text-align: center;
  }
  .rv-num { text-align: right; padding-right: .45rem; }

  .rv-divider { height: 1px; background: var(--bd-soft); margin: .25rem 0 .1rem; }

  /* Footer — hint on the left, actions on the right */
  .rv-foot {
    display: flex; align-items: center; justify-content: space-between; gap: .6rem;
    padding: .55rem .85rem; border-top: 1px solid var(--bd-soft);
  }
  .rv-hint { font-size: .64rem; color: var(--tx-dim); }
  .rv-foot-right { display: flex; align-items: center; gap: .55rem; flex-shrink: 0; }

  .rv-btn {
    font-family: inherit; font-size: .72rem; cursor: pointer;
    padding: .26rem .8rem; border-radius: var(--r);
    border: 1px solid var(--bd); background: var(--panel-2); color: var(--tx-mut);
    transition: background-color .12s, border-color .12s, color .12s, opacity .12s;
  }
  .rv-btn-ghost:hover { background: var(--raised); color: var(--tx); }
  .rv-btn-primary {
    background: var(--accent-bg); border-color: var(--accent); color: var(--tx);
  }
  .rv-btn-primary:hover:not(:disabled) { background: var(--raised); }
  .rv-btn-primary:disabled { opacity: .4; cursor: default; }
  /* Destructive confirm — functional --err only, no fill. */
  .rv-btn-danger { border-color: var(--err); color: var(--err); background: var(--panel-2); }
  .rv-btn-danger:hover { background: var(--raised); }

  @media (prefers-reduced-motion: reduce) {
    .rv-dialog { animation: none; }
  }
</style>
