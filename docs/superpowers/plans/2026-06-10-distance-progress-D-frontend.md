# Distance Progress v2 — Plan D: Live Dividers + Continuous-Fill Bar

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Render the (already-correct) distance completion as **one continuous fill** with **lap divider ticks placed live** at each in-game `cur_lap` change — replacing the N-equal-segment bar — and remove the temporary debug %.

**Architecture:** The presence hub keeps a per-player `dividers` list (the completion value captured at each in-race lap tick, reset on a new run) and ships it in `PresenceEntry`. The card's `viewModel` exposes `{ fill, dividers }`; `PlayerCard.svelte` draws a single fill bar + tick marks at the divider fractions + the existing live dot.

**Tech Stack:** TypeScript (pi), Svelte (frontend), vitest. Spec: `docs/superpowers/specs/2026-06-10-distance-progress-model-design.md` §6.

**Conventions:** pi tests from `pi/`; frontend tests + `svelte-check` from repo root. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure
- **Modify** `pi/src/presence/hub.ts` — `PresenceEntry.dividers`; per-player divider tracking in `update()`; `offlineEntry` includes `dividers: []`.
- **Modify** `pi/src/presence/hub.test.ts` — divider-tracking test.
- **Modify** `src/lib/playerCard.js` — `viewModel` returns `bar = { fill, dividers }` (replaces `segments`); drop `lapSegments`/`dotPct` equal-segment logic.
- **Modify** `src/lib/playerCard.test.js` — update view-model assertions.
- **Modify** `src/components/PlayerCard.svelte` — continuous fill + tick marks + live dot; remove the N-segment `.lapbar` and the temp `.dbg` debug %.

---

## Task 1: Hub tracks live lap dividers

**Files:** `pi/src/presence/hub.ts`, `pi/src/presence/hub.test.ts`

- [ ] **Step 1: Write the failing test** — append to `pi/src/presence/hub.test.ts` (reuse the file's existing `db()`/hub setup pattern; the completion double here returns a fixed value so dividers are predictable):

```ts
it('records a divider (the live completion) at each in-race lap tick, reset on a new run', () => {
  const seen: PresenceEntry[] = [];
  // completion double: returns 0.3 always, so each lap tick records 0.3
  const hub = new PresenceHub(db(), () => 0.3, () => 1000);
  hub.addSink((m: any) => { if (m.type === 'presence_update') seen.push(m.player); });
  const f = (cur_lap: number, course = 'bc') => ({ screen: 'RACING', course, cur_lap, tot_lap: 3, pos: [1, 2] as [number, number] });
  hub.update(1, f(1)); hub.update(1, f(1));        // lap 1 (no tick yet)
  hub.update(1, f(2));                              // 1->2 tick
  hub.update(1, f(3));                              // 2->3 tick
  expect(seen.at(-1)!.dividers).toEqual([0.3, 0.3]);
  hub.update(1, f(1));                              // lap drops -> new run resets
  expect(seen.at(-1)!.dividers).toEqual([]);
});
```

(`PresenceEntry` is exported from `./hub`; import it in the test if not already. Use player id 1 — the existing hub tests seed a roster including id 1; follow whatever the file's `db()` helper sets up.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd pi && npx vitest run src/presence/hub.test.ts`
Expected: FAIL — `PresenceEntry` has no `dividers`.

- [ ] **Step 3: Implement.** In `pi/src/presence/hub.ts`:

Add `dividers: number[];` to the `PresenceEntry` interface (after `completion`). Add `dividers: []` to the object `offlineEntry` returns. Add a field to the class: `private dividers = new Map<number, number[]>();`. In `update()`, replace the `entry` construction so completion is computed once and dividers are tracked:

```ts
  update(playerId: number, frame: PresenceFrame): void {
    const cur = this.map.get(playerId);
    if (!cur) return;
    const now = this.now();
    const stale = frame.track_state != null && !FRESH_TRACK.has(frame.track_state);
    const completion = this.completion(frame.course, frame.cur_lap, frame.pos, playerId, now, stale, frame.tot_lap);

    const newCourse = frame.course ?? null;
    const newLap = frame.cur_lap ?? null;
    let divs = this.dividers.get(playerId) ?? [];
    const newRun = !cur.online || cur.course !== newCourse || (newLap != null && cur.cur_lap != null && newLap < cur.cur_lap);
    if (newRun) divs = [];
    const advancedInRace = newLap != null && cur.cur_lap != null && newLap > cur.cur_lap && (frame.tot_lap == null || newLap <= frame.tot_lap);
    if (advancedInRace && completion != null) divs = [...divs, completion];
    this.dividers.set(playerId, divs);

    const entry: PresenceEntry = {
      player_id: playerId, name: cur.name, color: cur.color, online: true,
      screen: frame.screen ?? null, course: newCourse,
      character: frame.character ?? null, kart: frame.kart ?? null, costume: frame.costume ?? null,
      cur_lap: newLap, tot_lap: frame.tot_lap ?? null,
      coins: frame.coins ?? null, mushrooms: frame.mushrooms ?? null, resets: frame.resets ?? null,
      completion, pb_ms: this.pbForCourse(playerId, frame.course),
      dividers: divs, final_time: frame.final_time ?? null, updated_at: now,
    };
    this.map.set(playerId, entry);
    this.broadcast({ type: 'presence_update', player: entry });
  }
```

Also clear a player's dividers in `setOffline` (so a reconnect starts clean): `this.dividers.delete(playerId);` before building `off`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd pi && npx vitest run src/presence/hub.test.ts`
Expected: PASS (new test + existing hub tests, whose completion doubles ignore the extra args).

- [ ] **Step 5: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): track live lap dividers per player"
```

---

## Task 2: Card view-model exposes `{ fill, dividers }`

**Files:** `src/lib/playerCard.js`, `src/lib/playerCard.test.js`

- [ ] **Step 1: Write the failing test** — in `src/lib/playerCard.test.js`, the racing-state test currently asserts `vm.segments` / `vm.dotPct`. Replace those assertions with the new `bar` shape. Add/adjust a case:

```js
it("exposes a continuous bar fill + live dividers while racing", () => {
  const e = { online: true, screen: "RACING", course: "Bowsers Castle", cur_lap: 2, tot_lap: 3,
    completion: 0.42, dividers: [0.31], updated_at: 1, name: "P", color: "#888" };
  const vm = viewModel(e, () => 2);
  expect(vm.bar).toEqual({ fill: 0.42, dividers: [0.31] });
});

it("has no bar when not racing/finished", () => {
  const e = { online: true, screen: "MAIN_MENU", updated_at: 1, name: "P", color: "#888" };
  expect(viewModel(e, () => 2).bar).toBeNull();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/lib/playerCard.test.js` (repo root)
Expected: FAIL — `vm.bar` undefined; old `segments`/`dotPct` assertions gone.

- [ ] **Step 3: Implement.** In `src/lib/playerCard.js`: delete `lapSegments` and the `segments`/`dotPct` fields of the returned view-model. Add a `bar` field:

```js
  bar: race ? { fill: e.completion == null ? 0 : Math.max(0, Math.min(1, e.completion)),
                dividers: Array.isArray(e.dividers) ? e.dividers : [] } : null,
```

(where `race = state === "racing" || state === "finished"`, already computed). Remove any now-dead `lapSegments` import/usage.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerCard.js src/lib/playerCard.test.js
git commit -m "feat(card): view-model exposes continuous bar fill + dividers"
```

---

## Task 3: `PlayerCard.svelte` — continuous fill + ticks; remove debug %

**Files:** `src/components/PlayerCard.svelte`

- [ ] **Step 1: Replace the bar markup + drop the debug readout.** In `PlayerCard.svelte`:

Remove the temp debug block: delete the `$: dbgPct = …` reactive line in `<script>` and the `{#if dbgPct != null}<div class="dbg">…</div>{/if}` element and the `.dbg` style rule.

Replace the `{#if vm.segments}…{/if}` block with a single fill bar + ticks + the existing dot (driven by `vm.bar`):

```svelte
    {#if vm.bar}
      <div class="barwrap">
        <div class="bar"><i style="width:{vm.bar.fill * 100}%"></i></div>
        {#each vm.bar.dividers as d}<span class="tick" style="left:{d * 100}%"></span>{/each}
        <span class="live" style="left:{vm.bar.fill * 100}%"></span>
      </div>
    {/if}
```

Replace the `.lapbar`/`.seg` style rules with:

```css
  .bar { height: 4px; background: var(--track); overflow: hidden; border-radius: 1px; }
  .bar > i { display: block; height: 100%; background: var(--pc); }
  .tick { position: absolute; top: 0; width: 1.5px; height: 4px; margin-left: -0.75px; background: var(--panel);
          box-shadow: 0 0 0 0.5px rgba(0,0,0,.35); }
```

Keep the existing `.barwrap` and `.live` rules. (The `.live` dot already sits at `left:{…}%`; it now tracks `vm.bar.fill`.)

- [ ] **Step 2: Type-check**

Run: `npx svelte-check` (repo root)
Expected: 0 errors / 0 warnings (the `vm.segments`/`dbgPct` references are gone; `vm.bar` is what the view-model now returns).

- [ ] **Step 3: Commit**

```bash
git add src/components/PlayerCard.svelte
git commit -m "feat(card): continuous-fill lap bar with live dividers; remove temp debug %"
```

---

## Final verification

- [ ] **pi suite:** `cd pi && npm test` — green.
- [ ] **Frontend suite:** `npx vitest run` (repo root) — green.
- [ ] **Type check:** `npx svelte-check` — 0/0.
- [ ] **Live smoke (manual):** restart the pi server + reload; the card bar fills continuously to the live %, with a tick dropped at each lap line as you cross it, and no debug number.

---

## Self-Review (author checklist — completed)

**Spec coverage:** §6 frontend (continuous fill + live dividers, remove debug %) → Tasks 1-3; §5 "emit dividers live (push completion at each lap tick)" → Task 1 (in the hub, where per-player lap state already lives). **Placeholder scan:** none — code shown for every step; the Task 2/3 edits reference exact existing identifiers (`vm.segments`/`dotPct`/`dbgPct`) to replace, which the implementer reads first. **Type consistency:** `PresenceEntry.dividers: number[]` (Task 1) → `e.dividers` consumed by `viewModel` (Task 2) → `vm.bar.dividers` rendered (Task 3). `bar: { fill, dividers }` shape identical across Tasks 2-3.
