import type { DatabaseSync } from 'node:sqlite';

export { CANONICAL_COURSES } from '../../db/courses';
import { seedCourses } from '../../db/courses';

/** The 40 track names exactly as they appear on mkwrs.com (note "Wario Shipyard"
 *  and straight apostrophes). Used by the completeness test. */
export const MKWRS_NAMES: string[] = [
  'Mario Bros. Circuit', 'Crown City', 'Whistlestop Summit', 'DK Spaceport', 'Desert Hills',
  'Shy Guy Bazaar', 'Wario Stadium', 'Airship Fortress', 'DK Pass', 'Starview Peak',
  'Sky-High Sundae', 'Wario Shipyard', 'Koopa Troopa Beach', 'Faraway Oasis', 'Peach Stadium',
  'Peach Beach', 'Salty Salty Speedway', 'Dino Dino Jungle', 'Great ? Block Ruins',
  'Cheep Cheep Falls', 'Dandelion Depths', 'Boo Cinema', 'Dry Bones Burnout', 'Moo Moo Meadows',
  'Choco Mountain', "Toad's Factory", "Bowser's Castle", 'Acorn Heights', 'Mario Circuit',
  'Rainbow Road',
  'SNES Mario Circuit 1', 'SNES Mario Circuit 2', 'SNES Mario Circuit 3', 'SNES Ghost Valley 1',
  'SNES Ghost Valley 2', 'SNES Ghost Valley 3', 'SNES Choco Island 1', 'SNES Choco Island 2',
  'SNES Koopa Beach 1', 'SNES Vanilla Lake 1',
];

export function seedCanonicalCourses(db: DatabaseSync): void {
  seedCourses(db);
}
