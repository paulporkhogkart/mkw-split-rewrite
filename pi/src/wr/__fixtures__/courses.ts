import type { DatabaseSync } from 'node:sqlite';

/** The 30 canonical courses as [slug, display_name] (mirrors server/courses.py). */
export const CANONICAL_COURSES: [string, string][] = [
  ['mario_bros_circuit', 'Mario Bros. Circuit'],
  ['crown_city', 'Crown City'],
  ['whistlestop_summit', 'Whistlestop Summit'],
  ['dk_spaceport', 'DK Spaceport'],
  ['desert_hills', 'Desert Hills'],
  ['shy_guy_bazaar', 'Shy Guy Bazaar'],
  ['wario_stadium', 'Wario Stadium'],
  ['airship_fortress', 'Airship Fortress'],
  ['dk_pass', 'DK Pass'],
  ['starview_peak', 'Starview Peak'],
  ['sky_high_sundae', 'Sky-High Sundae'],
  ['warios_galleon', 'Wario’s Galleon'],
  ['koopa_troopa_beach', 'Koopa Troopa Beach'],
  ['faraway_oasis', 'Faraway Oasis'],
  ['peach_stadium', 'Peach Stadium'],
  ['peach_beach', 'Peach Beach'],
  ['salty_salty_speedway', 'Salty Salty Speedway'],
  ['dino_dino_jungle', 'Dino Dino Jungle'],
  ['great_block_ruins', 'Great ? Block Ruins'],
  ['cheep_cheep_falls', 'Cheep Cheep Falls'],
  ['dandelion_depths', 'Dandelion Depths'],
  ['boo_cinema', 'Boo Cinema'],
  ['dry_bones_burnout', 'Dry Bones Burnout'],
  ['moo_moo_meadows', 'Moo Moo Meadows'],
  ['choco_mountain', 'Choco Mountain'],
  ['toads_factory', 'Toad’s Factory'],
  ['bowsers_castle', 'Bowser’s Castle'],
  ['acorn_heights', 'Acorn Heights'],
  ['mario_circuit', 'Mario Circuit'],
  ['rainbow_road', 'Rainbow Road'],
];

/** The 30 track names exactly as they appear on mkwrs.com (note "Wario Shipyard"
 *  and straight apostrophes). Used by the completeness test. */
export const MKWRS_NAMES: string[] = [
  'Mario Bros. Circuit', 'Crown City', 'Whistlestop Summit', 'DK Spaceport', 'Desert Hills',
  'Shy Guy Bazaar', 'Wario Stadium', 'Airship Fortress', 'DK Pass', 'Starview Peak',
  'Sky-High Sundae', 'Wario Shipyard', 'Koopa Troopa Beach', 'Faraway Oasis', 'Peach Stadium',
  'Peach Beach', 'Salty Salty Speedway', 'Dino Dino Jungle', 'Great ? Block Ruins',
  'Cheep Cheep Falls', 'Dandelion Depths', 'Boo Cinema', 'Dry Bones Burnout', 'Moo Moo Meadows',
  'Choco Mountain', "Toad's Factory", "Bowser's Castle", 'Acorn Heights', 'Mario Circuit',
  'Rainbow Road',
];

export function seedCanonicalCourses(db: DatabaseSync): void {
  const stmt = db.prepare('INSERT OR IGNORE INTO courses(slug, display_name) VALUES (?, ?)');
  for (const [slug, name] of CANONICAL_COURSES) stmt.run(slug, name);
}
