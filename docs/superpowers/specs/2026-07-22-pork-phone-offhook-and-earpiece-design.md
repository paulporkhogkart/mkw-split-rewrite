# Pork Phone: off-hook detection + mobile earpiece verdict

**Date:** 2026-07-22 (rev 2, same day: SNMP replaces the auto-dial call as primary after
Paul challenged the mechanism and re-verification proved him right) · **Status:**
APPROVED design shape 2026-07-22; rev 2 pending Paul's review · **Parent specs:**
`2026-07-12-pork-phone-hotline-design.md` (architecture),
`2026-07-21-pork-phone-public-call-page-design.md` (the live page — this spec amends its
§2.1 state table and §4 server design).

Two asks from the 2026-07-22 follow-up session. Feature 1 ships code; feature 2 was
investigated and deliberately ships **nothing** — the verdict is recorded here so it is
not re-litigated later.

---

## 1. Feature 1 — off-hook detection

### 1.1 Problem

Lifting the 802's handset with no active call is invisible to the site: the page shows
`idle`, callers claim the line, the originate rings into an off-hook port, and they wait
out a 30 s no-answer. Desired: the page shows an off-hook state, claims are refused
while the handset is up, and everything recovers when it is cradled.

### 1.2 Mechanism research (corrected 2026-07-22)

Rev 1 claimed the only signal path was making the ATA place a call (SIP has no hook
message for an idle FXS line — true, but incomplete). Paul challenged it; verification
against Grandstream's current docs found the management plane DOES expose hook state:

- **SNMP — supported and already reachable.** The HT8xx series is in Grandstream's SNMP
  guide supported-products table (v1/v2c/v3; Get/GetNext/GetBulk; traps to 3
  destinations; firmware 1.0.5.11+), and the HT80x admin guide documents the full
  settings block (Enable SNMP, version, port 161, communities, v3 auth/priv). The
  sibling FXS gateway (GXW42xx) publicly demonstrates **per-port hook + registration
  status** in its MIB. SNMP is UDP 161: the existing Pi↔ATA UDP-any firewall rule
  already permits it — zero network changes. The HT8xx MIB file itself is ticket-gated
  by Grandstream, so whether it carries the hook OID is confirmed on the real device
  (§1.4), not from paper.
- **Syslog — real but rejected.** At DEBUG level the ATA emits hook events ("GOING OFF
  HOOK", `FXSLS_ONHOOK/OFFHOOK`). Undocumented format, firmware-dependent, lossy UDP,
  and a debug firehose as the price of admission. Last-resort only.
- **SNMP traps — rejected as the signal.** Trap event lists for HT8xx are undocumented
  and the default trap interval (5 min) suggests keepalive-style reporting. Polling GET
  is deterministic; traps may be enabled later as a bonus, never a dependency.
- **Offhook Auto-Dial held call — demoted to fallback (§1.8).** Works, but costs an ATA
  behavior change (dial tone replaced by a dead line), adds answer-during-ring race
  analysis, and **misses a real case SNMP catches**: far side hangs up while Paul keeps
  holding the handset — no new call fires, so auto-dial shows idle while the port is
  genuinely engaged; an SNMP hook poll reports the truth.

**Chosen: SNMP hook-state poll (primary), gated on one device experiment; auto-dial
held call fully specified as the fallback if the experiment comes back empty.**

### 1.3 Primary design — SNMP hook poll

- **ATA (Paul-in-the-loop, one settings page):** Enable SNMP, v2c, port 161, a strong
  random community string (stored with the other secrets in `/etc/hotline/hotline.env`).
  v2c's plaintext community is acceptable on the isolated Phone VLAN with pinned host
  IPs (same posture as SIP); if the V2 UI offers v3 with authPriv at no extra
  complexity, the implementation may use it — the client library choice decides (plan
  detail).
- **Hotline app:** new `hotline/snmp.py` — a minimal asyncio SNMP GET client
  (hand-rolled v2c BER encode/decode for a single OID is ~100 dependency-free lines,
  matching the service's aiohttp-only posture; pulling a library instead is a plan-time
  call). A `watch_hook` poller task (sibling of `watch_ata` in `__main__.py`) polls the
  hook OID every `HOTLINE_SNMP_POLL_S` (default 2 s) and drives
  `Controller.set_phone_offhook(bool)`.
- **Config (all env, feature off until set):** `HOTLINE_SNMP_HOST` (empty = poller
  disabled), `HOTLINE_SNMP_COMMUNITY`, `HOTLINE_SNMP_HOOK_OID` + the value(s) meaning
  off-hook (exact OID/values supplied by the §1.4 experiment), `HOTLINE_SNMP_POLL_S`.
  Unset config = feature dormant = today's behavior exactly.
- **Fail closed:** SNMP timeout/error/garbage → `set_phone_offhook(False)` — the page
  falls back to today's behavior (claims ring and fail fast), never a stuck closed
  line. Unreachability stays the qualify-driven `unplugged` path; the poller never
  touches it.
- **Latency:** state flips within ~one poll interval (~2–3 s). Claims racing inside the
  window get a fast 486 on the originate (port is off-hook), release, and resync —
  worst case equals today's behavior.

### 1.4 The gating experiment (Paul in the loop, ~10 min, before/with implementation)

Ground truth beats the ticket-gated MIB: enable SNMP on the ATA, then from the Pi
(`sudo apt install snmp` for net-snmp tools):

1. Handset cradled: `snmpwalk -v2c -c <community> 192.168.3.226 > /tmp/onhook.txt`
2. Handset lifted (idle, no call): walk again to `/tmp/offhook.txt`
3. `diff` the walks. A flipping OID = our signal; also confirm it flips during a live
   call and after a far-side hangup with the handset still up.

Outcome A (OID found): set the env config; primary design ships as-is.
Outcome B (nothing hook-shaped flips): primary is dead on this firmware — build the
§1.8 fallback instead; the server/page state machinery (§1.5–1.7) is identical either
way, only the detector swaps.

### 1.5 Server state

- `Controller` gains `_phone_offhook` beside `_phone_reachable`, set by
  `set_phone_offhook(ok)` (publishes composed line state on change, same shape as
  `set_phone_reachable`).
- `line_snapshot()` composition when the lease is idle, precedence:
  **unplugged > offhook > idle**. (ATA dead while off-hook → unplugged wins; cradling
  won't fix a dead ATA.) Lease states (held/ringing/oncall) render as themselves. With
  the SNMP detector the hook is naturally off during a real call — the composition
  already keeps `oncall` on top, and the honest side effect is that after a far-side
  hangup the page shows `off the hook` until Paul cradles, which is exactly true.
- `claim_line()` raises new `PhoneOffhook` when set → `POST /call/claim` returns
  `409 {"error": "offhook"}`. Refusal precedence matches display precedence
  (`unplugged` before `offhook`).
- Every transition publishes `line_state: "offhook"` on the public rt feed and in the
  events-WS hello, exactly like `unplugged`.

### 1.6 Page

New state row (amends the parent page spec §2.1; copy rules hold — lowercase, no em
dashes, machinery never mentioned):

| State | Pill | Button | Caption | Sound |
|---|---|---|---|---|
| Off the hook | grey dot · `off the hook` | disabled | `the phone is off the hook` | — |

`syncFromLine()` maps `line.state === "offhook"` to it. A claim that 409s keeps the
existing flow — one `busy.wav`, then `syncFromLine()` — correct here too: dialling an
off-hook line gives a busy tone on a real network, and the resync then lands on
`off the hook` instead of `line busy`.

### 1.7 Dev & tests

- Unit: SNMP codec (golden request bytes, response decode, malformed-response
  rejection); poller against a fake UDP responder (flip values, timeouts → fail-closed
  False); controller composition + precedence; claim refusal → 409 reason; broadcast +
  hello carry `offhook`.
- Bench/dev: token-gated `POST /admin/line-sim?state=offhook|clear` drives
  `set_phone_offhook` (works in echo mode, where no SNMP/ARI exists) so the page state
  is eyeballable in the `HOTLINE_ECHO=1` loop and from `/test`. In real mode the poller
  re-asserts truth on its next tick — documented as a debug tool, not a control.
- Existing 85+ tests stay green.

### 1.8 Fallback design — Offhook Auto-Dial held call (build only on §1.4 outcome B)

Kept fully specified so outcome B is a detector swap, not a redesign:

- ATA FXS1: `Offhook Auto-Dial = 200`, delay 0; call waiting verified **off** (with it
  on, the lift-vs-ring race rings a call Paul cannot answer — hook flash is disabled —
  for 30 s instead of failing fast with 486).
- Dialplan (`extensions.conf.tmpl`), above the `_X.` catch-all:
  `exten => 200,1,Stasis(pork,offhook)` (no dialplan `Answer()` — the app answers via
  ARI so a down app fails the call fast). One-time `dialplan reload` at deploy.
- `OffhookWatch` (sibling of `AriPhoneLeg`): `StasisStart` args `["offhook"]` → answer
  via new `AriClient.answer(channel_id)` and hold; `StasisEnd`/`ChannelDestroyed` →
  cleared. Live channel = off-hook. No AudioSocket, no recorder, no `CallSession`; the
  handset mic is discarded, never recorded; the earpiece hears a dead line (today's
  dial tone is pre-empted — accepted; a synthesized dial tone is a possible bolt-on).
- Standard FXS semantics make answering real calls safe (auto-dial applies only at
  dial-tone stage; off-hook during ring answers the ring) — physically verified at
  deploy; rollback = clear one ATA field. App restart while off-hook loses the state
  until the next cradle (Stasis returns → dialplan hangs the held call up). Known
  blind spot vs SNMP: far-side hangup with handset still up shows idle.

### 1.9 Failure modes (both detectors; all fail closed)

| Case | Behaviour |
|---|---|
| Detector unconfigured / broken / times out | Off-hook reads False → today's behavior exactly (page idle, claims ring and fail fast). Never worse, never stuck closed. |
| Paul lifts a beat before a claim's originate | Originate gets 486 from the engaged port → ring fails → lease releases → page resyncs to `off the hook` on the next poll/signal. |
| Ring timeout / caller hangs up just as Paul lifts | Port off-hook with no call → detected (SNMP: next poll; fallback: auto-dial fires). |
| ATA power-yanked while off-hook | Qualify fails → `unplugged` (wins precedence). |
| Hotline restart | Boot starts lines-closed as today; state re-detected within one poll (SNMP) or on next lift (fallback). |

### 1.10 Paul-in-the-loop checklist (exact steps supplied at implementation)

§1.4 experiment (SNMP enable + two walks + call/hangup variations) · then per outcome:
env config on the Pi, or the §1.8 ATA field + dialplan reload. Physical matrix either
way: **T1** lift idle → page flips to `off the hook` (≤ ~3 s SNMP / ~1 s fallback) ·
**T2** cradle → `idle` · **T3** web call rings → lift → answers normally, two-way audio
· **T4** lift during claim/ring race → caller fails fast, page recovers to
`off the hook` · **T5** off-hook then ATA power-yank → `phone unplugged` · **T6** leave
off-hook 30 min → state holds.

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

- `hotline/server/hotline/snmp.py` — NEW: minimal async SNMP GET client + hook poller
- `hotline/server/hotline/__main__.py` — poller wiring (and, outcome B only:
  `OffhookWatch`)
- `hotline/server/hotline/config.py` — `HOTLINE_SNMP_HOST` / `_COMMUNITY` /
  `_HOOK_OID` / `_POLL_S`
- `hotline/server/hotline/controller.py` — `set_phone_offhook`, `PhoneOffhook`,
  snapshot composition
- `hotline/server/hotline/http.py` — claim 409 `offhook`, `POST /admin/line-sim`
- `hotline/server/hotline/static/phone.js` — `offhook` state row (`index.html` /
  `phone.css` only if needed)
- `hotline/server/tests/` — per §1.7
- `hotline/RUNBOOK.md` — SNMP experiment, env config, physical matrix
- Root `CLAUDE.md` — hotline surface row mentions the off-hook state
- Outcome B only: `hotline/server/asterisk/extensions.conf.tmpl` (exten 200) +
  `hotline/server/hotline/ari.py` (`answer()` helper)

## 4. Deploy

Only on Paul's explicit go: push → on the Pi (`ssh pi@192.168.4.21`) wait for
`pgrep -f 'deploy/update[.]sh'` to clear → `cd /home/pi/mkw && git fetch origin -q &&
git checkout origin/main -- hotline` → rsync per `hotline/server/deploy/install.sh`'s
rsync line to `/opt/hotline/server/` → add the SNMP env keys to
`/etc/hotline/hotline.env` (values from the §1.4 experiment) → `sudo systemctl restart
hotline` → verify `/healthz` + live page (mtime `?v=` stamps cache-bust) → §1.10
physical matrix. (Outcome B adds the extensions.conf rsync +
`sudo asterisk -rx "dialplan reload"` + the ATA auto-dial field.)
