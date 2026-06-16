# Broadcast Stats Engine - Design

**Date:** 2026-06-08
**Status:** Approved for planning
**Sub-project:** First increment of a "broadcast statistics" capability for the kart-off (and parallel pork-off) competition.

## 1. Goal

Provide auto-generated broadcast graphics with arbitrary aggregate statistics over the
competition data: totals, counts, averages and changes of metrics (coins, resets,
mushrooms, driving time, PBs, body composition, ...) sliced by player, course,
character, kart, costume, cc and an arbitrary time window ("this week", "this month",
"today", all-time, or explicit range), including combinations ("coins on Bowser's
Castle this month", "PBs this week while under 22 BMI").

The defining realization: almost every requested stat is the **same shape** -

> an **aggregation** (sum / count / avg / min / max / current / change) of a **metric**,
> **sliced by** dimensions, **filtered by** a time window (and optionally a body
> condition).

So we build one composable engine rather than an endpoint per stat.

## 2. Scope

### In scope (this increment)
The composable stats engine and its HTTP surface, across **two domains**:
- **race** - facts from `runs` / `run_laps` / `run_points` in `mkw.db`.
- **body** - facts from the pork-off `porker.db` (read-only).

Plus the **cross-domain body-condition filter** ("race metric where the player's body
state at run time satisfies a predicate"), because it fits the dimension/filter model
and builds the alignment primitive the later analytical work reuses.

### Roadmap (scoped here, built as later increments)
2. **Sequential stats** - resets-since-last-PB, average-resets-until-a-PB, attempt/reset
   streaks. These need run *ordering*, not GROUP BY, so they are a distinct query shape.
3. **Track-progress reconstruction** - per-course centreline built from finished runs'
   trail points; project a run's last point onto it to estimate completion %; powers
   "average completion before reset" and is reusable elsewhere.
4. **Screen-time telemetry** (capture-dependent) - the engine emits screen enter/leave
   intervals -> a new server table -> "time in menus" and similar. This is the only ask
   that needs *new data capture*; everything else aggregates data we already store. No
   AFK/idle cap (raw wall-clock is acceptable per product decision).
5. **Analytical** - race x body correlations / regressions ("lap time improves as body
   fat drops"); reuses the alignment primitive from this increment.

### Out of scope
Precompute / materialised read caches (data is tiny; see Section 5.4) and any write path
to `porker.db`. The one schema touch is a single additive `runs.was_pb` column
(Section 4.5), not a restructuring.

## 3. Background: current data model

Authoritative server DB is `pi/mkw.db` (SQLite, WAL), served by the Node/TypeScript Hono
app in `pi/`. Schema in `server/schema.sql`. Relevant tables:

- `runs` - one row per attempt: `status` (`finished` | `reset` | `dnf`), `provenance`
  (`live` | `legacy_import` | `carryover`), `season_id`, `player_id`, `course_id`, `cc`,
  `started_at`, `ended_at`, `total_time_ms`, `character`, `kart`, `costume`, `is_pb`,
  `created_at`.
- `run_laps` - per lap: `lap_time_ms`, `coins` (signed delta), `shrooms` (use-count).
- `run_points` - minimap trail: `t_ms`, `cx`, `cy`, `score`.
- `world_records`, `players` (has `color`), `courses`, `seasons`, `season_rosters`.

Existing read patterns live in `pi/src/db/*.ts` (e.g. `reads.ts`, `leaderboards.ts`) and
`pi/src/api/reads.ts`. The stats engine follows these conventions.

`porker.db` (pork-off, .NET/EF-Core, read-only to us): **per-person tables** -
`Measurements` (generic) plus `<Name>Measurements`. Each row is a weigh-in:
`Timestamp` (Unix epoch seconds), `Weight`, `BodyMassIndex`, `BodyFat`,
`FatFreeBodyWeight`, `SubcutaneousFat`, `VisceralFat`, `BodyWater`, `SkeletalMuscle`,
`MuscleMass`, `BoneMass`, `Protein`, `BasalMetabolicRate`, `MetabolicAge`, plus
later-added gamey scores (`Agility`/`Endurance`/`Strength`/`Overall`, ignored).

### Data reality (sets expectations)
Of ~393 runs, ~355 are historical imports that are **total-time-only** - no per-lap coins
/ mushrooms, no trail points. Only **live runs** (38 so far, growing) carry the rich
per-lap and trail data. So coins / mushrooms / resets / driving-time / completion-% stats
are **forward-looking** - they accrue as the competition is played live; history provides
PB *times* only.

## 4. Key decisions

### 4.1 Identity map (porker person -> kart player)
Configured (not hardcoded in queries), in `pi/src/stats/body.ts` or a small config:

| porker table | kart player | player_id |
|---|---|---|
| `Measurements` | Paul | 1 |
| `AddymerMeasurements` | Gub | 2 |
| `AlexMeasurements` | Alex | 4 |
| `EunoraMeasurements` | Luke | 3 |
| `BraydenMeasurements` | Aliias | 5 |

`BluMeasurements` and `CbriMeasurements` are excluded (not competition participants).

### 4.2 Time periods (tz-aware, client-supplied)
All server timestamps are **UTC** (mkw.db ISO-8601 with `+00:00`, in mixed formats;
porker `Timestamp` is epoch seconds = absolute UTC). The client passes a `period` key and
a `tz`:
- The server resolves `{period, tz}` into a half-open `[start, end)` window **in app
  code** using a timezone-correct method (handles AEDT/AEST DST), then converts to UTC
  bounds. **SQLite never does timezone math** (it has no tz database).
- Defaults: `tz = Australia/Melbourne`, weeks start **Monday**.
- Periods: `today`, `this_week`, `this_month`, `all_time`, and explicit `from`/`to`.
- Race rows are bucketed on **`ended_at`** (when the attempt occurred). Body rows on
  `Timestamp` (epoch). Window bounds are passed to SQL as UTC; comparisons wrap the
  column in `datetime()` to neutralise the mixed string formats
  (`datetime(ended_at) >= :start AND datetime(ended_at) < :end`).
- Implementation note: prefer a small, well-tested tz method. Luxon or `date-fns-tz` are
  the safe options; a zero-dep `Intl.DateTimeFormat`-based resolver is acceptable if it
  passes the DST tests. Adding a dependency must respect the CI lockfile constraints (see
  memory: release CI uses `npm install`).

### 4.3 Reset handling (per-metric status inclusion)
Every metric declares **which run statuses it counts**. Reset attempts still collected
coins, burned mushrooms and spent driving time on the laps before they bailed, so:
- `coins`, `mushrooms`, `driving_time`, `attempts` count **all statuses**.
- `resets` counts `status='reset'`; `finishes` counts `status='finished'`.
- PB / leaderboard-time metrics (`best_time`, `avg_finish_time`, `pb_count`) count
  `finished` only.

### 4.4 Driving time = trail-based `engaged_ms`
Wall-clock (`ended_at - started_at`) is unusable: a mid-race pause (`race_menu`) inflates
it (verified - a 2:51.8 run showed 4:42 wall-clock). The in-game timer and the trail
`t_ms` are both pause-safe (the engine stops recording during `race_menu`).

Per product decision, driving time uses the **trail**: `engaged_ms = MAX(run_points.t_ms)`
per attempt, summed over the run set, all statuses. Attempts with no trail contribute 0
(a handful of telemetry-less short resets). Includes a ~6.7s countdown per attempt
(accepted as negligible). No fallback chain.

### 4.5 PB-history reconstruction (which runs *were* PBs)
`runs.is_pb` is only the **current** best. "PBs this week" and the sequential stats need
which runs **were** PBs when set. This is **derived** via a running minimum: over finished
runs partitioned by `(player_id, course_id, cc)` and ordered by `datetime(ended_at), id`,
a run *was* a PB iff its `total_time_ms` is strictly less than the running minimum of all
prior runs (the first finished run is always a PB).

```sql
WITH f AS (
  SELECT id, player_id, course_id, cc, total_time_ms, ended_at, is_pb,
    MIN(total_time_ms) OVER (PARTITION BY player_id, course_id, cc
        ORDER BY datetime(ended_at), id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_min
  FROM runs WHERE status='finished' AND season_id=:season)
SELECT *, (prior_min IS NULL OR total_time_ms < prior_min) AS was_pb FROM f
```

Verified against the live DB: the last reconstructed PB equals the current `is_pb` row in
**150/150** player/course groups, 0 mismatches.

**Storage (decided after review).** Rather than run this window on every read, `was_pb` is
a **stored, additive `runs.was_pb` column** (added in `pi/src/db/connect.ts`, mirroring
its existing `is_current` additive migration). It is re-derived from the CTE at
**finalize** - the same step that maintains `is_pb`, scoped to the affected
`(player, course, cc)` - and **backfilled once** for existing rows. Re-deriving (rather
than an incremental flag) stays correct under attempt-replacement and out-of-order
ingest, and moves the cost onto the write path (one cheap reconstruction per finished
run) instead of every broadcast read, which becomes a trivial indexed
`COUNT(*) WHERE was_pb=1`. The CTE stays the single source of truth for the maintainer,
the backfill and the tests.

Caveat: pre-cutover history is a single carryover baseline per player/course, so the full
PB *sequence* exists only from the live era onward.

### 4.6 Cross-domain body-condition filter
A race metric may carry `body_condition` (e.g. `bmi<22`, `body_fat>20`). For each
candidate run, take the player's **most-recent weigh-in on-or-before** the run's
`ended_at` (the body they brought into that race) and evaluate the predicate. Runs before
a player's first weigh-in are **unevaluable** and excluded; the response reports how many
were excluded. No staleness cap (product decision: most-recent regardless of age). This
"measurement as of run R" lookup is the alignment primitive reused by roadmap item 5.

## 5. Architecture

New module `pi/src/stats/`, served by the existing Hono app (route registered in
`pi/src/api/app.ts`). TypeScript, `node:sqlite`.

```
pi/src/stats/
  metrics.ts    Metric registry: declarative metric defs (domain, source, value/agg,
                statuses counted, allowed dimensions).
  resolve.ts    Resolver: {metric, filters, group_by, period, tz, body_condition} ->
                one parameterised SQL query (+ label joins) -> rows. Owns dimension
                filtering, status filtering, the datetime() window, GROUP BY.
  period.ts     {period, tz} -> {start_utc, end_utc} (tz/DST-correct, Monday weeks).
  body.ts       Read-only access to porker.db; UNION-ALL view over the mapped per-person
                tables -> normalised (player_id, ts_epoch, weight, bmi, body_fat, ...);
                dedup; the identity map.
  align.ts      "measurement as of run R" (most-recent on-or-before) + body_condition
                parsing/evaluation.
  pb.ts         was_pb CTE (Section 4.5): the finalize maintainer + one-time backfill
                write the stored runs.was_pb; reads use the column. Reused by #2.
api/stats.ts    Hono routes: /v1/stats/value | /breakdown | /series | /metrics.
```

### 5.1 Metric registry (`metrics.ts`)
Each metric is a declarative record. Illustrative shape:

```ts
type Domain = 'race' | 'body';
type Agg = 'sum' | 'count' | 'avg' | 'min' | 'max' | 'current' | 'change';
type Dimension = 'player' | 'course' | 'character' | 'kart' | 'costume' | 'cc';

interface RaceMetric {
  id: string; domain: 'race';
  agg: Agg;                          // default aggregation
  valueSql: string;                  // e.g. 'SUM(rl.coins)', 'COUNT(*)', 'SUM(p.maxt)'
  statuses?: Array<'finished'|'reset'|'dnf'>;  // undefined = all
  needsLaps?: boolean; needsPoints?: boolean; finishedOnly?: boolean; pbOnly?: boolean;
  dimensions: Dimension[];           // allowed filter / group_by dims
}
interface BodyMetric {
  id: string; domain: 'body';
  column: string;                    // normalised column on the body view
  aggs: Agg[];                       // current | change | min | max
}
```

Adding a broadcast stat is normally **one registry entry**, not new plumbing.

### 5.2 Resolver (`resolve.ts`)
Builds a single parameterised query:
- **FROM/JOIN**: `runs r`; join `run_laps rl` when `needsLaps`; for `driving_time`, a
  subquery `(SELECT run_id, MAX(t_ms) maxt FROM run_points GROUP BY run_id)` joined on
  run.
- **WHERE**: `season_id`, `cc`, status set (from the metric), dimension filters, the
  `datetime(ended_at)` window, and `body_condition` (via `align.ts`).
- **GROUP BY**: the `group_by` dimension; label join for display (courses.display_name,
  players.display_name, or the raw character/kart/costume/cc value).
- Returns `rows: {key, value}[]` and a `total`.

### 5.3 Body source (`body.ts`)
A composed `SELECT ... UNION ALL ...` over the mapped per-person tables, each tagged with
its `player_id`, normalising columns to snake_case and exposing `ts_epoch`. Dedup by
`(player_id, ts_epoch)` (porker has a few exact-duplicate rows). porker.db opened
**read-only** (`{ readOnly: true }`) with a `busy_timeout` so we coexist with the pork
bot's writer; WAL allows concurrent readers. Cross-domain queries use a read-only
`ATTACH` (or a two-step app-side lookup) - implementer's choice; both are cheap at this
data size.

### 5.4 Compute model
Purely **on-demand**. Data is tiny (hundreds-to-low-thousands of runs; full scans with
`datetime()` are sub-millisecond), so no precompute, no cache. The existing WebSocket hub
(`/v1/events`) already emits live moments; graphics re-fetch on those nudges. Escape
hatch if volume ever explodes: normalise timestamps on write + add a cache - noted, not
built. (The one write-time computation is `was_pb` maintenance (Section 4.5), paid once
per finished run, not per read.)

## 6. Metric catalog

### Race metrics
| metric | value | statuses | dims |
|---|---|---|---|
| `attempts` | `COUNT(*)` | all | all |
| `resets` | `COUNT(*)` | reset | all |
| `finishes` | `COUNT(*)` | finished | all |
| `reset_rate` | resets / attempts | all | all |
| `coins` | `SUM(rl.coins)` | all | all |
| `mushrooms` | `SUM(rl.shrooms)` | all | all |
| `driving_time` | `SUM(maxt)` | all | all |
| `best_time` | `MIN(total_time_ms)` | finished | all |
| `avg_finish_time` | `AVG(total_time_ms)` | finished | all |
| `pb_count` | `COUNT(*) WHERE was_pb=1` (stored) | finished | all |
| `time_improvement` | last PB - first PB in window | finished | all |

`coins` uses the signed per-lap delta (net). Gross-vs-net is an open detail (Section 9).

### Body metrics (per person; dims = `player` + period only)
`weight`, `bmi`, `body_fat`, `fat_free_weight`, `subcutaneous_fat`, `visceral_fat`,
`body_water`, `skeletal_muscle`, `muscle_mass`, `bone_mass`, `protein`, `bmr`,
`metabolic_age`. Aggregations: `current` (latest weigh-in), `change` (last - first in
window), `min`, `max`. "Total muscle mass / total body fat" across the roster = sum of
each person's `current`.

### Dimensions
`player` (slug/name -> id), `course` (slug -> id), `character`, `kart`, `costume` (raw
strings on `runs`), `cc`, and the period window. Body metrics accept only `player` +
period. Used as **filters** (equality) and/or **group_by**.

## 7. API surface

Small fixed set of **shape** endpoints, each taking a registry-validated `metric`:

- `GET /v1/stats/value` - one aggregated number.
  `?metric=&<filters>&period=&tz=&body_condition=`
  -> `{ metric, period:{key,tz,start,end}, filters, value, unevaluable? }`
- `GET /v1/stats/breakdown` - grouped rows.
  `?metric=&group_by=&<filters>&period=&tz=&body_condition=`
  -> `{ metric, period, filters, group_by, rows:[{key,value}], total }`
- `GET /v1/stats/series` - time-bucketed.
  `?metric=&bucket=day|week|month&<filters>&period=&tz=`
  -> `{ metric, period, bucket, buckets:[{start,end,value}] }`
- `GET /v1/stats/metrics` - registry introspection: list metrics, their domain,
  aggregations and allowed dimensions (lets graphics/website discover what exists).

Shared query params: `metric`, filters (`player`, `course`, `character`, `kart`,
`costume`, `cc`), `period` + `tz`, `season` (defaults active), `agg` (override the
metric's default where multiple are allowed, e.g. body `current` vs `change`),
`body_condition` (race metrics only).

Worked example:
```
GET /v1/stats/breakdown?metric=coins&group_by=course&period=this_week
    &tz=Australia/Melbourne&player=Luke
-> { "metric":"coins",
     "period":{"key":"this_week","tz":"Australia/Melbourne",
               "start":"2026-06-08T00:00:00+10:00","end":"2026-06-15T00:00:00+10:00"},
     "filters":{"player":"Luke"}, "group_by":"course",
     "rows":[{"key":"Bowser's Castle","value":142},
             {"key":"Mario Bros. Circuit","value":98}],
     "total":240 }
```

## 8. Error handling & validation

- Unknown `metric` -> 400. `group_by`/filter dimension not allowed for the metric (e.g.
  `body_fat` by `course`) -> 400, listing allowed dims.
- Unknown `course`/`player` -> 400. Unknown `tz` -> 400. Malformed `body_condition`
  (not `<col><op><number>`, op in `< <= > >= =`) -> 400. `body_condition` on a body
  metric -> 400.
- Empty result -> 200 with `value: 0` / `rows: []` (not an error).
- `body_condition`: report `unevaluable` count (runs with no prior weigh-in) alongside
  the result so graphics can footnote it.
- porker.db missing/locked -> body + cross-domain endpoints return 503 with a clear
  message; race-only endpoints are unaffected.

## 9. Testing strategy

Vitest, co-located `*.test.ts`, mirroring existing `pi/src` tests. Seed small in-memory /
temp SQLite fixtures.

- `period.ts`: window resolution across tz + DST boundary (AEST<->AEDT), Monday week
  start, month/today/all-time, explicit range.
- `pb.ts`: was_pb reconstruction on a crafted progression (ties are not PBs, first finish
  is a PB, carryover baseline behaves as the seed); the finalize maintainer sets `was_pb`
  on a new PB but not on a slower run and re-derives correctly when an attempt is
  replaced; the one-time backfill matches the CTE; regression-guard that the last was_pb
  equals current `is_pb`.
- `resolve.ts`: SQL builder per metric category - status inclusion (resets counted for
  coins/driving, excluded for finish-time metrics), filters, group_by, the datetime()
  window catching mixed `T`/space formats.
- `body.ts`: UNION normalisation, identity map, dedup of duplicate rows, epoch->window
  filtering; excluded people (Blu/Cbri) absent.
- `align.ts`: most-recent on-or-before selection; unevaluable before first weigh-in;
  body_condition parsing + evaluation.
- `api/stats.ts`: endpoint integration on a seeded DB - value/breakdown/series/metrics
  shapes, validation 400s, the 503 when porker is absent.

## 10. Open details (resolve during planning/implementation)

- **Coins gross-vs-net.** `run_laps.coins` is a signed delta; summing gives net coins.
  Confirm whether "coins collected" should be net (simple) or gross positive-only.
- **"per cm" body ratios.** "muscle mass per cm" implies height, which porker doesn't
  store; heights would be supplied via small config if such ratios are wanted.
- **tz library choice** vs zero-dep resolver (Section 4.2), under CI lockfile constraints.
- **Cross-domain mechanism**: read-only `ATTACH` vs two-step app-side lookup.

## 11. Roadmap recap

1. **Stats engine** (this spec) - registry + resolver + period + body + align + pb;
   value/breakdown/series/metrics; race + body domains + body-condition filter.
2. Sequential stats (resets-since-PB, avg-resets-until-PB, streaks).
3. Track-progress reconstruction (completion-%; avg completion before reset).
4. Screen-time telemetry (engine emits screen intervals -> new table; no AFK cap).
5. Analytical (race x body correlation/regression; reuses align).
