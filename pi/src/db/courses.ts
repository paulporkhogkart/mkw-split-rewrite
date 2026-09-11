import type { DatabaseSync } from 'node:sqlite';

/** The canonical courses as [slug, display_name] - MUST mirror server/courses.py
 *  (the slug is the stable cross-system key; the desktop engine's course templates are
 *  images/courses/<lang>/<slug>.png). Append only: courses.id is insertion order and the
 *  site sorts by it. */
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
  // Game v1.8.0 (Sept 2026): 10 SNES retro courses added to Time Trials (5 laps each).
  ['snes_mario_circuit_1', 'SNES Mario Circuit 1'],
  ['snes_mario_circuit_2', 'SNES Mario Circuit 2'],
  ['snes_mario_circuit_3', 'SNES Mario Circuit 3'],
  ['snes_ghost_valley_1', 'SNES Ghost Valley 1'],
  ['snes_ghost_valley_2', 'SNES Ghost Valley 2'],
  ['snes_ghost_valley_3', 'SNES Ghost Valley 3'],
  ['snes_choco_island_1', 'SNES Choco Island 1'],
  ['snes_choco_island_2', 'SNES Choco Island 2'],
  ['snes_koopa_beach_1', 'SNES Koopa Beach 1'],
  ['snes_vanilla_lake_1', 'SNES Vanilla Lake 1'],
];

/** Retro courses are not on the World island: no world/turf-map slot. */
export const RETRO_COURSE_SLUGS: ReadonlySet<string> =
  new Set(CANONICAL_COURSES.map(([s]) => s).filter((s) => s.startsWith('snes_')));

/** Boot migration: make sure every canonical course has a `courses` row. Idempotent
 *  (INSERT OR IGNORE on the UNIQUE slug); existing rows and their ids are untouched, new
 *  courses are appended. Historically only the Python importer seeded this table, so a
 *  game update that adds courses needed a manual INSERT on the Pi. Returns the number
 *  of rows added. */
export function seedCourses(db: DatabaseSync): number {
  const stmt = db.prepare('INSERT OR IGNORE INTO courses(slug, display_name) VALUES (?, ?)');
  let added = 0;
  for (const [slug, name] of CANONICAL_COURSES) added += Number(stmt.run(slug, name).changes);
  return added;
}
