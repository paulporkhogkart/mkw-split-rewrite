// Pure, unit-testable helpers for PlayerPanel.svelte (no Svelte/Tauri imports), mirroring
// playerCard.js. They turn the serverConnection state into the header chip + empty-state copy.
import { lastSeen } from "./playerCard.js";

/** The header connection chip. `conn` = { connected, syncedAt }; `now` is epoch-ms.
 *  live (green) = a live link; offline (amber) = no link but we have a cached snapshot,
 *  labelled with its age; none (grey) = no link and nothing cached. */
export function connectionChip(conn, now = Date.now()) {
  if (conn && conn.connected) return { tier: "live", label: "Live" };
  if (conn && conn.syncedAt != null) return { tier: "offline", label: `Offline · last sync ${lastSeen(now - conn.syncedAt)}` };
  return { tier: "none", label: "Not connected" };
}

/** Copy for the empty panel (no live or cached players). `configured` = a season-server URL is set. */
export function emptyState(configured) {
  return configured
    ? { title: "No player data yet.", hint: "Waiting for the season server…" }
    : { title: "No player data yet.", hint: "Connect a season server in Settings › Sync." };
}
