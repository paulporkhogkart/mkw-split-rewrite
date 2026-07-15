import { splitCharacter } from './history_parse';
import { resolveItem, type ItemCategory } from './roster';

export type UnresolvedName = { category: ItemCategory; raw: string; slugGuess: string };

export type Loadout = {
  character: string | null;      // raw, costume stripped
  costume: string | null;        // raw; null == base costume (legitimate, not a failure)
  kart: string | null;           // raw
  characterSlug: string | null;
  costumeSlug: string | null;
  kartSlug: string | null;
  unresolved: UnresolvedName[];  // present-but-unresolvable only
};

/** Resolve a scraped `Character (Costume)` + kart into canonical slugs.
 *  A NULL costume is the base costume and is never reported as unresolved —
 *  only a name that is present AND fails to resolve lands in `unresolved`. */
export function resolveLoadout(characterRaw: string | null, kartRaw: string | null): Loadout {
  const { character, costume } = splitCharacter(characterRaw ?? '');
  const kartTrim = (kartRaw ?? '').trim();
  const kart = !kartTrim || kartTrim === '-' ? null : kartTrim;

  const unresolved: UnresolvedName[] = [];
  const resolve = (category: ItemCategory, raw: string | null): string | null => {
    if (!raw) return null;
    const { slug, slugGuess } = resolveItem(category, raw);
    if (!slug) unresolved.push({ category, raw, slugGuess });
    return slug;
  };

  return {
    character, costume, kart,
    characterSlug: resolve('character', character),
    costumeSlug: resolve('costume', costume),
    kartSlug: resolve('kart', kart),
    unresolved,
  };
}
