# Players Page — Design Spec

**Date:** 2026-07-03
**Surface:** `web/` (thekartoff.com) + `pi/` (one new public endpoint)
**Status:** approved for v1; visual design deferred to implementation.

## Overview

A per-player section of thekartoff.com. A single **PLAYERS** nav item opens a roster
grid; each card links to that player's profile at `/players/:slug`. The profile shows a
trophy-cabinet half (card gif + headline standings + full PB table) and a **strategy**
half — a coaching tool that ranks a player's best opportunities to climb each of the
three leaderboards.

The strategy engine is the novel part. All three modes are one per-course table read
through three sort keys, sharing a single difficulty kernel borrowed from the existing
on-fire model (`web/src/lib/fireModel.js`): a fixed ms gap is easy to close when a PB
sits far off the WR (lots of slack) and brutal near the WR. Raw ms gaps lie about
difficulty; normalizing by the fire curve's local spread tells the truth.

## Leaderboard hierarchy (context)

The site ranks players on four axes, in this order of importance:

1. **Turf %** — share of courses currently owned (fastest PB) on the turf map. **Primary.**
2. **Total time** — sum of PBs across all courses.
3. **Golf** — sum of per-course ranks (golf-style, lower is better).
4. **% off WR** — mean gap to world record across a player's PBs.

The three strategy modes are named for the leaderboard each one improves: **GOLF**,
**TURF**, **TIME**. (% off WR is the difficulty kernel, not a mode of its own.)

## Decisions locked

- **Nav / structure:** one `PLAYERS` nav item → `/players` index grid → `/players/:slug`
  profile. The grid is the player picker (no second navbar, no default-player switcher).
  Strategy lives as a section *on* the profile, not a separate page.
- **Index grid order:** fixed, intentional order (canonical `/v1/roster` order). **Not**
  rank-sorted. A hand-picked sequence can be defined later if wanted.
- **v1 scope:** everything below is powered by already-public data
  (`/v1/leaderboard` per course, `/v1/world-records`, `/v1/roster`, `/v1/territory`) plus
  one new public summary endpoint. No porker/stats surface exposed in v1.
- **v2 (out of scope here):** "flavor" stats — most-played character/kart, play counts,
  coins, trends — which need a new public passthrough over the token-gated `/v1/stats/*`
  (porker) surface.
- **Visual design:** deferred to implementation (frontend-design skill, OBS-plain /
  functional-colour house standards). This spec fixes structure + data, not final look.

## Routing & navigation

- Add `PLAYERS` to the navbar in `App.svelte`.
- Extend `lib/view.js` History-API routing (same mechanism as `/turf`, `/heat`,
  `/version`): `/players` → index, `/players/:slug` → profile.

## Backend — one new public endpoint

All profile data requires cross-player aggregation (the player's rank *among all players*
in 4 metrics; the strategy math needs every course's full leaderboard). The client should
not reassemble this from granular calls, so the Pi computes and serves it whole.

- `GET /v1/players/:slug` → summary object (shape below). Keyed by slug (`db/slug.ts`).
- Added to `PUBLIC_READS` + `readCors` in `pi/src/api/app.ts` (token-free, CORS for the
  cross-origin site). The existing token-gated `/v1/players/:id/pbs` is left untouched.
- The **index grid** needs no new endpoint: it reuses the already-public `/v1/roster`
  (names + slugs, in canonical order) and `/v1/territory` (turf % per player for the card
  stat). Card gifs render client-side from `src/lib/playerFigures` keyed by `playerKey`.

### Response shape (`GET /v1/players/:slug`)

```
{
  profile:  { slug, display_name },
  headline: {
    turf:  { pct,       rank },   // primary
    time:  { total_ms,  rank },
    golf:  { points,    rank },
    offwr: { avg_pct,   rank }
  },
  pbs: [
    { course, cc, your_ms, your_rank, wr_ms, off_wr_pct,
      next_rank_ms, gap_to_next_ms,          // rival directly above you
      leader_ms, leader_off_wr_pct }         // course #1
    // ...one per course the player has a PB on
  ],
  strategy: {
    golf:  [ /* GOLF rows,  sorted */ ],
    turf:  [ /* TURF rows,  sorted */ ],
    time:  [ /* TIME rows,  sorted */ ]
  }
}
```

Each metric rank is the player's position when *all* players are ranked on that axis:
turf % (from turf standings, tie-broken by summed track time), total time, golf points,
and avg % off WR. These reuse existing standings computations where possible
(`overallLeaderboard` for total time + golf; turf standings for turf %); avg % off WR is
computed per player from PBs + WRs and ranked across the roster.

## Strategy engine

### Shared difficulty kernel

Reuse `fireBarPct(offPct) = E0 · e^(offPct/K)` from `web/src/lib/fireModel.js` — the
natural spread of time "in play" at a given distance off the WR. A new pure module
`web/src/lib/strategy.js` **imports** it (mirroring how `onFire.js` reuses it — no
duplicated curve).

For a course, given `wr`, the player's `yourTime`, and a target `rivalTime`:

```
gapPct     = (yourTime − rivalTime) / wr × 100   // ms to shave, as % of WR
yourOffPct = (yourTime − wr)         / wr × 100   // how far off WR you sit
ease       = gapPct / fireBarPct(yourOffPct)      // smaller = easier; difficulty-adjusted
```

### The three modes

| Mode | Rows (one per…) | Target rival | Sort key | Row reads |
|------|-----------------|--------------|----------|-----------|
| **GOLF** | course you're not 1st on | PB directly above you | `ease` to next rival ↑ | "shave 0.05s → 5th → 4th" |
| **TURF** | course you don't lead | course #1 (leader) | `ease` to leader ↑, softened by the leader's own off-WR % | "shave 0.4s → take #1 (leader 3% off WR)" |
| **TIME** | every PB you hold | — | `yourOffPct` ↓ | "6.2% off WR here" |

- **GOLF** = cheapest single place to gain (each place = 1 golf point). Exactly one row
  per course, targeting the single next-faster PB — no cluster/leapfrog weighting (that
  would smuggle an arbitrary second target time onto a course). Cheapest ease at the top.
- **TURF** = inverse of the on-fire model. Instead of "is the leader safe?", it's "how
  snuffable is this leader?" — normalized gap to #1, made easier when the leader sits
  further off WR (a soft record is more stealable). Taking #1 flips turf ownership.
- **TIME** = your PBs sorted by largest % off WR — where your total time (the #2 axis)
  bleeds most. This is just `yourOffPct`, the kernel's exponent, read directly.

### Edge cases the math module must handle

- No WR for a course → excluded (kernel undefined without a WR).
- Player is already #1 → excluded from GOLF and TURF (no rival above / no leader to take).
- Player already at/under a rival time (data race) → clamp gap ≥ 0, ease → 0.
- Course with only the player on the board → no next rival, no leader; TIME still applies.

## Profile layout (`/players/:slug`)

```
┌ card gif ┐   Turf %   · #2      Total time · #4
│  (frame) │   Golf     · #3      % off WR   · #5     ← 4 tiles: value + rank, turf first
└──────────┘
── PB table (all courses, sortable) ──
   course · your PB · your rank · WR · Δ WR % · gap to next rank
── Strategy (mode toggle: GOLF | TURF | TIME) ──
```

## Index grid (`/players`)

Roster cards in fixed canonical order. Each card: card gif + display name + turf % (from
`/v1/territory`). Whole card links to `/players/:slug`.

## Files

New:
- `web/src/PlayersIndex.svelte`, `web/src/PlayerProfile.svelte`, `web/src/StrategyPanel.svelte`
- `web/src/lib/strategy.js` + `web/src/lib/strategy.test.js`
- `pi/src/db/playerSummary.ts` + colocated test

Edited:
- `web/src/App.svelte` (nav + view routing), `web/src/lib/view.js` (routes)
- `web/src/lib/api.js` (+ `playerSummaryUrl`)
- `pi/src/api/reads.ts` (+ `GET /v1/players/:slug`), `pi/src/api/app.ts` (PUBLIC_READS entry)

## Testing

- `strategy.js` — fully unit-tested pure math: hand-built leaderboards covering clustered
  rivals, WR-adjacent PBs, missing WR, player-is-#1, single-player board. Assert sort
  order and the ease/off-WR values.
- `playerSummary.ts` — tested against a seeded in-memory DB: headline ranks, PB rows, and
  the three strategy lists resolve correctly, including a player who leads some courses.
- Svelte components — verified in a **real browser** (headless Edge + CDP), never OpenCV,
  per house visual-verification rules.

## Non-goals (explicit)

- No most-played character/kart, coins, play counts, or trend charts in v1 (v2, needs a
  public stats passthrough).
- No cross-player comparison / head-to-head view.
- No editing, auth, or "my page" personalization — this is public read-only.
- No cluster/leapfrog weighting in GOLF (decided against: one row per course).
