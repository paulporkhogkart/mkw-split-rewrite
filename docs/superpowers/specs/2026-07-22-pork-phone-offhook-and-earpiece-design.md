# Pork Phone: off-hook detection + mobile earpiece verdict

**Date:** 2026-07-22 · **Status:** APPROVED (Paul, 2026-07-22) · **Parent specs:**
`2026-07-12-pork-phone-hotline-design.md` (architecture),
`2026-07-21-pork-phone-public-call-page-design.md` (the live call page — this spec
amends its §2.1 state table and §4 server design).

Two asks from the 2026-07-22 follow-up session. Feature 1 ships code; feature 2 was
investigated and deliberately ships **nothing** — the verdict is recorded here so it is
not re-litigated later.

---

## 1. Feature 1 — off-hook detection

### 1.1 Problem

Lifting the 802's handset with no active call is invisible upstream: the ATA
(Grandstream HT802V2, 192.168.3.226, FXS1, registered to Asterisk 22.10.1 on the Pi)
sends nothing until a call forms. The page shows `idle`, callers claim the line, the
originate rings into an off-hook port, and they wait out a 30 s no-answer. Desired: the
page shows an off-hook state, claims are refused while the handset is up, and everything
recovers when it is cradled.

### 1.2 Why an auto-dial call (mechanism choice)

The ATA knows its own hook state (the port LED), but SIP has **no message for "handset
lifted, no call"** — hook state only ever crosses the wire as a call. Alternatives
considered and rejected:

- **Poll the ATA admin/status page from the Pi** — needs a new Phone-VLAN firewall hole
  (Pi↔ATA is UDP-only today; only the PC reaches 80/443), plus scraping a login-walled
  V2 UI. Rejected.
- **ATA syslog to the Pi** — the ATA can stream logs over UDP and hook events may appear
  in them, but the format is undocumented, firmware-dependent, and lossy. A management-
  plane coupling the line state must not depend on. Rejected.
- **Auto-dial but never answer** (treat a ringing channel as the signal) — the ATA's own
  ring timeout eventually kills the unanswered call and plays reorder while the handset
  is still up, losing the signal mid-off-hook. Rejected.

**Chosen: the answered held channel.** Grandstream's documented "Offhook Auto-Dial"
makes the ATA place a call the instant the handset lifts with no incoming ring; the
hotline app answers that call and holds it. The live channel IS the off-hook state;
its death (Paul cradles the handset) IS the on-hook signal. It rides the exact call
path already trusted end to end.

### 1.3 Signal path

1. **ATA:** FXS PORT1 `Offhook Auto-Dial = 200`, `Offhook Auto-Dial Delay = 0`
   (Paul-in-the-loop config; exact V2 left-sidebar steps given at implementation).
   Call waiting on FXS1 verified **off** (see §1.6 races).
2. **Dialplan** (`extensions.conf.tmpl`), above the `_X.` catch-all:
   `exten => 200,1,Stasis(pork,offhook)` — same context, no Answer() in dialplan (the
   app answers via ARI, so an app that is down fails the call fast).
3. **Hotline app:** new `OffhookWatch` (sibling of `AriPhoneLeg` in `__main__.py`,
   registered on the shared `AriClient`): on `StasisStart` with args `["offhook"]` it
   answers the channel (new `AriClient.answer(channel_id)` helper) and records the
   channel id; on `StasisEnd`/`ChannelDestroyed` for that id it clears it. Wired to
   `Controller.set_phone_offhook(bool)`. ARI-originated phone legs enter Stasis with
   args `["phone"]` and are untouched; `AriPhoneLeg` filters by channel id and ignores
   the offhook channel.
4. **No audio machinery:** no AudioSocket, no externalMedia, no recorder, no
   `CallSession`. The handset mic's RTP lands on Asterisk and is discarded — never
   recorded. The earpiece hears silence (dead line). Note: the dial tone Paul hears
   today on lifting the handset disappears — auto-dial pre-empts it instantly. Accepted
   (Paul, 2026-07-22: default is fine). A synthesized dial tone down the held call is a
   possible later bolt-on, not part of this design.

### 1.4 Server state

- `Controller` gains `_phone_offhook` beside `_phone_reachable`, set by
  `set_phone_offhook(ok)` (publishes composed line state on change, same shape as
  `set_phone_reachable`).
- `line_snapshot()` composition when the lease is idle, in precedence order:
  **unplugged > offhook > idle**. (If the ATA dies while off-hook, both signals flip
  and unplugged wins — correct, since cradling won't help a dead ATA.) Lease states
  (held/ringing/oncall) render as themselves; during a real call there is no auto-dial
  channel, so offhook cannot shadow a call.
- `claim_line()` raises new `PhoneOffhook` when `_phone_offhook` →
  `POST /call/claim` returns `409 {"error": "offhook"}`. Refusal precedence matches
  display precedence: unreachable wins over off-hook (`unplugged` before `offhook`).
- Every transition publishes `line_state: "offhook"` on the public rt feed and in the
  events-WS hello, exactly like `unplugged`.

### 1.5 Page

New state row (amends the parent page spec §2.1; copy rules hold — lowercase, no em
dashes, machinery never mentioned):

| State | Pill | Button | Caption | Sound |
|---|---|---|---|---|
| Off the hook | grey dot · `off the hook` | disabled | `the phone is off the hook` | — |

`syncFromLine()` maps `line.state === "offhook"` to it. A claim that 409s keeps the
existing flow — one `busy.wav`, then `syncFromLine()` — which is correct here too:
dialling an off-hook line gives you a busy tone on a real network, and the resync then
lands on the `off the hook` rendering instead of `line busy`.

### 1.6 Races & failure modes (all fail closed)

| Case | Behaviour |
|---|---|
| Paul lifts during an incoming ring | Standard FXS semantics: off-hook during ring **answers the ring**; auto-dial applies only at dial-tone stage. Physically verified at deploy (test T3); if the HT802V2 ever misbehaves, rollback = clear one ATA field. |
| Paul lifts a beat before a claim's originate arrives | Auto-dial call is up → originate gets 486 fast (call waiting off) → ring fails → lease releases → page resyncs to `off the hook`. Worst case = today's behaviour. Call waiting **on** would instead ring a call Paul cannot answer (hook flash disabled) for 30 s — hence the verify-off step. |
| Ring times out / caller hangs up just as Paul lifts | Incoming call gone → ATA sees plain off-hook → auto-dial fires → off-hook state. Correct. |
| Auto-dial unset / dialplan line missing / watch dead | No signal → page shows idle → claims ring into the off-hook port and fail → today's behaviour exactly. Never worse. |
| Hotline app restarts while off-hook | Stasis returns → dialplan `Hangup()` kills the held channel → Paul hears reorder/silence; state is **lost until he cradles once**. Documented, accepted. Claims meanwhile fail as today. |
| ARI WS dies | Existing `on_dead` → app stop → systemd restart → lines-closed boot; offhook re-detected on next lift. |
| Long off-hook (hours) | SIP session timers refresh the held call (ATA default re-INVITE refresh; Asterisk supports timers). Verified live at deploy (test T6: leave off-hook 30+ min). |
| ATA power-yanked while off-hook | Qualify fails → unplugged (wins precedence); held channel dies too. |

### 1.7 Dev & tests

- Unit: `OffhookWatch` against the existing fake ARI server (StasisStart args routing,
  answer call, channel-death clears, non-offhook channels ignored); controller
  composition + precedence; claim refusal → 409 reason; broadcast + hello include
  `offhook`.
- Bench/dev: token-gated `POST /admin/line-sim?state=offhook|clear` (works in echo
  mode, where no ARI exists) drives `set_phone_offhook` so the page state is
  eyeballable in the `HOTLINE_ECHO=1` loop and from `/test`. Sim endpoint is
  admin-token-gated like `/admin/test-ring`; in real mode it still works (useful for
  page debugging) but the ARI watch immediately re-asserts truth on the next real
  transition — documented as a debug tool, not a control.
- Existing 85+ tests stay green.

### 1.8 Paul-in-the-loop deploy checklist (steps supplied verbatim at implementation)

ATA config (V2 admin UI): set Offhook Auto-Dial + delay, verify call waiting off.
Physical matrix: **T1** lift idle → page flips to `off the hook` ≤ ~1 s · **T2** cradle
→ `idle` · **T3** web call rings → lift → answers normally, two-way audio · **T4** lift
during the claim/ring race → caller fails fast, page recovers to `off the hook` ·
**T5** off-hook then ATA power-yank → `phone unplugged` · **T6** leave off-hook 30 min
→ state holds (session-timer check). Asterisk needs a one-time
`sudo asterisk -rx "dialplan reload"` after the conf rsync.

---

## 2. Feature 2 — mobile earpiece vs speakerphone: investigated, not shipping

**Verdict (verified against current sources, 2026-07-22): no routing UI ships, on any
platform.** Decision chain: Android's web platform cannot do it at all; Paul chose
cross-platform consistency over an iOS-only control; Paul also declined a guidance
line. The page keeps zero output-routing UI and mobile callers get whatever the OS
does (speaker by default; headphones/bluetooth auto-routed by the OS when connected).

The findings, so this stays settled:

- **iOS Safari 16.4+**: `navigator.audioSession.type = "play-and-record"` is the one
  real web mechanism — documented (MDN, W3C Audio Session) to route output to the
  earpiece on mobile, against WebKit's forced-speaker default during `getUserMedia`
  capture. A genuine speaker/earpiece toggle **is possible on iOS alone**; mid-call
  switch timing is undocumented and would need on-device verification. Not built —
  see verdict.
- **Android Chrome**: genuinely out of reach. `AudioContext.setSinkId` exists (110+),
  but Android never enumerates the earpiece as an `audiooutput` device (only the
  virtual default); communications routing is OS-owned; `selectAudioOutput` is
  Firefox-desktop-only. Any toggle would be fake — banned.
- Sources: MDN AudioSession · W3C Audio Session spec · caniuse
  `mdn-api_navigator_audiosession` (iOS 16.4+, no Chrome/Android) · caniuse
  `mdn-api_audiocontext_setsinkid` (Chrome Android yes — but nothing to point it at) ·
  Chrome developers blog `audiocontext-setsinkid`.

If this is ever revisited (e.g. Android exposes the earpiece, or the self-echo issue
escalates before Plan 3's bleeper), the iOS half is shovel-ready: settings-section
segmented toggle, feature-detected via `navigator.audioSession` + coarse pointer,
persisted, flips `type` between `"play-and-record"` and `"auto"`, governs all page
audio so the Test button proves the route; acceptance gate = audible route change on a
real iPhone or the toggle is dropped.

---

## 3. Files touched

- `hotline/server/asterisk/extensions.conf.tmpl` — exten 200 → `Stasis(pork,offhook)`
- `hotline/server/hotline/ari.py` — `answer(channel_id)` helper
- `hotline/server/hotline/__main__.py` — `OffhookWatch` + wiring
- `hotline/server/hotline/controller.py` — `set_phone_offhook`, `PhoneOffhook`,
  snapshot composition
- `hotline/server/hotline/http.py` — claim 409 `offhook`, `POST /admin/line-sim`
- `hotline/server/hotline/static/phone.js` — `offhook` state row (`index.html` /
  `phone.css` only if markup/styling needs it)
- `hotline/server/tests/` — per §1.7
- `hotline/RUNBOOK.md` — ATA field, physical matrix, dialplan-reload step
- Root `CLAUDE.md` — hotline surface row mentions the off-hook state

## 4. Deploy

Only on Paul's explicit go: push → on the Pi (`ssh pi@192.168.4.21`) wait for
`pgrep -f 'deploy/update[.]sh'` to clear → `cd /home/pi/mkw && git fetch origin -q &&
git checkout origin/main -- hotline` → rsync per `hotline/server/deploy/install.sh`'s
rsync line to `/opt/hotline/server/` → **also rsync/install the updated
`extensions.conf` + `sudo asterisk -rx "dialplan reload"`** → `sudo systemctl restart
hotline` → verify `/healthz` + live page (mtime `?v=` stamps cache-bust) → Paul's ATA
config + physical matrix (§1.8).
