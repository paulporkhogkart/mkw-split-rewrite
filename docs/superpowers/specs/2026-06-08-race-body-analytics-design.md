# Race × Body Analytics (Increment #5) — Design

**Date:** 2026-06-08
**Status:** Approved for planning (standing approval).
**Builds on:** Increments #1–#4. Reuses the porker read + the as-of-run alignment idea from #1.

## 1. Goal

The "would be nice" from the brainstorm: does a player's **lap time improve as their body composition changes** — e.g. "time vs body fat." Expose a **Pearson correlation + linear regression** over `(body-value-as-of-run, finish-time)` pairs.

## 2. Scope

**In:** a `/v1/stats/correlation` endpoint + `resolveCorrelation`. **Per (player, course)** only — finish times across different courses aren't comparable, so correlating them would be meaningless. Reuses `openPorker` + `BODY_SOURCE_COLUMNS` + `PORKER_MAP` from #1.

**Out:** multi-variate analysis, per-lap correlation, charts. No registry metric (the output shape — `n, r, slope, intercept` — differs from the engine's value/breakdown/series).

## 3. Algorithm

`resolveCorrelation(mkwDb, porkerDb, { body, player, course, period, seasonId, cc })`:
1. Resolve `player` → id + display_name, `course` → id. `body` → porker column via `BODY_SOURCE_COLUMNS`. The player's porker table via `PORKER_MAP` (by display_name); if absent → `n = 0`.
2. Finished runs for `(season, player, course, cc)` in the period (on `ended_at`) with a non-null `total_time_ms`: select `y = total_time_ms` and `ep = strftime('%s', datetime(ended_at))` (robust epoch, mixed formats).
3. For each run, the player's **most-recent weigh-in on-or-before** `ep`: `SELECT <col> FROM <table> WHERE Timestamp <= ep ORDER BY Timestamp DESC LIMIT 1`. Runs with no prior weigh-in are dropped.
4. `pearson(pairs)` over `[body_value, time_ms]` → `{ n, r, slope, intercept }` (pure). `r/slope/intercept` are `null` when `n < 2` or variance is 0.

A negative `r` means time drops as the body metric drops (e.g. fat down → faster); the sign is reported raw for the client to phrase.

## 4. Code

`pi/src/stats/correlation.ts`: `pearson(pairs)` (pure) + `resolveCorrelation(mkw, porker, q)`. Route in `stats.ts`: `GET /v1/stats/correlation?body=&player=&course=&period=&tz=` → opens a read-only porker connection (503 if unavailable), 400 if `body`/`player`/`course` missing. Returns `{ body, filters:{player,course}, period, n, r, slope, intercept }`.

## 5. Testing

`pi/src/stats/correlation.test.ts`:
- `pearson`: a perfect line `(16,180),(18,190),(20,200)` → `r=1, slope=5, intercept=100`; `n<2` → nulls; zero-variance → null `r`.
- `resolveCorrelation`: 3 finished runs (times 200/190/180 k-ms) with as-of body-fat 20/18/16 → `n=3, r≈1, slope≈5000`; a run before the first weigh-in is dropped (`n` excludes it).

`pi/src/api/stats.test.ts` (append): `/v1/stats/correlation` over an attached porker fixture returns the expected `n`/`r`; missing params → 400; no porker → 503.

## 6. Roadmap position

Increment #5 of 5 — the last. Completes the broadcast-stats feature set (engine + sequential + reconstruction + screen-time + analytics).
