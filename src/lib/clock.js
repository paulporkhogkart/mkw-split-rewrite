import { readable } from "svelte/store";

// A coarse wall clock that ticks every 30s. Subscribing components recompute time-relative
// text (e.g. a card's "last seen 3h ago") even while no new data arrives — an offline player
// sends no frames, so without this the relative label would freeze at "just now". Only runs
// while something is subscribed.
export const nowTick = readable(Date.now(), (set) => {
  const id = setInterval(() => set(Date.now()), 30000);
  return () => clearInterval(id);
});
