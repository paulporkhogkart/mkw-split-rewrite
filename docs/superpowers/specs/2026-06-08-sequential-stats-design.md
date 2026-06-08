# Sequential Stats (Increment #2) — Design

**Date:** 2026-06-08
**Status:** Approved for planning (covered by the standing approval for the broadcast-stats feature set)
**Builds on:** Increment #1 (`docs/superpowers/specs/2026-06-08-broadcast-stats-engine-design.md`) — reuses the stored `runs.was_pb` column, the metric registry, and the `/v1/stats` surface.

## 1. Goal

Add the broadcast stats that need run **ordering** rather than a GROUP BY: "resets since last PB," "average resets until a PB," and the current reset streak — sliced per player / course / cc, the same way the rest of the engine slices.

These don't fit increment #1's additive resolver (which aggregates independent rows). They require walking each `(player, course, cc)` run history in time order, which is why they were deferred to their own increment.

## 2. Scope

**In:** three new **sequential** metrics, a `resolveSequential()` resolver, and their exposure through the existing `/v1/stats/value` and `/v1/stats/breakdown` routes (no new endpoints).

**Out:** track-progress reconstruction (#3), screen-time (#4), correlations (#5). No schema changes (everything derives from `runs` + the existing `was_pb`).

## 3. Metrics

All three are computed per `(player, course, cc)` group from that group's finished+reset+dnf runs ordered by `datetime(ended_at), id`. A "PB" is a run with `was_pb=1` (the stored flag from #1). A "reset" is `status='reset'` (dnf is treated as a non-finish but is **not** counted as a reset — resets are the headline; dnf is rare/abnormal).

| metric | definition | empty case |
|---|---|---|
| `resets_since_pb` | count of `status='reset'` runs **after** the most-recent `was_pb=1` run in the ordered history | no PB yet → count of all resets in the group |
| `avg_resets_until_pb` | mean resets per **PB-epoch**: partition the ordered history at each `was_pb=1` run; for each epoch ending in a PB, count the resets within it; average over those epochs (i.e. over the number of PBs) | 0 PBs → `null` |
| `current_reset_streak` | number of consecutive `status='reset'` runs at the **tail** of the ordered history (stops at the first finished run scanning backward) | no trailing resets → 0 |

**Worked example** (one group, ordered): `reset, finish(PB), reset, reset, finish(PB), reset` →
- `resets_since_pb` = 1 (one reset after the 2nd PB)
- `avg_resets_until_pb` = (epoch1 resets=1, epoch2 resets=2) ⇒ mean = 1.5
- `current_reset_streak` = 1 (one trailing reset)

## 4. Resolution

`resolveSequential(db, { metric, filters, groupBy, seasonId })` in `pi/src/stats/sequential.ts`:

1. Determine the set of `(player_id, course_id, cc)` groups in scope from the filters (`player`, `course`, `cc`) and `seasonId`.
2. For each group, load its ordered runs: `SELECT status, was_pb FROM runs WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND status IN ('finished','reset','dnf') ORDER BY datetime(ended_at), id`.
3. Compute the metric in JS (clear, and the data is tiny).
4. Shape into the standard `StatResult`:
   - **value** (no `group_by`): requires `player` + `course` filters → one group → single `total`.
   - **breakdown** (`group_by=course` with a `player` filter, or `group_by=player` with a `course` filter): one row per group, labelled by the group-by dimension. `total` = sum for count-like metrics, mean for `avg_resets_until_pb` (or simply the sum of the rows for the count metrics; for the avg metric `total` is the mean across rows, `null` if no rows have a value).

**Period:** sequential metrics describe **current state** (as of now), so they **ignore** the period window. The route still echoes the requested period for response consistency, but does not filter by it. Documented in the response and the metrics catalog.

**Dimensions:** filter/group_by by `player`, `course`, `cc` only (these are per-course-progression stats; character/kart/costume don't define an ordering scope). The registry enforces this.

## 5. Registry + routing

Extend `pi/src/stats/metrics.ts`:
- A new `SequentialMetric` kind `{ id, kind: 'sequential', dimensions: ['player','course','cc'] }`.
- `getMetric` / `listMetrics` include them; `allowsDimension` returns true only for `player|course|cc`.

Extend the `/v1/stats` dispatch (`pi/src/api/stats.ts`):
- `kind === 'sequential'` → `resolveSequential(...)` (mirrors the race/body dispatch).
- `value` requires enough filters to identify a single group (`player` + `course`); otherwise 400 with a clear message.

## 6. Validation / errors

- `resolveSequential` value with insufficient filters (can't isolate one group) → 400 `"<metric> value needs player + course"`.
- `avg_resets_until_pb` with 0 PBs in a group → that row's value is `null` (not an error); `value` returns `null`.
- Unknown metric / disallowed dimension → 400 (reuses the existing `guard`).

## 7. Testing

`pi/src/stats/sequential.test.ts` (unit, in-memory DB):
- The worked example above for all three metrics (single group).
- `resets_since_pb` with no PB → all resets.
- `avg_resets_until_pb` with 0 PBs → null.
- `current_reset_streak` ending on a finish → 0.
- Breakdown by course for a player → one row per course.

`pi/src/api/stats.test.ts` (append):
- `/v1/stats/value?metric=resets_since_pb&player=&course=` returns the expected number.
- `/v1/stats/value?metric=resets_since_pb` (no player/course) → 400.
- `/v1/stats/metrics` includes the sequential metrics with `dimensions:['player','course','cc']`.

## 8. Roadmap position

Increment #2 of 5. Next: #3 track-progress reconstruction (completion-% from trail points), #4 screen-time telemetry, #5 race×body analytics.
