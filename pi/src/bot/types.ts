import type { ReignInfo } from '../db/reign';

export type OvertakenEntry = { name: string; diff_str: string };   // name 'WR' for the world record
export type StillAhead = { name: string; diff_str: string } | null;
export type Positions = {
  track: { old: number | null; new: number | null };
  total: { old: number | null; new: number | null };
};

export type PbEmbedData = {
  player: string;
  track: string;            // course display name
  time: string;             // total time string
  improvement_str: string;  // formatted delta vs the player's previous PB
  is_new_track_record: boolean;
  reign: ReignInfo;
  positions: Positions;
  overtaken: OvertakenEntry[];
  still_ahead: StillAhead;
};

export type WrEmbedData = {
  holder: string;
  track: string;                       // course display name
  record: string;                      // total time string
  improvement_str: string | null;      // null => "First WR"
  reign: ReignInfo;
};
