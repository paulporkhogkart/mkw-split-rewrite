export type Lap = { lap: number; time_ms: number; time_str?: string | null; coins?: number | null; shrooms?: number | null };
// [t_ms, cx, cy, score, lap?] — lap is the engine's per-point HUD lap stamp (added 2026-06);
// legacy payloads omit it (4-tuple) and it may be null when the lap counter wasn't yet read.
export type Point = [number, number, number, number, (number | null)?];
export type AttemptPayload = {
  attempt_id: string; course: string; cc?: number; total_laps?: number | null;
  status: 'finished' | 'reset' | 'dnf';
  character?: string | null; kart?: string | null; costume?: string | null;
  started_at?: string | null; ended_at?: string | null; total_time?: string | null;
  coins_gained?: number | null; coins_lost?: number | null; mushrooms_used?: number | null;
  laps?: Lap[]; points?: Point[];
  source?: string | null;     // 'ghost' when re-derived from an in-game ghost replay
};
export type RunResult = { is_pb: boolean; rank: number | null; gap_to_leader_ms: number | null; gap_to_wr_ms: number | null };
export type ServerEvent =
  | { type: 'run_started'; player: string; course: string; cc: number }
  | { type: 'run_finished'; player: string; course: string; cc: number; total_time: string | null; is_pb: boolean; rank: number | null }
  | { type: 'pb_achieved'; player: string; course: string; cc: number; total_time: string; delta_vs_prev_ms: number | null; rank: number | null }
  | { type: 'lead_change'; course: string; cc: number; new_leader: string; prev_leader: string | null; total_time: string }
  | { type: 'wr_beaten'; player: string; course: string; cc: number; total_time: string; wr_time: string }
  | { type: 'wr_update'; course: string; cc: number; holder: string | null;
      total_time: string; prev_holder: string | null; prev_time: string | null;
      improvement_ms: number | null; character: string | null;
      vehicle: string | null; video_url: string | null }
  | { type: 'wr_name_flag'; category: 'character' | 'kart' | 'costume' | 'course';
      raw_value: string; slug_guess: string | null; course: string | null }
  | { type: 'wr_job_stuck'; wr_id: number; course: string; holder: string | null;
      record_str: string; reason: string; attempts: number };
