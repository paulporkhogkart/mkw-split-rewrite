<script>
  // LanguageSelectors.svelte - Application language + Switch system language selects.
  // State is owned by App.svelte; changes are persisted via the passed callbacks
  // (which call send({ type:"update_config", ... }) exactly as before).

  export let LANGUAGES = [];
  export let appLanguage = "en_uk";
  export let switch2Language = "en_uk";

  // Callbacks - pass onAppLanguageChange / onSwitch2LanguageChange from App.svelte.
  export let onAppLanguageChange    = () => {};
  export let onSwitch2LanguageChange = () => {};

  // Optional: unique id prefix to avoid duplicate HTML ids when both wizard and
  // settings modal render at the same time (they don't - only one is shown at a time -
  // but a prefix keeps ids unique by convention).
  export let idPrefix = "ls";
</script>

<div class="lang-form">
  <div class="lang-row">
    <label for="{idPrefix}-app-lang">Application language</label>
    <select id="{idPrefix}-app-lang" bind:value={appLanguage} on:change={onAppLanguageChange}>
      {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
    </select>
  </div>
  <div class="lang-row">
    <label for="{idPrefix}-sw2-lang">Switch system language</label>
    <select id="{idPrefix}-sw2-lang" bind:value={switch2Language} on:change={onSwitch2LanguageChange}>
      {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
    </select>
    <p class="hint lang-hint">Determines which image templates are used for detection (characters, courses, menus, etc.).</p>
  </div>
</div>

<style>
  .lang-form { display: flex; flex-direction: column; gap: 1rem; width: 100%; }
  .lang-row  { display: flex; flex-direction: column; gap: .3rem; }
  .lang-row label { font-size: .72rem; color: var(--tx-mut); }
  .hint      { font-size: .7rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }
  .lang-hint { font-size: .64rem; }
</style>
