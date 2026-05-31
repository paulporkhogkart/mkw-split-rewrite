<script>
  import { screen, liveScore, candidates, selection } from "../lib/stores.js";
  import { screenLabel } from "../lib/format.js";
  import RailSection from "./RailSection.svelte";
  import ReadoutRow from "./ReadoutRow.svelte";
  import CandidateList from "./CandidateList.svelte";
  import RaceSection from "./RaceSection.svelte";
  import EventLog from "./EventLog.svelte";

  /** Which ReadoutRow is currently expanded (shows CandidateList). null = none. */
  let expandedField = null;

  /** Event log section open/collapsed state — default open to match old panel default */
  let logOpen = true;

  /** Toggle expanded field: collapse if already open, else open the new one. */
  function toggleField(field) {
    expandedField = expandedField === field ? null : field;
  }
</script>

<!-- ── Selection section (screen + char + kart + course + costume) ── -->
<RailSection title="Selection" first={true}>
  <!-- Screen -->
  <ReadoutRow
    value={$screen && $screen !== "—" ? screenLabel($screen) : "no signal"}
    score={$liveScore}
    empty={!$screen || $screen === "—"}
    expanded={expandedField === "screen"}
    on:toggle={() => toggleField("screen")}
  />
  {#if expandedField === "screen"}
    <CandidateList candidates={$candidates.screen ?? []} />
  {/if}

  <!-- Character -->
  <ReadoutRow
    value={$selection.char ?? "no character"}
    score={$selection.charConf}
    empty={!$selection.char}
    expanded={expandedField === "char"}
    on:toggle={() => toggleField("char")}
  />
  {#if expandedField === "char"}
    <CandidateList candidates={$candidates.char ?? []} />
  {/if}

  <!-- Kart -->
  <ReadoutRow
    value={$selection.kart ?? "no kart"}
    score={$selection.kartConf}
    empty={!$selection.kart}
    expanded={expandedField === "kart"}
    on:toggle={() => toggleField("kart")}
  />
  {#if expandedField === "kart"}
    <CandidateList candidates={$candidates.kart ?? []} />
  {/if}

  <!-- Course -->
  <ReadoutRow
    value={$selection.course ?? "no course"}
    score={$selection.courseConf}
    empty={!$selection.course}
    expanded={expandedField === "course"}
    on:toggle={() => toggleField("course")}
  />
  {#if expandedField === "course"}
    <CandidateList candidates={$candidates.course ?? []} />
  {/if}

  <!-- Costume -->
  <ReadoutRow
    value={$selection.costume ?? "no costume"}
    score={$selection.costumeConf}
    empty={!$selection.costume}
    expanded={expandedField === "costume"}
    on:toggle={() => toggleField("costume")}
  />
  {#if expandedField === "costume"}
    <CandidateList candidates={$candidates.costume ?? []} />
  {/if}
</RailSection>

<!-- ── Race section ── -->
<RailSection title="Race">
  <RaceSection />
</RailSection>

<!-- ── Event log section ── -->
<RailSection title="Event log" collapsible={true} open={logOpen} on:toggle={() => logOpen = !logOpen}>
  <EventLog />
</RailSection>
