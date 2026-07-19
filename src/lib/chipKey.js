// Display names -> chip-pack slugs. Mirrors App.svelte's toFilename rule (the same rule
// that names the capture/template files): lowercase, strip [?'.], spaces -> underscore.

export function slug(name) {
  if (!name) return null;
  const s = name.toLowerCase().replace(/[?'.]/g, "").trim().replace(/\s+/g, "_");
  return s || null;
}

/** Pack combo key for a presence entry's selection. Kart present -> char__costume__kart;
 *  else the standalone char__costume chip. Costume "Base"/absent -> "base". */
export function comboKey({ character, costume, kart }) {
  const c = slug(character);
  if (!c) return null;
  const co = slug(costume) || "base";
  const k = slug(kart);
  return k ? `${c}__${co}__${k}` : `${c}__${co}`;
}
