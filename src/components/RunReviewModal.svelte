<script>
  // RunReviewModal.svelte — review-and-complete a detected run before it uploads.
  //
  // A finished/reset run reaches the outbox only once it's "complete enough":
  //   • every run  → course, character, kart
  //   • finished   → + total time
  //   • finished PB → + every lap split (count == total_laps)
  // Anything missing holds the run here for the user to fill (or Discard) — nothing
  // uploads until they act. The popup is deliberately part of the app's existing
  // OBS-style system (same tokens / modal idiom), not a separate look.
  //
  // Stateless wrt the queue: shows ONE run; the parent advances on submit/discard.
  //   props : run, isPb, options{courses,characters,karts,costumes}, queueIndex, queueCount
  //   events: submit(filledRun) · discard({attempt_id})
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { fade, scale } from "svelte/transition";
  import { quintOut } from "svelte/easing";
  import snd from "../assets/run-review.wav";

  export let run;                       // { attempt_id, status, course, character, kart, costume, total_time, total_laps, laps[] }
  export let isPb = false;
  export let options = { courses: [], characters: [], karts: [], costumes: [] };
  export let queueIndex = 0;            // 0-based position in the review queue
  export let queueCount = 1;            // total runs awaiting review
  export let playSound = true;

  const dispatch = createEventDispatcher();
  const TIME_RE = /^\d+:\d{2}\.\d{3}$/;
  const validTime = (t) => TIME_RE.test((t ?? "").trim());

  // ── Working copy, (re)loaded only when the run identity changes ────────────────
  let course, character, kart, costume, totalTime, laps;
  let loadedId = null;
  let confirmingDiscard = false;     // two-step Discard: first click asks "are you sure?"
  let dialogEl, firstInvalidEl;

  $: if (run && run.attempt_id !== loadedId) {
    loadedId  = run.attempt_id;
    confirmingDiscard = false;       // reset the prompt when the queue advances
    course    = run.course    ?? "";
    character = run.character ?? "";
    kart      = run.kart      ?? "";
    costume   = run.costume   ?? "Base";
    totalTime = run.total_time ?? "";
    const n = run.total_laps ?? (run.laps?.length ?? 0);
    const seen = new Map((run.laps ?? []).map((l) => [l.lap, l.time_str ?? ""]));
    laps = Array.from({ length: n }, (_, i) => ({ lap: i + 1, time: seen.get(i + 1) ?? "" }));
  }

  // ── What this run needs, and what's still missing ─────────────────────────────
  $: isFinished  = run?.status === "finished";
  $: needTotal   = isFinished;
  $: needSplits  = isFinished && isPb;

  $: missCourse  = !course;
  $: missChar    = !character;
  $: missKart    = !kart;
  $: missTotal   = needTotal && !validTime(totalTime);
  $: badLaps     = needSplits ? (laps ?? []).filter((l) => !validTime(l.time)).map((l) => l.lap) : [];

  $: canSubmit = !missCourse && !missChar && !missKart && !missTotal && badLaps.length === 0;

  function submit() {
    if (!canSubmit) return;
    dispatch("submit", {
      attempt_id: run.attempt_id,
      course, character, kart, costume,
      total_time: needTotal ? totalTime.trim() : (run.total_time ?? null),
      laps: needSplits ? laps.map((l) => ({ lap: l.lap, time_str: l.time.trim() }))
                       : (run.laps ?? []),
    });
  }
  const discard = () => dispatch("discard", { attempt_id: run.attempt_id });

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
  >
    <header class="rv-head">
      <h2 id="rv-title" class="rv-head-title">Run needs review</h2>
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
          {#each (options.costumes.length ? options.costumes : ["Base"]) as c}<option value={c}>{c}</option>{/each}
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
        {#each laps as lap (lap.lap)}
          <label class="rv-row" class:rv-miss={!validTime(lap.time)}>
            <span class="rv-label rv-label-lap">Lap {lap.lap}{#if !validTime(lap.time)}<i class="rv-flag" aria-label="missing">!</i>{/if}</span>
            <input class="rv-ctrl rv-time" class:rv-ctrl-miss={!validTime(lap.time)}
                   bind:value={lap.time} placeholder="0:00.000" spellcheck="false" autocomplete="off" />
          </label>
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
          <span class="rv-hint">{canSubmit ? "Ready to submit" : "Fill the flagged fields to submit"}</span>
          <button class="rv-btn rv-btn-primary" on:click={submit} disabled={!canSubmit}>Submit</button>
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
  }
  .rv-head-title { font-size: .82rem; font-weight: 600; color: var(--tx); letter-spacing: .01em; }
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
  .rv-label-lap { color: var(--tx-dim); font-variant-numeric: tabular-nums; }

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
