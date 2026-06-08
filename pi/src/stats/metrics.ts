import type { Dimension } from './types';

export const RACE_DIMENSIONS: Dimension[] = ['player', 'course', 'character', 'kart', 'costume', 'cc'];

export type Status = 'finished' | 'reset' | 'dnf';

export interface RaceMetric {
  id: string;
  kind: 'race';
  /** SQL aggregate over the joined run/lap/point rows. */
  value: string;
  /** Which run statuses count; 'all' = no status filter. */
  statuses: Status[] | 'all';
  /** Extra joins the value needs. */
  joins: Array<'laps' | 'points'>;
  /** Restrict to was_pb=1 (PB counters). */
  pbOnly?: boolean;
}

export type BodyAgg = 'current' | 'change' | 'min' | 'max';

export interface BodyMetric {
  id: string;
  kind: 'body';
  /** Normalized column on the porker union (see body.ts). */
  column: string;
  aggs: BodyAgg[];
  defaultAgg: BodyAgg;
}

export interface SequentialMetric {
  id: string;
  kind: 'sequential';
}

export interface CompletionMetric {
  id: string;
  kind: 'completion';
}

export interface ScreenMetric {
  id: string;
  kind: 'screen';
}

export type MetricDef = RaceMetric | BodyMetric | SequentialMetric | CompletionMetric | ScreenMetric;

const RACE: RaceMetric[] = [
  { id: 'attempts',        kind: 'race', value: 'COUNT(*)',                                                statuses: 'all',        joins: [] },
  { id: 'resets',          kind: 'race', value: 'COUNT(*)',                                                statuses: ['reset'],    joins: [] },
  { id: 'finishes',        kind: 'race', value: 'COUNT(*)',                                                statuses: ['finished'], joins: [] },
  { id: 'reset_rate',      kind: 'race', value: "AVG(CASE WHEN r.status='reset' THEN 1.0 ELSE 0.0 END)",   statuses: 'all',        joins: [] },
  // Run-level totals (include resets + the partial final lap); per-lap rows stay for splits.
  { id: 'coins',           kind: 'race', value: 'SUM(r.coins_gained)',                                     statuses: 'all',        joins: [] },
  { id: 'coins_lost',      kind: 'race', value: 'SUM(r.coins_lost)',                                       statuses: 'all',        joins: [] },
  { id: 'mushrooms',       kind: 'race', value: 'SUM(r.mushrooms_used)',                                   statuses: 'all',        joins: [] },
  { id: 'driving_time',    kind: 'race', value: 'SUM(pt.maxt)',                                            statuses: 'all',        joins: ['points'] },
  { id: 'best_time',       kind: 'race', value: 'MIN(r.total_time_ms)',                                    statuses: ['finished'], joins: [] },
  { id: 'avg_finish_time', kind: 'race', value: 'AVG(r.total_time_ms)',                                    statuses: ['finished'], joins: [] },
  { id: 'pb_count',        kind: 'race', value: 'COUNT(*)',                                                statuses: ['finished'], joins: [], pbOnly: true },
  // was_pb runs only get faster over time, so within a window MAX=first PB, MIN=last PB.
  { id: 'time_improvement', kind: 'race', value: 'MAX(r.total_time_ms) - MIN(r.total_time_ms)',            statuses: ['finished'], joins: [], pbOnly: true },
];

const BODY_COLUMNS: Record<string, string> = {
  weight: 'weight', bmi: 'bmi', body_fat: 'body_fat', fat_free_weight: 'fat_free_weight',
  subcutaneous_fat: 'subcutaneous_fat', visceral_fat: 'visceral_fat', body_water: 'body_water',
  skeletal_muscle: 'skeletal_muscle', muscle_mass: 'muscle_mass', bone_mass: 'bone_mass',
  protein: 'protein', bmr: 'bmr', metabolic_age: 'metabolic_age',
};

const BODY: BodyMetric[] = Object.entries(BODY_COLUMNS).map(([id, column]) => ({
  id, kind: 'body', column, aggs: ['current', 'change', 'min', 'max'], defaultAgg: 'current',
}));

const SEQUENTIAL: SequentialMetric[] = [
  { id: 'resets_since_pb', kind: 'sequential' },
  { id: 'avg_resets_until_pb', kind: 'sequential' },
  { id: 'current_reset_streak', kind: 'sequential' },
];

const COMPLETION: CompletionMetric[] = [
  { id: 'avg_completion_before_reset', kind: 'completion' },
];

const SCREEN: ScreenMetric[] = [
  { id: 'screen_time', kind: 'screen' },
];

const REGISTRY = new Map<string, MetricDef>([...RACE, ...BODY, ...SEQUENTIAL, ...COMPLETION, ...SCREEN].map((m) => [m.id, m]));

export function getMetric(id: string): MetricDef | undefined { return REGISTRY.get(id); }
export function listMetrics(): MetricDef[] { return [...REGISTRY.values()]; }

/** Which dimensions a metric may be filtered/grouped by. Body metrics: player only. */
export function allowsDimension(metricId: string, dim: Dimension): boolean {
  const m = REGISTRY.get(metricId);
  if (!m) return false;
  if (m.kind === 'race') return RACE_DIMENSIONS.includes(dim);
  if (m.kind === 'sequential' || m.kind === 'completion') return dim === 'player' || dim === 'course' || dim === 'cc';
  if (m.kind === 'screen') return dim === 'player' || dim === 'screen';
  return dim === 'player'; // body
}
