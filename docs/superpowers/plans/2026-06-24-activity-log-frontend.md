# Activity Log — Frontend (web) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the activity-log feed beneath the player cards on thekartoff.com's live page, fed by the already-merged `pi/` backend (`GET /v1/activity` history + `/v1/activity/stream` live WS).

**Architecture:** A pure formatter (`activityFormat.js`) maps each `ActivityEvent` → renderable row spans (all wording/colour logic, unit-tested). A feed client (`activityClient.js`, mirroring `presenceClient.js`) loads history then streams live events, both merged into a shared `activity` store via a pure `mergeActivity` (dedup by id, newest-first, capped). `ActivityLog.svelte` is presentation-only: it subscribes to `$activity` and renders `toRow(...)` output in a flat four-column grid, inserted inside `CardWall.svelte` below the card wall.

**Tech Stack:** Svelte 4, Vite, vitest (`web/` tests are pure-lib + WS-client only — no component-render tests exist; do not add a component-test harness).

> **Scope:** This is the feed only (spec Part B + its plumbing). The `PlayerCard` `attempts·timer` change (spec Part A) and its presence fields are a separate **Plan 3** (deferred — its live "session attempts that don't reset on a PB" + screen-dwell counters need a data-source decision).

## Global Constraints

- Tests: vitest, files `**/*.test.js`, run with **`npm --prefix web test`**. `web/` unit-tests **pure lib functions and the injectable WS client only** — there is NO Svelte component-render harness (no @testing-library). Do not introduce one. The component is gated by `npm --prefix web run check` (svelte-check) + `npm --prefix web run build`.
- Stores live in **repo-root `src/lib/stores.js`** (the `writable` pattern); `web/` imports them via `../../src/lib/stores.js`. Add the `activity` store there.
- Theme tokens come from `src/theme.css` (imported by `web/src/main.js`). Use: `--bg`, `--panel`, `--bd`/`--bd-soft` (hairlines), `--tx` (bright nums/times), `--tx-mut` (base/labels/system tags), `--tx-dim` (muted). Font `var(--ui)` (Segoe UI) with `font-variant-numeric: tabular-nums`. **No monospace, no rounding, no shadow.**
- **Colour = player identity only.** A player's name uses `player.color`; the PB row gets a left colour strip in `player.color`; every delta/gap is neutral `--tx-mut`; times are bright `--tx` but uncoloured.
- **Stream/order contract (from the backend):** events carry an ascending `id` = canonical order; `GET /v1/activity` returns newest-first (`id` DESC); the live WS sends one `ActivityEvent` JSON per message in ascending id. The client MERGES everything by id (dedup, sort `id` DESC, cap) so order-of-arrival never matters.

## Backend payload reference (AUTHORITATIVE — use these exact fields)

`ActivityEvent = { id:number, ts:number(epoch ms), type, course:{slug,name}|null, player:{id,name,color}|null, payload }`. `type` ∈ `pb|rank|turf_claim|turf_fire|turf_waver|wr|attempts|screen`. The server resolves `payload.rival_id` → `payload.rival = {id,name,color}`. Exact payloads:

| type | player | payload |
|---|---|---|
| `pb` | the runner | `{ time_ms, time_str, delta_ms }` (delta vs own prev PB, negative) |
| `rank` | the mover | `{ place, rival_id, rival_name, rival_time_ms, gap_ms, rival:{id,name,color} }` (gap positive) |
| `turf_claim` | new #1 | `{ rival_id, rival:{id,name,color} }` (the dethroned) |
| `turf_fire` | leader | `{}` |
| `turf_waver` | leader | `{}` |
| `wr` | **null** | `{ time_ms, time_str, holder:string, delta_ms }` (delta = record drop, negative) |
| `attempts` | the player | `{ count, duration_ms }` |
| `screen` | the player | `{ screen:string, dwell_ms }` (screen is the label e.g. `"character select"`); `course` is null |

## File structure

| File | Responsibility |
|---|---|
| `web/src/lib/activityFormat.js` (create) | pure formatters + `toRow(event, now)` → render spans |
| `web/src/lib/api.js` (modify) | add `activityUrl` + `activityStreamWsUrl` |
| `src/lib/stores.js` (modify) | add the `activity` writable store |
| `web/src/lib/activityMerge.js` (create) | pure `mergeActivity(existing, incoming, cap)` |
| `web/src/activityClient.js` (create) | `loadActivityHistory` + `startActivityStream` + `pushActivity` (mirrors `presenceClient.js`) |
| `web/src/ActivityLog.svelte` (create) | presentation: `$activity` → `toRow` → flat grid |
| `web/src/CardWall.svelte` (modify) | render `<ActivityLog/>` below `.wall`; start/stop the feed on mount |

---

### Task 1: Pure formatter (`activityFormat.js`)

**Files:**
- Create: `web/src/lib/activityFormat.js`, `web/src/lib/activityFormat.test.js`

**Interfaces:**
- Produces: `fmtTime(ms)`, `signedDelta(ms)`, `fmtDuration(ms)`, `relTime(ts, now)`, `ordinal(n)`, and `toRow(event, now)` → `{ id, when, sys:boolean, who:{text,color}, where:{text,dim?}, strip:string|null, what:Span[] }` where `Span = { text, cls:''|'t'|'delta'|'dim'|'name', color? }`.

- [ ] **Step 1: Write the failing test**

```javascript
// web/src/lib/activityFormat.test.js
import { describe, it, expect } from "vitest";
import { toRow, fmtTime, signedDelta, fmtDuration, relTime, ordinal } from "./activityFormat.js";

const ev = (type, over = {}) => ({
  id: 1, ts: 1000, type,
  course: { slug: "crown_city", name: "Crown City" },
  player: { id: 1, name: "Gub", color: "#38bdf8" },
  payload: {}, ...over,
});

describe("helpers", () => {
  it("fmtTime → m:ss.SSS", () => expect(fmtTime(107980)).toBe("1:47.980"));
  it("signedDelta → signed 3dp", () => { expect(signedDelta(-430)).toBe("-0.430"); expect(signedDelta(1118)).toBe("+1.118"); });
  it("fmtDuration → compact", () => { expect(fmtDuration(40000)).toBe("40s"); expect(fmtDuration(7*60000)).toBe("7m"); expect(fmtDuration(64*60000)).toBe("1h 4m"); });
  it("relTime → terse", () => { const now = 1000 + 0; expect(relTime(1000, now)).toBe("now"); expect(relTime(1000, 1000 + 120000)).toBe("2m"); expect(relTime(1000, 1000 + 2*3600000)).toBe("2h"); });
  it("ordinal", () => { expect(ordinal(1)).toBe("1st"); expect(ordinal(2)).toBe("2nd"); expect(ordinal(11)).toBe("11th"); });
});

describe("toRow", () => {
  it("pb → player row with colour strip + neutral delta", () => {
    const r = toRow(ev("pb", { payload: { time_ms: 107980, time_str: "1:47.980", delta_ms: -430 } }), 1000);
    expect(r.sys).toBe(false);
    expect(r.who).toEqual({ text: "Gub", color: "#38bdf8" });
    expect(r.strip).toBe("#38bdf8");
    expect(r.what.map(s => s.text).join("")).toBe("PB 1:47.980 (-0.430)");
    expect(r.what.find(s => s.cls === "t").text).toBe("1:47.980");
  });
  it("rank → system tag, coloured mover+rival, ordinal + gap", () => {
    const r = toRow(ev("rank", { player: { id: 1, name: "Paul", color: "#a78bfa" }, payload: {
      place: 2, rival_id: 3, rival_name: "Aliias", rival_time_ms: 116420, gap_ms: 1118,
      rival: { id: 3, name: "Aliias", color: "#4ade80" } } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.who.text).toBe("Rank");
    const names = r.what.filter(s => s.cls === "name");
    expect(names.map(s => [s.text, s.color])).toEqual([["Paul", "#a78bfa"], ["Aliias", "#4ade80"]]);
    expect(r.what.map(s => s.text).join("")).toBe("Paul took 2nd from Aliias · 1:56.420 (+1.118)");
  });
  it("turf_claim → 'X claimed Y\\'s turf'", () => {
    const r = toRow(ev("turf_claim", { player: { id: 1, name: "Gub", color: "#38bdf8" }, payload: {
      rival_id: 2, rival: { id: 2, name: "Paul", color: "#a78bfa" } } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.what.map(s => s.text).join("")).toBe("Gub claimed Paul's turf");
  });
  it("turf_fire / turf_waver wording", () => {
    expect(toRow(ev("turf_fire"), 1000).what.map(s => s.text).join("")).toBe("the people are rallying behind Gub");
    expect(toRow(ev("turf_waver"), 1000).what.map(s => s.text).join("")).toBe("the people are losing faith in Gub");
  });
  it("wr → grey tag, neutral delta, dimmed 'by holder', null player", () => {
    const r = toRow(ev("wr", { player: null, payload: { time_ms: 89180, time_str: "1:29.180", holder: "Ralph", delta_ms: -220 } }), 1000);
    expect(r.sys).toBe(true);
    expect(r.who.text).toBe("WR");
    expect(r.what.map(s => s.text).join("")).toBe("1:29.180 (-0.220) by Ralph");
  });
  it("attempts → 'N attempts · dur'", () => {
    const r = toRow(ev("attempts", { payload: { count: 19, duration_ms: 7*60000 } }), 1000);
    expect(r.sys).toBe(false);
    expect(r.what.map(s => s.text).join("")).toBe("19 attempts · 7m");
  });
  it("screen → screen label in `where`, dwell in `what`, null course", () => {
    const r = toRow(ev("screen", { course: null, payload: { screen: "character select", dwell_ms: 40000 } }), 1000);
    expect(r.where).toEqual({ text: "character select", dim: true });
    expect(r.what.map(s => s.text).join("")).toBe("40s");
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `npm --prefix web test -- activityFormat`

- [ ] **Step 3: Create `web/src/lib/activityFormat.js`:**

```javascript
// Pure: an ActivityEvent -> renderable row spans. No Svelte, no DOM, no fetch. Unit-tested.
// Colour is player-identity only; deltas/gaps are neutral; times bright-but-uncoloured.

const ORD = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"];
export const ordinal = (n) => ORD[n] || `${n}th`;

export function fmtTime(ms) {
  if (ms == null) return null;
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), x = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(x).padStart(3, "0")}`;
}

export function signedDelta(ms) {
  if (ms == null) return null;
  return `${ms < 0 ? "-" : "+"}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

export function fmtDuration(ms) {
  const s = Math.round((ms ?? 0) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function relTime(ts, now) {
  const s = Math.max(0, Math.floor((now - ts) / 1000));
  if (s < 45) return "now";
  const m = Math.floor(s / 60); if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

const paren = (s) => (s == null ? "" : `(${s})`);
const nameSpan = (n) => ({ text: n.name, cls: "name", color: n.color });

/** ActivityEvent -> a structured row the component renders span-by-span. */
export function toRow(e, now) {
  const when = relTime(e.ts, now);
  const course = e.course?.name ?? "";
  const p = e.player;
  const pay = e.payload || {};
  const base = { id: e.id, when };

  switch (e.type) {
    case "pb":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: course }, strip: p.color,
        what: [{ text: "PB ", cls: "" }, { text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }] };
    case "rank":
      return { ...base, sys: true, who: { text: "Rank", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: ` took ${ordinal(pay.place)} from `, cls: "" }, nameSpan(pay.rival),
               { text: " · ", cls: "dim" }, { text: fmtTime(pay.rival_time_ms), cls: "t" },
               { text: " " + paren(signedDelta(pay.gap_ms)), cls: "delta" }] };
    case "turf_claim":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [nameSpan(p), { text: " claimed ", cls: "" }, { text: pay.rival.name + "'s", cls: "name", color: pay.rival.color }, { text: " turf", cls: "" }] };
    case "turf_fire":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are rallying behind ", cls: "" }, nameSpan(p)] };
    case "turf_waver":
      return { ...base, sys: true, who: { text: "Turf", color: null }, where: { text: course }, strip: null,
        what: [{ text: "the people are losing faith in ", cls: "" }, nameSpan(p)] };
    case "wr":
      return { ...base, sys: true, who: { text: "WR", color: null }, where: { text: course }, strip: null,
        what: [{ text: fmtTime(pay.time_ms), cls: "t" }, { text: " " + paren(signedDelta(pay.delta_ms)), cls: "delta" }, { text: " by " + pay.holder, cls: "dim" }] };
    case "attempts":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: course }, strip: null,
        what: [{ text: `${pay.count} attempts`, cls: "" }, { text: " · " + fmtDuration(pay.duration_ms), cls: "dim" }] };
    case "screen":
      return { ...base, sys: false, who: { text: p.name, color: p.color }, where: { text: pay.screen, dim: true }, strip: null,
        what: [{ text: fmtDuration(pay.dwell_ms), cls: "dim" }] };
    default:
      return null;
  }
}
```

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git add web/src/lib/activityFormat.js web/src/lib/activityFormat.test.js && git commit -m "feat(web): activity-log row formatter"`

---

### Task 2: API URLs + store + pure merge

**Files:**
- Modify: `web/src/lib/api.js`, `src/lib/stores.js`
- Create: `web/src/lib/activityMerge.js`, `web/src/lib/activityMerge.test.js`

**Interfaces:**
- Produces: `activityUrl(before?, limit?)` and `activityStreamWsUrl(apiBase)` in `api.js`; the `activity` writable (init `[]`) in `src/lib/stores.js`; `mergeActivity(existing, incoming, cap=300)` → newest-first, deduped-by-id, capped array.

- [ ] **Step 1: Write the failing test**

```javascript
// web/src/lib/activityMerge.test.js
import { describe, it, expect } from "vitest";
import { mergeActivity } from "./activityMerge.js";

const e = (id) => ({ id, ts: id, type: "pb", payload: {} });

describe("mergeActivity", () => {
  it("merges newest-first by id and dedups", () => {
    const out = mergeActivity([e(3), e(1)], [e(2), e(3)]);
    expect(out.map(x => x.id)).toEqual([3, 2, 1]);
  });
  it("caps to the most recent N", () => {
    const existing = [e(5), e(4), e(3)];
    const out = mergeActivity(existing, [e(6)], 3);
    expect(out.map(x => x.id)).toEqual([6, 5, 4]);
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `web/src/lib/activityMerge.js`:**

```javascript
/** Combine two activity-event lists into one newest-first list: dedup by id, sort by id DESC, cap. */
export function mergeActivity(existing, incoming, cap = 300) {
  const byId = new Map();
  for (const ev of existing) byId.set(ev.id, ev);
  for (const ev of incoming) byId.set(ev.id, ev);
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, cap);
}
```

- [ ] **Step 4: Add the API helpers** to `web/src/lib/api.js` (after the existing `versionUrl` export):

```javascript
export const activityUrl = (before, limit = 100) =>
  `${API_BASE}/v1/activity?limit=${limit}${before != null ? `&before=${before}` : ""}`;
export const activityStreamWsUrl = () =>
  `${API_BASE.replace(/^http/, "ws")}/v1/activity/stream`;
```

- [ ] **Step 5: Add the store** to `src/lib/stores.js` (after the `serverConnection` export):

```javascript
export const activity = writable([]);   // ActivityEvent[] newest-first (merged history + live stream)
```

- [ ] **Step 6: Run, expect PASS. Commit** — `git add web/src/lib/api.js web/src/lib/activityMerge.js web/src/lib/activityMerge.test.js src/lib/stores.js && git commit -m "feat(web): activity api urls, store, merge"`

---

### Task 3: Feed client (history + live stream)

**Files:**
- Create: `web/src/activityClient.js`, `web/src/activityClient.test.js`

**Interfaces:**
- Consumes: `activity` store, `mergeActivity`, `activityUrl`, `activityStreamWsUrl`.
- Produces: `pushActivity(events)` (merges into the store); `loadActivityHistory(apiBase, { fetchImpl })` (async; fetches `GET /v1/activity` → `pushActivity`); `startActivityStream(apiBase, { WebSocketImpl, setTimeoutImpl, clearTimeoutImpl })` (reconnecting WS, mirrors `presenceClient.js`; each message → `pushActivity([event])`; returns `stop()`).

- [ ] **Step 1: Write the failing test** (mirror `presenceClient.test.js`'s FakeWS pattern)

```javascript
// web/src/activityClient.test.js
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import { activity } from "../../src/lib/stores.js";
import { pushActivity, loadActivityHistory, startActivityStream } from "./activityClient.js";

class FakeWS {
  constructor(url) { this.url = url; this.listeners = {}; FakeWS.last = this; }
  addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); }
  emit(t, ev) { (this.listeners[t] || []).forEach((fn) => fn(ev)); }
  close() {}
}
const e = (id) => ({ id, ts: id, type: "pb", payload: {} });

beforeEach(() => { activity.set([]); FakeWS.last = null; });

describe("pushActivity", () => {
  it("merges events newest-first into the store", () => {
    pushActivity([e(1)]); pushActivity([e(3), e(2)]);
    expect(get(activity).map(x => x.id)).toEqual([3, 2, 1]);
  });
});

describe("loadActivityHistory", () => {
  it("fetches /v1/activity and merges the result", async () => {
    const fetchImpl = async () => ({ json: async () => [e(5), e(4)] });
    await loadActivityHistory("http://localhost:8787", { fetchImpl });
    expect(get(activity).map(x => x.id)).toEqual([5, 4]);
  });
});

describe("startActivityStream", () => {
  it("opens the stream ws and prepends a live event", () => {
    const stop = startActivityStream("http://localhost:8787", { WebSocketImpl: FakeWS });
    expect(FakeWS.last.url).toBe("ws://localhost:8787/v1/activity/stream");
    FakeWS.last.emit("message", { data: JSON.stringify(e(9)) });
    expect(get(activity)[0].id).toBe(9);
    stop();
  });
  it("stop() prevents reconnect", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    const stop = startActivityStream("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    stop();
    FakeWS.last.emit("close");
    expect(scheduled).toBeNull();
  });
});
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Create `web/src/activityClient.js`** (mirrors `web/src/presenceClient.js`):

```javascript
// Read-only activity-log client for the public website: loads recent history via REST then
// streams live events from /v1/activity/stream (token-less, receive-only). Both paths merge
// into the shared `activity` store by id, so order-of-arrival never matters. Mirrors the
// reconnect/backoff of presenceClient.js. WebSocket/timers/fetch are injectable for tests.
import { activity } from "../../src/lib/stores.js";
import { mergeActivity } from "./lib/activityMerge.js";
import { activityUrl, activityStreamWsUrl } from "./lib/api.js";

const MAX_BACKOFF_MS = 30000;

export function pushActivity(events) {
  activity.update((cur) => mergeActivity(cur, events));
}

export async function loadActivityHistory(apiBase, { fetchImpl = fetch, limit = 100 } = {}) {
  try {
    const res = await fetchImpl(`${apiBase}/v1/activity?limit=${limit}`);
    const list = await res.json();
    if (Array.isArray(list)) pushActivity(list);
  } catch { /* offline / server down: leave the store as-is */ }
}

export function startActivityStream(apiBase, {
  WebSocketImpl = WebSocket, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
} = {}) {
  const url = `${apiBase.replace(/\/+$/, "").replace(/^http/, "ws")}/v1/activity/stream`;
  let ws = null, closed = false, backoff = 1000, timer = 0;

  function connect() {
    if (closed) return;
    ws = new WebSocketImpl(url);
    ws.addEventListener("open", () => { backoff = 1000; });
    ws.addEventListener("message", (ev) => {
      try { pushActivity([JSON.parse(ev.data)]); } catch { /* ignore malformed frame */ }
    });
    ws.addEventListener("close", () => {
      if (closed) return;
      if (timer) clearTimeoutImpl(timer);
      timer = setTimeoutImpl(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
  }
  connect();

  return function stop() {
    closed = true;
    if (timer) clearTimeoutImpl(timer);
    try { ws?.close(); } catch { /* ignore */ }
  };
}
```

(`activityStreamWsUrl`/`activityUrl` from Task 2 are exported for reuse; this client inlines the same derivation so the URL is testable without the API_BASE default.)

- [ ] **Step 4: Run, expect PASS. Commit** — `git add web/src/activityClient.js web/src/activityClient.test.js && git commit -m "feat(web): activity feed client (history + live stream)"`

---

### Task 4: `ActivityLog.svelte` + CardWall insertion

**Files:**
- Create: `web/src/ActivityLog.svelte`
- Modify: `web/src/CardWall.svelte`

**Interfaces:**
- Consumes: `activity` store, `toRow`/`relTime` from `activityFormat.js`, `loadActivityHistory`/`startActivityStream` from `activityClient.js`, `API_BASE` from `lib/api.js`, the shared `now` clock passed from `CardWall`.
- Produces: the rendered feed; no test (presentation; gated by svelte-check + build).

- [ ] **Step 1: Create `web/src/ActivityLog.svelte`:**

```svelte
<script>
  import { onMount, onDestroy } from "svelte";
  import { activity } from "../../src/lib/stores.js";
  import { toRow } from "./lib/activityFormat.js";
  import { API_BASE } from "./lib/api.js";
  import { loadActivityHistory, startActivityStream } from "./activityClient.js";

  export let now = Date.now();   // shared clock from CardWall (for relative "when")

  $: rows = $activity.map((e) => toRow(e, now)).filter(Boolean);

  let stop = () => {};
  onMount(() => { loadActivityHistory(API_BASE); stop = startActivityStream(API_BASE); });
  onDestroy(() => stop());
</script>

{#if rows.length}
  <section class="log" aria-label="Activity log">
    {#each rows as r (r.id)}
      <div class="row" class:pb={!!r.strip} style={r.strip ? `--pc:${r.strip}` : ""}>
        <div class="when">{r.when}</div>
        <div class="who" class:sys={r.sys} style={r.who.color ? `color:${r.who.color}` : ""}>{r.who.text}</div>
        <div class="where" class:dim={r.where.dim}>{r.where.text}</div>
        <div class="what">{#each r.what as s}<span class={s.cls} style={s.color ? `color:${s.color}` : ""}>{s.text}</span>{/each}</div>
      </div>
    {/each}
  </section>
{/if}

<style>
  .log { max-width: 720px; margin: 22px auto 40px; padding: 0 18px; }
  .row { display: grid; grid-template-columns: 42px 74px 150px 1fr; align-items: baseline; column-gap: 12px;
         padding: 7px 14px 7px 12px; border-bottom: 1px solid var(--bd-soft); border-left: 2px solid transparent;
         background: var(--panel); font-size: 12.5px; }
  .row:first-child { border-top: 1px solid var(--bd-soft); border-top-left-radius: var(--r); border-top-right-radius: var(--r); }
  .row:last-child { border-bottom-left-radius: var(--r); border-bottom-right-radius: var(--r); }
  .row.pb { border-left-color: var(--pc); }
  .when { font-size: 11px; color: var(--tx-dim); text-align: right; white-space: nowrap; }
  .who { font-weight: 600; color: var(--tx-mut); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who.sys { font-size: 10px; font-weight: 700; letter-spacing: .11em; text-transform: uppercase; color: var(--tx-dim); }
  .where { color: var(--tx-mut); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .where.dim { color: var(--tx-dim); }
  .what { color: var(--tx-mut); }
  .what :global(.t) { color: var(--tx); font-weight: 600; }
  .what :global(.delta) { color: var(--tx-mut); }
  .what :global(.dim) { color: var(--tx-dim); }
  .what :global(.name) { font-weight: 600; }
</style>
```

- [ ] **Step 2: Insert into `web/src/CardWall.svelte`** — import it and render below the `.wall`. Add to the `<script>` imports:

```javascript
import ActivityLog from "./ActivityLog.svelte";
```

And change the markup so the log renders beneath the wall (keep the existing `{#if players.length}` block; the log sits after it, inside the same component output):

```svelte
{#if players.length}
  <div class="wall">
    {#each players as p (p.player_id)}
      <div class="cell"><PlayerCard entry={p} {now} stale={!connected} /></div>
    {/each}
  </div>
{:else}
  <div class="empty">Connecting to the season server…</div>
{/if}

<ActivityLog {now} />
```

(`now` is the shared clock already defined in `CardWall`; passing it keeps the "when" column live without a second timer.)

- [ ] **Step 3: Gate — svelte-check + build + tests**

Run: `npm --prefix web run check`  → Expected: 0 errors / 0 warnings.
Run: `npm --prefix web run build` → Expected: build succeeds.
Run: `npm --prefix web test`       → Expected: all green (formatter + merge + client suites).

- [ ] **Step 4: Commit** — `git add web/src/ActivityLog.svelte web/src/CardWall.svelte && git commit -m "feat(web): ActivityLog feed under the card wall"`

---

## Final verification

- [ ] `npm --prefix web test` — all green.
- [ ] `npm --prefix web run check` — 0/0.
- [ ] `npm --prefix web run build` — succeeds.
- [ ] (Manual, user) `npm --prefix pi run dev` + `npm --prefix web run dev`, open the live page: the feed renders the backfilled history beneath the cards; posting a run shows a live cascade prepended on top.

## Self-review notes (author)

- **Spec coverage (Part B):** four-column grammar ✓(T1 toRow + T4 grid); colour=names + PB strip + neutral deltas ✓(T1 spans + T4 CSS); history + live merged by id ✓(T2 mergeActivity + T3 client); flat/hairline/tabular theme ✓(T4 uses `--panel`/`--bd-soft`/`--tx*`/`var(--ui)`). **Deferred:** the `PlayerCard` card change + presence fields = Plan 3.
- **Type consistency:** the real backend payloads (pb/rank/turf/wr/attempts/screen) drive `toRow`; `rival` is the server-resolved `{id,name,color}`; `wr.player` is null (the WR row uses a grey tag, holder string in payload).
- **No component-test harness added** (matches `web/` convention): the testable logic is all in `activityFormat.js` / `activityMerge.js` / `activityClient.js`; `ActivityLog.svelte` is presentation, gated by svelte-check + build.
- **Contract:** ascending-publish + merge-by-id-DESC means a live burst lands in correct newest-on-top order regardless of arrival timing.
