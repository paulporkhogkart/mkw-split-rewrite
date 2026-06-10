// pi/src/progress/types.ts
export interface GraphNode { id: number; x: number; y: number; progress: number; }

export interface GraphEdge {
  id: number; a: number; b: number;        // endpoint node ids (a===b for a cyclic centerline)
  poly: [number, number][];                // common-frame polyline
  arcLen: number;                          // total px length of poly
  pLo: number; pHi: number;                // progress range covered by this edge
  kind: 'main' | 'branch';
  passThrough: number | null;              // paired edge id at a crossing (always null in Plan 1)
}

export interface CourseGraph {
  version: number; startNode: number; lapLengthPx: number;
  nodes: GraphNode[]; edges: GraphEdge[];
  status: 'graph' | 'centerline';
}

export interface Transform { dx: number; dy: number; scale: number; }

/** One run's recorded trail + its lap structure, for the builder. */
export interface RunInput {
  playerId: number;
  points: { t_ms: number; cx: number; cy: number; score: number; lap: number | null }[];
  lapCumMs: number[];                      // cumulative lap end-times (run_laps), ascending
}

export type ProjState = { edge: number; progress: number; x: number; y: number; t: number } | null;
export interface Obs { x: number; y: number; lap: number; totLap: number; t: number; stale: boolean; }
