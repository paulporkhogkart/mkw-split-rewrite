// Presence-entry transitions -> chip player actions (spec Part B choreography:
// swap on a select screen = spawn, kart lock-in = flourish, finish = flourish).
import { comboKey } from "./chipKey.js";

const SELECT_SCREENS = new Set(["CHARACTER_SELECT", "KART_SELECT", "COURSE_SELECT"]);
const KART_RETREATS = new Set(["CHARACTER_SELECT", "HOME"]);

export function directorStep(prev, next) {
  const combo = comboKey(next || {});
  if (!combo) return { combo: null, action: null };
  const prevCombo = prev ? comboKey(prev) : null;
  const changed = combo !== prevCombo;
  // Kart lock-in flourish: only when ADVANCING out of kart select (mockup: "locked in →
  // FLOURISH"). Retreating to character select / home is not a lock-in; a swap-and-advance
  // in one transition still is (this rule must beat the select-spawn rule).
  if (prev && prev.screen === "KART_SELECT" && next.screen !== "KART_SELECT" && !KART_RETREATS.has(next.screen) && next.kart)
    return { combo, action: "confirm" };
  if (changed && SELECT_SCREENS.has(next.screen)) return { combo, action: "select" };
  if (next.final_time && !(prev && prev.final_time)) return { combo, action: "confirm" };
  if (changed) return { combo, action: "idle" };
  return { combo, action: null };
}
