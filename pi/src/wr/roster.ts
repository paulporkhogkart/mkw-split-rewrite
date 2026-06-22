import { slugify } from '../db/slug';

export type ItemCategory = 'character' | 'kart' | 'costume';

export const CHARACTERS = new Set<string>([
  'baby_daisy', 'baby_luigi', 'baby_mario', 'baby_peach', 'baby_rosalina', 'birdo', 'bowser',
  'bowser_jr', 'cataquack', 'chargin_chuck', 'cheep_cheep', 'coin_coffer', 'conkdor', 'cow',
  'daisy', 'dolphin', 'donkey_kong', 'dry_bones', 'fish_bone', 'goomba', 'hammer_bro', 'king_boo',
  'koopa_troopa', 'lakitu', 'luigi', 'mario', 'monty_mole', 'nabbit', 'para_biddybud', 'pauline',
  'peach', 'peepa', 'penguin', 'pianta', 'piranha_plant', 'pokey', 'rocky_wrench', 'rosalina',
  'shy_guy', 'sidestepper', 'snowman', 'spike', 'stingby', 'swoop', 'waluigi', 'wario', 'wiggler',
  'yoshi', 'toad', 'toadette',
]);

export const KARTS = new Set<string>([
  'b_dasher', 'baby_blooper', 'big_horn', 'billdozer', 'blastronaut_iii', 'bowser_bruiser',
  'buggybud', 'bumble_v', 'carpet_flyer', 'chargin_truck', 'cloud_9', 'cute_scoot',
  'dolphin_dasher', 'dread_sled', 'fin_twin', 'funky_dorrie', 'hot_rod', 'hyper_pipe',
  'junkyard_hog', 'lil_dumpy', 'lobster_roller', 'loco_moto', 'mach_rocket', 'mecha_trike',
  'pipe_frame', 'plushbuggy', 'rally_bike', 'rally_kart', 'rally_romper', 'rallygator',
  'reel_racer', 'ribbit_revster', 'roadster_royale', 'rob_hog', 'standard_bike', 'standard_kart',
  'stellar_sled', 'tune_thumper', 'w_twin_chopper', 'zoom_buggy',
]);

export const COSTUMES = new Set<string>([
  'aero', 'all_terrain', 'aristocrat', 'aurora', 'aviator', 'biker', 'biker_jr', 'burger_bud',
  'conductor', 'cowboy', 'dune_rider', 'farmer', 'fisherman', 'food_slinger', 'gondolier', 'happi',
  'mariachi', 'matsuri', 'mechanic', 'oasis', 'pirate', 'pit_crew', 'pro_racer', 'road_ruffian',
  'runner', 'sailor', 'sightseeing', 'slope_styler', 'soft_server', 'supercharged', 'swimwear',
  'touring', 'vacation', 'wampire', 'wicked_wasp', 'work_crew', 'yukata', 'engineer', 'explorer',
]);

/** User-editable alias maps, keyed by slugify(rawName) → canonical slug. mkwrs uses MK8-era
 *  display names for a few returning karts; our roster uses the in-game MKWorld names. */
export const CHARACTER_ALIASES: Record<string, string> = {};
export const KART_ALIASES: Record<string, string> = {
  r_o_b_h_o_g: 'rob_hog',       // 'R.O.B. H.O.G.'
  biddybuggy: 'buggybud',       // 'Biddybuggy'  -> Buggybud
  tiny_titan: 'rally_romper',   // 'Tiny Titan'  -> Rally Romper
};
export const COSTUME_ALIASES: Record<string, string> = {};

const TABLE: Record<ItemCategory, { set: Set<string>; aliases: Record<string, string> }> = {
  character: { set: CHARACTERS, aliases: CHARACTER_ALIASES },
  kart: { set: KARTS, aliases: KART_ALIASES },
  costume: { set: COSTUMES, aliases: COSTUME_ALIASES },
};

/** Resolve a raw mkwrs name to a canonical slug. slugify → canonical set → alias map → null. */
export function resolveItem(category: ItemCategory, raw: string): { slug: string | null; slugGuess: string } {
  const slugGuess = slugify(raw);
  const { set, aliases } = TABLE[category];
  if (set.has(slugGuess)) return { slug: slugGuess, slugGuess };
  if (aliases[slugGuess]) return { slug: aliases[slugGuess], slugGuess };
  return { slug: null, slugGuess };
}
