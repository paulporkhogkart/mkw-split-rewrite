// Display names -> chip-pack slugs. Slug rule follows the chip pack's canonical naming
// (see pi/src/wr/roster.ts slugs and web/chips.lock shard names). Extends App.svelte's
// toFilename with trim and hyphen folding, which the pack requires (e.g., para_biddybud).

export function slug(name) {
  if (!name) return null;
  const s = name.toLowerCase().replace(/[?'.]/g, "").trim().replace(/[\s-]+/g, "_");
  return s || null;
}

/** Pack combo key for a presence entry's selection. Kart present -> char__costume__kart;
 *  else the standalone char__costume chip. Costume "Base"/absent -> "base". */
export function comboKey(entry) {
  const { character, costume, kart } = entry || {};
  const c = slug(character);
  if (!c) return null;
  const co = slug(costume) || "base";
  const k = slug(kart);
  return k ? `${c}__${co}__${k}` : `${c}__${co}`;
}
