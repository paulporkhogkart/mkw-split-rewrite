// Pure, unit-testable helper for PlayerPanel.svelte (no Svelte/Tauri imports). The
// server-connection indicator lives in the StatusBar; this is just the empty-state copy.

/** Copy for the empty panel (no live or cached players). `configured` = a season-server URL is set. */
export function emptyState(configured) {
  return configured
    ? { title: "No player data yet.", hint: "Waiting for the season server…" }
    : { title: "No player data yet.", hint: "Connect a season server in Settings › Sync." };
}
