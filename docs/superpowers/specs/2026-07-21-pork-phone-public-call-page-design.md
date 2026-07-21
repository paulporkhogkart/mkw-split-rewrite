# Pork Phone public call page (phone.thekartoff.com)

**Date:** 2026-07-21 · **Status:** DRAFT (pending Paul's review) · **Parent spec:**
`2026-07-12-pork-phone-hotline-design.md` (Phase 1 complete; this is the "spiced up page"
step between Phase 1 and the Plan 2 Twitch product) · **Locked mockups:**
`docs/design/pork-phone-call-page/states-locked.html` (v9 states round, decision truth for
copy/colours) + `layout-reference.html` (layout round, option A "plain" chosen).

**Amends the parent spec:** §10 placed the caller page in the `web/` SPA. Decision 2026-07-21:
the caller page is its own tiny site served by the hotline service at the root of
`phone.thekartoff.com`. Rationale: same-origin (no CORS surface at all), atomic page+server
deploys, the whole Pork Phone stays one self-contained appliance for the §13 migration, and
"phone.thekartoff.com" is a better thing to say on stream than a hidden path. The `web/` SPA
is untouched; thekartoff.com can grow a link later. Plan 2 (Twitch OAuth, queue, console)
builds on this page; the OAuth cookie plan (`Domain=.thekartoff.com`) spans the subdomain
unchanged.

## 1. Goal

Turn the bench `/test` page into a public, obvious, phone-feeling call page: anyone with the
URL can ring the 802 (no token, no account), one caller at a time with fair recovery when a
tab is parked or dies, callers can set up and test their audio before ever touching the line,
and the page sounds like a real phone call (MicroSIP SFX). The raw `/test` bench page stays,
token-gated, for debugging.

## 2. What the user sees

Single centred column, plain dark page (site ground `#0b0c0e`, Inter, lowercase voice).
No decoration beyond the controls. Reference: `states-locked.html`.

- **Header row:** brand `pork phone` (lowercase, weight 800) top-left; line-status pill
  top-right: 8px dot left of lowercase text.
- **Call button:** flat 78px circle, 48px white handset icon (exact 2x of the 24px vector
  grid; never fractional-scale the icon). Green `#16a34a` = press to call; red `#dc2626` =
  hang up (ringing or on call); recessed `--ink-2` with grey icon = disabled (busy/unplugged).
  Hover brightens (`filter:brightness(1.12)`), press dims. No bevels, no shadows, no pulse.
- **Caption under the button:** lowercase, muted.
- **Settings section** below (titled "Settings"): microphone picker + live level meter +
  "Test" button; speaker picker + "Test" button; volume slider. Native `<select>`s, restyled
  dark. Usable any time, including mid-call and while the line is busy.

### 2.1 State table (locked wording, v9 + final round)

| State | Pill | Button | Caption | Sound |
|---|---|---|---|---|
| Idle (line free) | grey dot · `idle` | green, handset | `press to call` | — |
| Ringing (you) | grey dot (twitching in bell cadence) · `ringing…` | red, handset rotated 135° | `hang up` | `ringing.wav` looped |
| On call (you) | green dot · `on call · m:ss` | red | `hang up` | live call audio |
| Line busy (someone else) | grey dot · `line busy` | disabled | `wait for their call to end` | optional quiet tick on change |
| No answer (rang out, 30s) | grey dot · `idle` | green | `no answer`, fades back to `press to call` after ~4s | `hangup.wav` once |
| Call ended | grey dot · `idle` | green | `press to call` | `hangup.wav` once |
| Phone unplugged | grey dot · `phone unplugged` | disabled | `not taking calls right now` | — |

Copy rules: captions and statuses lowercase; page copy never uses em dashes; the internal
lease/claim machinery never appears in copy (no countdowns, no "slots").

The ringing pill's dot twitches in the real AU double-ring cadence (400/200/400/2000 ms),
matching what the physical bell is doing.

### 2.2 First call, zero setup

Meet-style: pressing call on a fresh browser asks for mic permission right then and uses
default devices. Settings exist for those who care. No setup gate.

### 2.3 Settings behaviour

- Mic is live **only** during a call or a mic test. Mic test holds the mic while the meter
  runs (released on stop/navigate away); no self-monitor loopback in v1.
- Speaker test plays `ringtone.wav` once through the chosen output at the chosen volume.
- Output device via `AudioContext.setSinkId` (Chromium); fallback for browsers without it:
  route through a `MediaStreamAudioDestinationNode` + `<audio>` element `setSinkId`; if
  neither exists (Firefox), hide the output picker and use the default device.
- Volume = a `GainNode` on the playback path, applied to call audio and all SFX.
- Persistence: `localStorage` keys for input deviceId, output deviceId, volume. Restored on
  load; device ids that no longer resolve fall back to default silently.
- Device lists populate from `enumerateDevices` (labels appear once mic permission has been
  granted at least once); refresh on `devicechange`.

## 3. Sounds

Committed to `hotline/server/hotline/static/sfx/` (from `temp/phonesfx`, MicroSIP defaults):

| File | Envelope | Use |
|---|---|---|
| `ringing.wav` | 1s tone + 4s silence, complete ringback cycle | loop while ringing, stop on answer/timeout/hangup |
| `hangup.wav` | 0.2s disconnect beep | once on any call end (incl. no answer) |
| `ringtone.wav` | AU double-ring, 3.0s | speaker test sound |
| `msgin.wav` / `msgout.wav` | short ticks | optional quiet line-state ticks for idle watchers; wired only if they sound good at build, off by default otherwise |

`ringing2.wav` (call-waiting blip) is unused. All SFX play through the chosen output at the
chosen volume via WebAudio (decoded once, not `<audio>` tags).

## 4. Server design

### 4.1 The line lease

One in-memory lease guards the single caller slot (replaces "first WS wins, holds forever"):

```
IDLE ──claim──► HELD ──ring──► RINGING ──answer──► ONCALL
  ▲               │10s no ring     │30s no answer      │
  └───────────────┴────────────────┴────────────────────┴── release
```

- `POST /call/claim` → `{lease_id}` (uuid) or `409 {reason: busy|unplugged}`.
- The audio WS (`/ws/audio?lease=<id>`) requires a valid lease. The admin token also still
  works there (`?token=`) so the bench `/test` page keeps functioning; a token connection
  bypasses the lease but still occupies the single caller slot.
- `POST /call/ring?lease=<id>` starts the call (controller.test_ring internals, ring
  timeout 30s). `POST /call/hangup?lease=<id>` ends it — hangup requires the lease, so
  strangers can't kill someone else's call.
- Lease released on: hangup (either side), ring timeout, claim window expiry (10s without
  ring), audio WS drop past a 15s reconnect grace, or the 30-minute absolute backstop
  (zombie insurance; not a talk cap). Every release publishes line state.
- Client sequence: press call → `getUserMedia` (permission prompt happens **before**
  claiming, so slow first-timers never sit on the lease) → claim → open audio WS → ring.
  Claim-race loser gets 409 busy and the page flips to line busy.
- Leases are memory-only; a service restart drops them (pages resync from the events feed).

### 4.2 Events feed goes public

`/ws/events?feed=rt` becomes token-free and read-only (delayed feed stays token-gated with
the admin surfaces). The bus gains `line_state` publishes:

```json
{"type": "line_state", "state": "idle|held|ringing|oncall|unplugged", "since": <epoch>}
```

The lease id is deliberately **not** broadcast (it is the credential for ring/hangup; leaking
it would let any viewer kill someone else's call). A page knows whether the line is *its own*
because it holds its lease locally; anyone else seeing `held|ringing|oncall` renders line
busy. If a page's lease has expired server-side, its next ring/hangup gets 404 and the page
resyncs to the broadcast state. The timer runs from `since`. Current state is also sent as a
hello on events-WS connect so a fresh page renders correctly without waiting for a
transition.

### 4.3 Unplugged detection

The ARI client watches the ATA's PJSIP endpoint reachability (qualify is already on;
frequency tuned to ~15-30s, plus ARI endpoint state events if available at build). ATA
unreachable → `line_state: unplugged`, claims refused 409; reachable again → idle. Paul's
workflow: yank the ATA's power to close the line; unplugging only the 605 cord is
electrically invisible (rings into nothing → callers see no answer at 30s).

### 4.4 Auth surface after this change

| Surface | Auth |
|---|---|
| `/` (new page), `/static/*`, `/healthz` | public |
| `POST /call/claim` / `ring` / `hangup`, `/ws/audio` | lease-gated (public to acquire), Origin-checked |
| `/ws/events?feed=rt` | public read-only, Origin-checked |
| `/admin/test-ring`, `/admin/hangup`, `/test`, `feed=delayed` | admin token (unchanged) |

Origin check: reject browser connections whose `Origin` header is present and not in the
allowlist (`https://phone.thekartoff.com`, `http://127.0.0.1:<port>` for dev) — stops random
websites ringing the phone from a visitor's browser. Non-browser clients (no Origin) can
still hit the public endpoints; that's accepted for this phase (the URL is the gate, same as
today's unlisted posture).

`HOTLINE_ADMIN_TOKEN` stays required in prod for the admin surfaces.

### 4.5 Failure modes (all fail closed, all end in a free line)

| Failure | Behaviour |
|---|---|
| Tab closed mid-ring/mid-call | WS drop → 15s grace → hangup phone leg, release, `hangup.wav` for nobody, line idle |
| Claim race | Second claim 409s; page shows line busy |
| ATA dies mid-call | Phone leg drops → call ends → release |
| Service restart | Leases vanish, lines closed on boot (existing), pages resync via events WS reconnect |
| Ring while unplugged undetected yet | Ring times out at 30s → no answer; detection catches up within the qualify window |

## 5. Files touched

- `hotline/server/hotline/static/` — new `index.html` + `phone.js` + `phone.css` +
  `sfx/*.wav`; `test.html` untouched.
- `hotline/server/hotline/http.py` — `/` route, lease endpoints, public events feed,
  Origin checks, audio WS lease auth.
- `hotline/server/hotline/controller.py` — lease state machine, release hooks into the
  existing call teardown paths, 30s ring timeout.
- `hotline/server/hotline/ari.py` — endpoint reachability watch → bus publishes.
- `hotline/server/hotline/config.py` — allowlisted origins, timeout knobs (claim window,
  grace, ring timeout, backstop) with the defaults above.
- `hotline/server/tests/` — lease machine (claim/expire/race/backstop), endpoint auth
  matrix, line_state broadcasts, Origin rejection.
- Root `CLAUDE.md` hotline row + `hotline/RUNBOOK.md` — the page's existence, the
  ATA-power-yank workflow.

## 6. Testing

- pytest: everything in §5's test list; existing 58+ tests stay green.
- Manual: `HOTLINE_ECHO=1 python -m hotline` → `http://127.0.0.1:9100/` full loop (claim,
  ring, echo call, hangup, SFX, settings persistence, two-tab busy view) with zero
  telephony; then Pi deploy per runbook and a real ring from outside the LAN.

## 7. Out of scope

Twitch OAuth/points/queue (Plan 2), the `/console`, bleeper daemon (Plan 3), any
thekartoff.com link to the page, caller identity/names (phase stays anonymous), call length
caps (Twitch phase owns that), mic self-monitor, mobile-specific work beyond the layout
being a single column that happens to fit.
