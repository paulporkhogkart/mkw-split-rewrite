import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DateTime } from 'luxon';
import { applySchema } from '../db/connect';
import { createStatsApp } from './stats';

function db(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
          INSERT INTO players(id,display_name) VALUES (1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle');
          INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms)
          VALUES (1,1,1,1,150,'reset','live','2026-06-10T03:00:00+00:00',NULL),
                 (2,1,1,1,150,'finished','live','2026-06-10T04:00:00+00:00',160000);`);
  return d;
}

const MEL = 'Australia/Melbourne';

describe('stats routes', () => {
  it('GET /v1/stats/value counts resets in an explicit range', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request(`/v1/stats/value?metric=resets&period=range&from=2026-06-01&to=2026-06-30&tz=${MEL}`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.value).toBe(1);
    expect(body.period.tz).toBe(MEL);
  });

  it('respects the range window (Jan excludes the June run)', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request(`/v1/stats/value?metric=resets&period=range&from=2026-01-01&to=2026-01-31&tz=${MEL}`);
    expect((await res.json()).value).toBe(0);
  });

  it('rejects body_fat grouped by course', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/breakdown?metric=body_fat&group_by=course');
    expect(res.status).toBe(400);
  });

  it('GET /v1/stats/metrics lists the registry', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/metrics');
    const body = await res.json();
    expect(body.map((m: { id: string }) => m.id)).toContain('coins');
  });

  it('body metric without porker -> 503', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=body_fat&agg=current');
    expect(res.status).toBe(503);
  });

  it('series buckets a range by day', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request(`/v1/stats/series?metric=attempts&period=range&from=2026-06-08&to=2026-06-15&bucket=day&tz=${MEL}`);
    const body = await res.json();
    expect(body.bucket).toBe('day');
    const total = body.buckets.reduce((s: number, b: { value: number | null }) => s + (b.value ?? 0), 0);
    expect(total).toBe(2); // both runs land on the 2026-06-10 (Melbourne) bucket
  });

  it('body_condition keeps only runs under the BMI at run time', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'pk-'));
    const pkPath = join(dir, 'porker.db');
    const pk = new DatabaseSync(pkPath);
    pk.exec(`CREATE TABLE "EunoraMeasurements" ("Timestamp" INTEGER,"Weight" REAL,"BodyMassIndex" REAL,
      "BodyFat" REAL,"FatFreeBodyWeight" REAL,"SubcutaneousFat" REAL,"VisceralFat" REAL,"BodyWater" REAL,
      "SkeletalMuscle" REAL,"MuscleMass" REAL,"BoneMass" REAL,"Protein" REAL,"BasalMetabolicRate" REAL,"MetabolicAge" REAL)`);
    const ts = Math.floor(DateTime.fromISO('2026-06-09T00:00:00', { zone: 'utc' }).toSeconds());
    pk.prepare(`INSERT INTO "EunoraMeasurements" VALUES (?,80,21,18,60,15,5,55,50,50,3,18,1700,25)`).run(ts);
    pk.close();

    const app = createStatsApp(db(), { porkerPath: pkPath }); // player 1 = 'Luke' = Eunora
    const res = await app.request('/v1/stats/value?metric=finishes&period=all_time&body_condition=bmi<22');
    expect((await res.json()).value).toBe(1);   // BMI 21 < 22 -> the finished run qualifies
    const res2 = await app.request('/v1/stats/value?metric=finishes&period=all_time&body_condition=bmi<20');
    expect((await res2.json()).value).toBe(0);
    rmSync(dir, { recursive: true, force: true });
  });

  it('sequential resets_since_pb via /value (no PB -> all resets)', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=resets_since_pb&player=Luke&course=bc');
    expect(res.status).toBe(200);
    expect((await res.json()).value).toBe(1); // one reset, no PB yet
  });

  it('sequential /value without player+course -> 400', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=resets_since_pb');
    expect(res.status).toBe(400);
  });

  it('/v1/stats/metrics tags sequential dimensions', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const body = await (await app.request('/v1/stats/metrics')).json();
    const seq = body.find((m: { id: string }) => m.id === 'resets_since_pb');
    expect(seq.dimensions).toEqual(['player', 'course', 'cc']);
  });

  it('avg_completion_before_reset dispatches (null without trails) and is catalogued', async () => {
    const app = createStatsApp(db(), { porkerPath: null });
    const res = await app.request('/v1/stats/value?metric=avg_completion_before_reset&course=bc');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.value).toBeNull();      // the reset has no trail -> unevaluable
    expect(body.unevaluable).toBe(1);
    const cat = await (await app.request('/v1/stats/metrics')).json();
    expect(cat.find((x: { id: string }) => x.id === 'avg_completion_before_reset').dimensions).toEqual(['player', 'course', 'cc']);
  });

  it('correlation endpoint pairs finish time with as-of body fat', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'corr-'));
    const pkPath = join(dir, 'porker.db');
    const pk = new DatabaseSync(pkPath);
    pk.exec(`CREATE TABLE "EunoraMeasurements" ("Timestamp" INTEGER,"Weight" REAL,"BodyMassIndex" REAL,"BodyFat" REAL,
      "FatFreeBodyWeight" REAL,"SubcutaneousFat" REAL,"VisceralFat" REAL,"BodyWater" REAL,"SkeletalMuscle" REAL,
      "MuscleMass" REAL,"BoneMass" REAL,"Protein" REAL,"BasalMetabolicRate" REAL,"MetabolicAge" REAL)`);
    const ep = (iso: string) => Math.floor(DateTime.fromISO(iso, { zone: 'utc' }).toSeconds());
    pk.prepare(`INSERT INTO "EunoraMeasurements" VALUES (?,80,22,?,60,15,5,55,50,50,3,18,1700,25)`).run(ep('2026-06-09T00:00:00'), 18);
    pk.close();

    const app = createStatsApp(db(), { porkerPath: pkPath }); // db(): Luke + bc + a finished run on 2026-06-10
    const res = await app.request('/v1/stats/correlation?body=body_fat&player=Luke&course=bc&period=all_time');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.n).toBe(1);       // one finished run, with an as-of weigh-in
    expect(body.r).toBeNull();    // n < 2 -> null
    rmSync(dir, { recursive: true, force: true });

    const bad = await app.request('/v1/stats/correlation?body=body_fat&player=Luke');
    expect(bad.status).toBe(400); // missing course
  });
});
