export type Dimension = 'player' | 'course' | 'character' | 'kart' | 'costume' | 'cc' | 'screen';
export type PeriodKey = 'today' | 'this_week' | 'this_month' | 'all_time' | 'range';

export interface Period {
  key: PeriodKey;
  tz: string;
  startUtc: string | null;   // 'YYYY-MM-DD HH:MM:SS' UTC (datetime()-comparable), null = open
  endUtc: string | null;
  startIso: string | null;   // offset ISO for the response, null = open
  endIso: string | null;
}

export interface StatRow { key: string; value: number | null; }

export interface StatResult {
  metric: string;
  period: { key: string; tz: string; start: string | null; end: string | null };
  filters: Record<string, string>;
  group_by?: Dimension;
  rows: StatRow[];
  total: number | null;
  unevaluable?: number;      // runs skipped by a body_condition (no prior weigh-in)
}
