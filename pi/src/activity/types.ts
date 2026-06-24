export type ActivityType =
  | 'pb' | 'rank' | 'turf_claim' | 'turf_fire' | 'turf_waver' | 'wr' | 'attempts' | 'screen';

export interface ActivityInput {
  ts: number;
  type: ActivityType;
  season_id: number;
  player_id: number | null;
  course_id: number | null;
  cc: number | null;
  payload: Record<string, unknown>;
}

export interface ActivityEvent {
  id: number;
  ts: number;
  type: ActivityType;
  course: { slug: string; name: string } | null;
  player: { id: number; name: string; color: string | null } | null;
  payload: Record<string, unknown>;
}
