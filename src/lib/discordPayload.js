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

// The Twitch button appears on every non-idle state when enabled + a URL is set.
function withButton(p, s) {
  if (s.twitchButtonEnabled && s.twitchUrl) {
    p.button_label = s.twitchLabel || "Watch on Twitch";
    p.button_url = s.twitchUrl;
  }
  return p;
}
function lastCompletedDelta(s) {
  const lap = (s.curLap ?? 0) - 1;
  if (lap >= 1 && s.pbSplits && s.pbSplits[lap] != null && s.playerSplits && s.playerSplits[lap] != null)
    return s.playerSplits[lap] - s.pbSplits[lap];
  return null;
}
function charKart(s) { return `${s.character ?? "?"} · ${s.kart ?? "?"}`; }

// The finished card: shown the moment a final time lands (still on RACING, after
// the timer freezes) AND on POST_TIME_TRIAL. Both render identically, so the
// RACING->POST transition is a no-op. Delta is vs the PB's true total time.
function finishedCard(s) {
  const slug = courseSlug(s.course) || "splash";
  const suffix = (s.finalTime && s.pbTotalMs != null)
    ? formatDelta(parseTime(s.finalTime) - s.pbTotalMs)
    : charKart(s);
  return {
    large_image: slug, small_image: "penguin",
    details: `${s.course} · finished`,
    state: s.finalTime ? `${s.finalTime} · ${suffix}` : charKart(s),
  };
}

export function computePresence(s) {
  const screen = s.screen;
  if (IGNORE.has(screen)) return UNCHANGED;
  if (screen === "UNKNOWN") return { large_image: "penguin", details: "Idle" }; // idle: never a button

  let p;
  if (SETUP[screen]) {
    p = { large_image: "penguin", details: SETUP[screen] };
  } else if (screen === "RACING" && !s.finalTime) {
    // Active race: lap + live delta (or character/kart on lap 1 / no PB).
    const slug = courseSlug(s.course) || "splash";
    const resets = `${s.resets} reset${s.resets === 1 ? "" : "s"}`;
    const delta = lastCompletedDelta(s);
    const line2 = delta != null
      ? `Lap ${s.curLap}/${s.totLap} · ${formatDelta(delta)}`
      : `Lap ${s.curLap}/${s.totLap} · ${charKart(s)}`;
    p = { large_image: slug, small_image: "penguin", details: `${s.course} · ${resets}`, state: line2 };
  } else if (screen === "RACING" || screen === "POST_TIME_TRIAL") {
    // Finished (final time in), whether the freeze is still on RACING or we've
    // advanced to the results screen.
    p = finishedCard(s);
  } else if (screen === "GHOST") {
    const slug = courseSlug(s.course) || "splash";
    p = { large_image: slug, small_image: "penguin", details: s.course || "", state: "Watching a ghost" };
  } else {
    // TITLE / MAIN_MENU / SINGLEPLAYER_MENU / TIME_TRIALS / START_TIME_TRIAL / GALLERY / anything else
    p = { large_image: "penguin", details: "In the menus" };
  }

  return withButton(p, s); // button on all non-idle states
}
