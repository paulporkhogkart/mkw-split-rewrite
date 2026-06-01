<script>
  import { screen, liveScore, candidates, selection } from "../lib/stores.js";
  import { screenLabel } from "../lib/format.js";
  import RailSection from "./RailSection.svelte";
  import ReadoutRow from "./ReadoutRow.svelte";
  import CandidateList from "./CandidateList.svelte";
  import RaceSection from "./RaceSection.svelte";
  import EventLog from "./EventLog.svelte";

  // Which readout rows are expanded (multiple allowed). Initial default: Course
  // candidates dropped down, per request.
  let expanded = new Set(["course"]);

  /** Per-screen auto-expand presets. The active screen drives which readouts open. */
  const AUTO_PRESETS = {
    CHARACTER_SELECT: ["char", "costume"],   // expand both char + costume
    KART_SELECT:      ["kart"],
    COURSE_SELECT:    ["course"],
    RACING:           [],                     // collapse everything
  };
  // Any other recognised screen → just the Screen readout.
  const DEFAULT_PRESET = ["screen"];

  /** Event log section open/collapsed state. */
  let logOpen = true;

  /** Manual toggle - allows several rows open at once. Respected until the
   *  active screen next changes (which re-applies that screen's preset). */
  function toggleField(field) {
    const next = new Set(expanded);
    next.has(field) ? next.delete(field) : next.add(field);
    expanded = next;
  }

  // Auto-expand: when the active screen changes, apply its preset. Manual
  // expand/collapse is left alone while you stay on a screen; a new screen
  // re-applies. Unknown/"no signal" keeps the current state (initial = course).
  let _lastScreen = null;
  $: applyAuto($screen);
  function applyAuto(scr) {
    if (scr === _lastScreen) return;
    _lastScreen = scr;
    if (!scr || scr === "-") return;
    expanded = new Set(AUTO_PRESETS[scr] ?? DEFAULT_PRESET);
  }
</script>

<div class="rail">
<!-- ── Selection section (screen + char + costume + kart + course) ── -->
<RailSection title="Selection" first={true}>
  <!-- Screen -->
  <ReadoutRow
    value={$screen && $screen !== "-" ? screenLabel($screen) : "no signal"}
    score={$liveScore}
    empty={!$screen || $screen === "-"}
    expanded={expanded.has("screen")}
    on:toggle={() => toggleField("screen")}
  />
  {#if expanded.has("screen")}
    <CandidateList candidates={$candidates.screen ?? []} />
  {/if}

  <!-- Character -->
  <ReadoutRow
    value={$selection.char ?? "no character"}
    score={$selection.charConf}
    empty={!$selection.char}
    expanded={expanded.has("char")}
    on:toggle={() => toggleField("char")}
  />
  {#if expanded.has("char")}
    <CandidateList candidates={$candidates.char ?? []} />
  {/if}

  <!-- Costume (sits under Character - they're chosen together) -->
  <ReadoutRow
    value={$selection.costume ?? "no costume"}
    score={$selection.costumeConf}
    empty={!$selection.costume}
    expanded={expanded.has("costume")}
    on:toggle={() => toggleField("costume")}
  />
  {#if expanded.has("costume")}
    <CandidateList candidates={$candidates.costume ?? []} />
  {/if}

  <!-- Kart -->
  <ReadoutRow
    value={$selection.kart ?? "no kart"}
    score={$selection.kartConf}
    empty={!$selection.kart}
    expanded={expanded.has("kart")}
    on:toggle={() => toggleField("kart")}
  />
  {#if expanded.has("kart")}
    <CandidateList candidates={$candidates.kart ?? []} />
  {/if}

  <!-- Course -->
  <ReadoutRow
    value={$selection.course ?? "no course"}
    score={$selection.courseConf}
    empty={!$selection.course}
    expanded={expanded.has("course")}
    on:toggle={() => toggleField("course")}
  />
  {#if expanded.has("course")}
    <CandidateList candidates={$candidates.course ?? []} />
  {/if}
</RailSection>

<!-- ── Race section ── -->
<RailSection title="Race">
  <RaceSection />
</RailSection>

<!-- ── Event log section (grows to fill remaining rail height when open) ── -->
<RailSection title="Event log" collapsible={true} grow={true} open={logOpen} on:toggle={() => logOpen = !logOpen}>
  <EventLog />
</RailSection>
</div>

<style>
  /* Flex column so the Event log section can flex into the leftover height.
     The sidebar (App.svelte) is a flex column with a definite height, so
     `.rail` fills it and hands the remaining space to the growable section. */
  .rail { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
</style>
