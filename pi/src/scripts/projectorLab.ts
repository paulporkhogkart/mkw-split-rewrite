// pi/src/scripts/projectorLab.ts
// Cross-validated projector evaluation on production-stack trails
// (temp/trails/*.json from temp/trail_lab.py): build the model from one run,
// replay the OTHER through projectStep at 15Hz, report per TUNING config.
// Usage: npm run projector-lab
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildCourseModel } from '../progress/build';
import { prepareModel, projectStep, TUNING } from '../progress/project';
import type { ProjState, RunInput } from '../progress/types';

const TRAILS = (name: string) =>
  fileURLToPath(new URL(`../../../temp/trails/${name}.json`, import.meta.url));

function loadRun(name: string): RunInput {
  const raw = JSON.parse(readFileSync(TRAILS(name), 'utf8')) as {
    playerId: number; lapCumMs: number[];
    points: [number, number, number, number, number | null][];
  };
  return { playerId: raw.playerId, lapCumMs: raw.lapCumMs,
    points: raw.points.map(([t_ms, cx, cy, score, lap]) => ({ t_ms, cx, cy, score, lap })) };
}

function lapOf(t: number, cum: number[]): number {
  let L = 1;
  for (const b of cum) { if (t >= b) L++; else break; }
  return L;
}

interface Report {
  pair: string; matchDist: number;
  mono: number; held_pct: number; d_p50: number; d_p99: number;
  final: number; nulls: number; steps: number;
}

function replay(model: RunInput, run: RunInput, matchDist: number): Report {
  TUNING.matchDist = matchDist;
  const built = buildCourseModel([model]);
  if (!built) throw new Error('model build failed');
  const m = built.model;
  const pe = prepareModel(m);
  const N = m.laps.length;
  let st: ProjState = null;
  let prevLap = 1, prev: number | null = null;
  let mono = 0, held = 0, nulls = 0, steps = 0;
  const deltas: number[] = [];
  for (let i = 0; i < run.points.length; i += 4) {        // 60Hz points -> 15Hz
    const p = run.points[i];
    const lap = Math.min(p.lap ?? lapOf(p.t_ms, run.lapCumMs), N);
    if (lap > prevLap) st = st ? { ...st, progress: 0, x: p.cx, y: p.cy, t: p.t_ms } : null;
    prevLap = Math.max(prevLap, lap);
    const r = projectStep(st, m, pe, { x: p.cx, y: p.cy, lap, totLap: N, t: p.t_ms, stale: false });
    st = r.state;
    steps++;
    if (r.completion == null) { nulls++; continue; }
    if (prev != null) {
      const d = r.completion - prev;
      if (d < -1e-9) mono++;
      else if (d === 0) held++;
      else deltas.push(d);
    }
    prev = r.completion;
  }
  deltas.sort((a, b) => a - b);
  const q = (p: number) => (deltas.length ? deltas[Math.min(deltas.length - 1, Math.floor(p * deltas.length))] : 0);
  return { pair: '', matchDist, mono, held_pct: Math.round(1000 * held / Math.max(1, steps)) / 10,
    d_p50: q(0.5), d_p99: q(0.99), final: prev ?? -1, nulls, steps };
}

function main() {
  const boo = loadRun('bootest');
  const koo = loadRun('koops');
  const pairs: [string, RunInput, RunInput][] = [
    ['model=bootest replay=koops', boo, koo],
    ['model=koops replay=bootest', koo, boo],
  ];
  console.log('pair                          mdist  mono  held%   d_p50      d_p99      final   nulls/steps');
  for (const [name, model, run] of pairs) {
    for (const md of [60, 40, 30]) {
      const r = replay(model, run, md);
      console.log(`${name.padEnd(30)}${String(md).padEnd(7)}${String(r.mono).padEnd(6)}`
        + `${String(r.held_pct).padEnd(8)}${r.d_p50.toFixed(5).padEnd(11)}${r.d_p99.toFixed(5).padEnd(11)}`
        + `${r.final.toFixed(3).padEnd(8)}${r.nulls}/${r.steps}`);
    }
  }
  TUNING.matchDist = 60;
}
main();
