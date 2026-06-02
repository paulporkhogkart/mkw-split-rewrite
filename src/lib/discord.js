// Decoupled Discord presence driver. Reads existing stores, computes the payload
// via the pure mapping, and calls the Rust commands. Reads only — no UI mutation.
import { invoke } from "@tauri-apps/api/core";
import { get } from "svelte/store";
import { screen, selection, race, pbSplits } from "./stores.js";
import { resets } from "./resets.js";
import { discordEnabled, twitchUrl } from "./discordSettings.js";
import { computePresence, UNCHANGED } from "./discordPayload.js";
import { parseTime } from "./discordFormat.js";

function snapshot() {
  const sel = get(selection);
  const r = get(race);
  const playerSplits = {};
  for (const [lap, t] of Object.entries(r.splits || {})) {
    const ms = parseTime(t);
    if (ms != null) playerSplits[Number(lap)] = ms;
  }
  return {
    screen: get(screen),
    course: sel.course, character: sel.char, kart: sel.kart,
    resets: get(resets),
    curLap: r.curLap, totLap: r.totLap,
    playerSplits, pbSplits: get(pbSplits),
    finalTime: r.finishTime,
    twitchUrl: get(twitchUrl),
  };
}

function push() {
  if (!get(discordEnabled)) { invoke("discord_clear_presence").catch(() => {}); return; }
  const payload = computePresence(snapshot());
  if (payload === UNCHANGED) return;
  invoke("discord_set_presence", { payload }).catch(() => {});
}

export function initDiscordPresence() {
  [screen, selection, race, pbSplits, resets, discordEnabled, twitchUrl]
    .forEach((s) => s.subscribe(() => push()));
}
