// Pure mapping: tracker state -> Discord presence payload. No Svelte/Tauri imports.
import { courseSlug, parseTime, formatDelta } from "./discordFormat.js";

export const UNCHANGED = Symbol("unchanged");

const IGNORE = new Set([
  "RACE_MENU", "RESET", "GHOST_RESET", "UNKNOWN_RESET",
  "REPLAY_MENU", "UNKNOWN_RACE_ACTIVE", "HOME",
]);
const SETUP = {
  CHARACTER_SELECT: "Choosing a character",
  KART_SELECT: "Choosing a kart",
  COURSE_SELECT: "Choosing a track",
};

function withButton(p, twitchUrl) {
  if (twitchUrl) { p.button_label = "Watch on Twitch"; p.button_url = twitchUrl; }
  return p;
}
function lastCompletedDelta(s) {
  const lap = (s.curLap ?? 0) - 1;
  if (lap >= 1 && s.pbSplits && s.pbSplits[lap] != null && s.playerSplits && s.playerSplits[lap] != null)
    return s.playerSplits[lap] - s.pbSplits[lap];
  return null;
}
function charKart(s) { return `${s.character ?? "?"} · ${s.kart ?? "?"}`; }

export function computePresence(s) {
  const screen = s.screen;
  if (IGNORE.has(screen)) return UNCHANGED;

  if (screen === "UNKNOWN") return { large_image: "penguin", details: "Idle" };
  if (SETUP[screen]) return { large_image: "penguin", details: SETUP[screen] };

  if (screen === "RACING") {
    const slug = courseSlug(s.course) || "splash";
    const resets = `${s.resets} reset${s.resets === 1 ? "" : "s"}`;
    const delta = lastCompletedDelta(s);
    const line2 = delta != null
      ? `Lap ${s.curLap}/${s.totLap} · ${formatDelta(delta)}`
      : `Lap ${s.curLap}/${s.totLap} · ${charKart(s)}`;
    return withButton({ large_image: slug, small_image: "penguin",
                        details: `${s.course} · ${resets}`, state: line2 }, s.twitchUrl);
  }

  if (screen === "GHOST") {
    const slug = courseSlug(s.course) || "splash";
    return withButton({ large_image: slug, small_image: "penguin",
                        details: s.course || "", state: "Watching a ghost" }, s.twitchUrl);
  }

  if (screen === "POST_TIME_TRIAL") {
    const slug = courseSlug(s.course) || "splash";
    let suffix;
    if (s.pbSplits && Object.keys(s.pbSplits).length) {
      const lastLap = Math.max(...Object.keys(s.pbSplits).map(Number));
      const pbTotal = s.pbSplits[lastLap];
      const myMs = parseTime(s.finalTime);
      suffix = (myMs != null) ? formatDelta(myMs - pbTotal) : charKart(s);
    } else suffix = charKart(s);
    return withButton({ large_image: slug, small_image: "penguin",
                        details: `${s.course} · finished`, state: `${s.finalTime} · ${suffix}` }, s.twitchUrl);
  }

  // TITLE / MAIN_MENU / SINGLEPLAYER_MENU / TIME_TRIALS / START_TIME_TRIAL / GALLERY / anything else
  return { large_image: "penguin", details: "In the menus" };
}
