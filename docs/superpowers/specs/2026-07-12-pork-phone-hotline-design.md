# The Pork Phone — viewer call-in hotline (security-first rebuild)

**Date:** 2026-07-12 · **Status:** **APPROVED 2026-07-12** (Paul's sign-off; decisions
recorded in §15) · **Supersedes:** `2026-07-11-pork-phone-hotline-design.md`
(rev 3, kept in history) · **Surfaces:** new `hotline/` service (Pi) + bleeper daemon
(streaming PC) + unlisted `web/` page + one ATA + **Paul's Telecom 802 rotary phone**.
No VPS, no monthly cost, no radio anywhere in the design, **no inbound connection to the
house anywhere in the design**.

The bit: viewers spend channel points on the KART-OFF site to ring a real rotary phone on
Paul's desk, live on stream — real bell, real horn, real narrowband crust. Paul answers by
lifting the horn; the conversation is fully real-time both ways; a radio-station-grade
**broadcast delay** plus a desk **DUMP button** keeps anything bad off the air.

**Why this rebuild.** Rev 3 had already replaced the US DECT phone with the rotary and the
VPS with the Pi, but it treated security as a privacy footnote: it had no threat model, no
network topology, and no answer to "what happens when I'm big enough to be a target." This
document restarts the architecture with security as a first-class driver. The **product
behavior of rev 3 carries over as requirements** (Paul's decision, 2026-07-12); its
bench-verified *facts* (bell specs, adaptor direction, tunnel-carries-WebSockets, Twitch API
mechanics) carry over as inputs. Everything structural was re-derived.

---

## 1. Answers to the security brief (index)

| Paul's question (2026-07-12) | Answer | Section |
|---|---|---|
| Is an ATA on the LAN a risk? | Modest and indirect — IoT-class firmware, but it needs zero internet and nothing inbound can reach it. Neutralized by the PHONE zone + first-boot hardening + egress block. It is an interior room, not a front door. | §3, §4, §5.2 |
| Was Grandstream-over-Cisco an artifact of the VPS-era plan? | No — the Cisco SPA112 was rejected for being end-of-life (support ended May 2025). An unpatchable device is *worse* on a home LAN. The HT802V2 pick stands, strengthened. | §5.2 |
| Is the phone server talking to my OBS PC a risk? | It would be — so in this design **they never talk on the LAN at all**. Overlay + call events reach the PC via the public URL through Cloudflare; call audio reaches the stream as analog electricity. | §4, §7 |
| Isolate the ATA from the main LAN — or pointless? | Worth doing and nearly free (PHONE VLAN), but the box that *earns* isolation is the Pi — it runs the internet-reachable code. Both get zones. | §3, §4 |
| Audio pipeline to mimic the vintage sound vs hardware capture + isolation? | False trade — hardware capture is *less* engineering **and** enables full isolation. The line coupler sound is a step more authentic than the digital tap; the earpiece-sim EQ drops to optional garnish. | §7 |
| Is this an overreaction? | No — but aim it at the ranked threat model: PC account theft and Paul's own public code outrank the ATA by a wide margin. The cheap absolutes close the doors that matter. | §3 |
| Move the sites/services off the Pi before I ever stream? | Yes — named pre-first-stream milestone in the posture roadmap. The hotline is built so that move is a DNS/deploy event, not a rewrite. | §13 |

---

## 2. Requirements

Carried from rev 3 (product) plus new security requirements (this rebuild):

1. A physical phone on Paul's desk rings with real, two-way calls placed by viewers from a
   web page — **Paul's Telecom 802 rotary**. No real phone number. Nothing that exposes
   Paul's home IP, address, or identity to callers.
2. **Channel points → N seconds** of call time, with proof that the spender is the caller.
   Real money later; possibly YouTube later.
3. **Bleep/censor** workable by one person, manual-reactive (broadcast delay + hovering
   finger; no automatic detection in v1); **blacklist** anyone who forces a bleep.
4. Call page on the existing site, unlisted; viewer identity via platform login.
5. **Chat hears what Paul hears**: caller audio with the real phone's character, Paul's voice
   through the 802's own transmitter, stream mic auto-gated during calls.
6. Hardware purchases allowed (budget comfortable — quality picks preferred over minimum
   spend), phone internals modification allowed, home network changes allowed. Paul is in
   Australia (Bellarine Peninsula, VIC). Product name: **the Pork Phone**.
7. **NEW — security requirements:**
   a. No inbound connection to the home network, ever. No port forwards (CGNAT makes them
      impossible anyway — treat that as a feature, and never work around it).
   b. The streaming PC opens **zero network-reachable listening ports** for this project
      (the DUMP endpoint binds localhost only) and has **no LAN path to or from the Pi**.
      (The §7.6 fallback, if ever invoked, relaxes this to a single PC→Pi outbound rule —
      a documented exception, never a default.)
   c. The ATA never touches the internet — enforced twice (zone rules + egress block).
   d. Internet-reachable code (the hotline app) runs isolated from the trusted LAN; a full
      compromise of the Pi must not yield a network path to the streaming PC or personal
      devices.
   e. Every failure fails closed (no call, no exposure) rather than open.
   f. The design survives the public site leaving the Pi (§13) without rework.

Out of scope for v1 (door kept open): real-money purchases, YouTube identity/Super Chat,
multiple phones, queue-jump pricing, caller video.

---

## 3. Threat model (ranked) & security principles

For a streamer large enough to attract attacks, in order of real-world likelihood:

| # | Threat | Realistic vector | Mitigation in this design |
|---|---|---|---|
| 1 | Account / stream-key theft | Phishing, commodity malware, poisoned OBS plugin on the PC | Out of network scope — hygiene: separate browser profile for broadcaster accounts, 2FA everywhere, no unvetted OBS plugins. The design adds **no listening surface** to the PC. |
| 2 | Bug in Paul's own public code | Site, API, bot, hotline app — reachable by anyone via the tunnel | The one segmentation genuinely addresses: Pi lives in SERVICES zone with **no path into LAN**; hotline sandboxed (§4.3); posture roadmap moves public web off-site entirely (§13). |
| 3 | IP leak → doxx / swatting | Exposed home IP from a service, port forward, or WebRTC ICE | Tunnel-only ingress; CGNAT (no public IPv4 exists); no WebRTC in v1 (ICE candidate leakage avoided by construction); callers only ever see Cloudflare edges. |
| 4 | DDoS | Flooding public endpoints | Cloudflare absorbs; nothing at home listens; worst case the hotline is down, the house is not. |
| 5 | ATA compromise | Requires prior foothold via #1/#2 — nothing external reaches it | PHONE zone (no internet, no LAN), first-boot hardening (§5.2), egress block, strong SIP auth. |

**Principles applied throughout:** no inbound, ever · the PC never listens · fail closed ·
two independent layers on admin surfaces · zones with pinned, minimal flows · the phone
system must be droppable (unplug ATA + stop unit) without touching any other surface.

---

## 4. Network topology & zones

Paul's gear: Telstra 5G modem (CGNAT) → **UniFi Dream Machine Pro** → UniFi 16-port PoE
switch → further switches/bridges. UDM + Pi live in **Building A**; the desk (802, streaming
PC, iD4) is in **Building B**, linked by a UniFi bridge. Everything below is UDM
configuration (zone-based firewall on current UniFi Network; equivalent inter-VLAN rules on
older versions) — **no new network hardware, and the Pi does not move**.

### 4.1 Zones

| Zone | Members | Allowed flows |
|---|---|---|
| **LAN** (trusted, default) | Streaming PC, Paul's machines, personal devices | → internet; → SERVICES: SSH from Paul's admin IPs only; → PHONE: ATA web UI (HTTPS) from Paul's admin IP only |
| **SERVICES** (new VLAN) | Pi only (static IP) | → internet **outbound only** (Cloudflare tunnel, apt, NTP, Twitch API, B2 backup); ↔ PHONE: SIP (5060) + the configured RTP range, **pinned to the two host IPs**, both directions. **No path into LAN, ever.** |
| **PHONE** (new VLAN) | ATA only (DHCP reservation), on a tagged desk-switch port in Building B | ↔ SERVICES: SIP/RTP as above; optional NTP to the Pi. **No internet, no LAN.** |

Notes:
- The bridge between buildings carries the PHONE VLAN tag; UniFi point-to-point links are
  WPA3/AES — SIP/RTP crossing it stays inside the encrypted link and the VLAN.
- The streaming PC stays in LAN and needs no rules: its only project traffic is *outbound
  HTTPS/WSS to Cloudflare* (overlay browser source + daemon event feed, §7.3). The DUMP
  button is localhost-only.
- ATA egress to WAN is **also** blocked by an explicit UDM rule, independent of zone
  membership — a mis-tagged port or zone edit fails safe.
- A VLAN misconfiguration fails closed: the ATA merely loses its registrar and no call can
  ring; nothing becomes exposed.

### 4.2 ATA first-boot hardening checklist (before it ever touches the PHONE VLAN)

Performed from Paul's admin machine on an isolated bench connection:

1. Change **both** web passwords (admin + user); HTTPS-only management.
2. Disable **GDMS cloud**, **TR-069**, and all auto-provisioning (blank the config server) —
   out of the box the unit will phone home to Grandstream's provisioning/firmware services.
3. Disable automatic firmware upgrade. Manual cadence: check Grandstream's site quarterly,
   apply from the admin machine.
4. Disable STUN and all NAT-traversal helpers (nothing to traverse), disable hook-flash
   detection (§5.1), set a **strong SIP auth password** (belt and braces on an isolated VLAN).
5. Static/reserved IP; NTP pointed at the Pi or left off (log drift is acceptable).

### 4.3 Pi hardening

- SSH: keys only, reachable only from LAN admin IPs (zone rule). CGNAT means WAN-side SSH
  cannot exist even by accident.
- OS: unattended-upgrades on.
- The hotline app runs as its **own low-privilege user** in a sandboxed systemd unit
  (`NoNewPrivileges`, `ProtectSystem=strict` + narrow `ReadWritePaths`, `ProtectHome`,
  `PrivateTmp`), with its **own SQLite database** — zero imports from `pi/` code, zero shared
  tables (this is also what makes §13's migration mechanical).
- Secrets (Twitch client secret, EventSub HMAC secret, broadcaster token, daemon/overlay
  tokens) live in a root-owned `0600` env file loaded by systemd — never in the repo.
- Asterisk binds SIP to the Pi's interface with an ACL accepting **only the ATA's IP**;
  ARI/AMI bind to localhost only. The tunnel forwards **only the hotline app's HTTP/WS
  port** — SIP is never web-reachable.

### 4.4 Admin surfaces

- **`/console`** (Paul's control panel): behind **Cloudflare Access** (Paul's email) *plus*
  an app-level session — two independent layers.
- **`/overlay`** (OBS browser source): long random token in the URL. OBS browser sources
  cannot complete interactive auth; the overlay is a read-only, already-N-delayed event
  view, so token-in-URL is an accepted, documented trade.
- **Daemon event feed** (§7.3): long-lived device token issued from the console; revocable.

---

## 5. Hardware

### 5.1 The phone: Telecom Australia 802 (identified & confirmed — carried facts)

Identified from the moulded 605 plug ("EE/85" ≈ 1985) + recon sticker; Paul confirmed
visually against britishtelephones.com/aus/800.htm. Bench-relevant facts, verified in the
rev-2/3 research rounds:

- **Bell accepts 16–50 Hz** — the HT802's ~20–25 Hz generator sits inside it; ringing odds
  high. **Loudness wheel** minimum is a low buzz, never silent — check it's wound up before
  declaring the bell dead. Audio-fine-but-no-ring ⇒ the **mode-3 bell strap** fix
  (screwdriver-level, documented among AU collectors).
- **Transmitter inset:** carbon No. 13 or electronic 20E (1985 = either; both authentically
  narrowband; swappable for maximum crust). **Receiver capsule: 4T** (the spare to hunt if
  the §7.5 re-amp is ever built).
- **Decadic dial, 10 pps** — all calls are inbound; **hook-flash detection stays OFF at the
  ATA** so mid-call dial-spinning or hook bounce does nothing.
- **Handset cord is hardwired internally** (no modular jack; era cords may share a return
  conductor) — this is why the stream tap is **line-side** (§7.1), not handset-side.
- **Adapter — naming verified 2026-07-12:** in the AU 600-series the **605 is the plug** on
  the phone's cord; the socket that receives it is a **610**. Buy an **RJ11-to-610-SOCKET
  adaptor** — the phone's 605 plug goes into the 610 socket, RJ11 end to the ATA. Old
  Phones Australia sells exactly this (~A$15), marketed specifically for Grandstream
  HT-series ATAs "so no need to modify your telephone to make the bells ring again" —
  preferred pick; generic eBay equivalents ~A$5–10. If the 802 still won't ring, the mode-3
  bell strap above remains the fix. **Avoid the common reverse product** ("605 plug to RJ11
  socket" — a male 605 with an RJ11 hole, for plugging modern phones into old wall sockets;
  useless here).

### 5.2 The ATA: Grandstream HT802V2

**Buy the HT802V2** (~A$63–89 AU retail). The Cisco SPA112 remains rejected — end-of-sale
2020, support ended May 2025; an unpatchable embedded device is exactly what you don't put
inside your house, on any VLAN. The Grandstream line has had real CVEs over the years
(auth-bypass / command-injection class — patched; the device class earns zero trust either
way), which is precisely why §4.2 hardening + the PHONE zone + the egress block all exist.
Config for the 802: AU SLIC impedance · AU double-ring cadence · ring voltage/frequency if
the bell needs coaxing · RX/TX gain to suit the old capsules **and** the §7.1 line balance ·
hook-flash off · registrar = Asterisk on the Pi. Supports registrar-less direct IP-to-IP
calls — that's the Phase-0 bench with a PC softphone, no server needed.

### 5.3 The line coupler (new — the stream tap)

A **transformer-isolated telephone line audio coupler** sits inline on the phone line at the
desk and hands the mixed line audio to the iD4. Purchase criteria: transformer isolation
(hum immunity), **ring-voltage protection** (the ring pulse is tens of volts and must be
clamped, not passed to the interface), RJ11 pass-through, line-level-ish output.

- Baseline: generic/Sescom-class inline coupler, ~A$30–80.
- Premium (budget is comfortable): **JK Audio-class broadcast inline coupler** (e.g. Inline
  Patch, ~US$250–300 — verify current pricing/AU availability at purchase). Buys build
  quality and clean levels; changes nothing structurally.
- Handset-jack taps (JK QuickTap etc.) **do not apply** — the 802's handset is hardwired
  (§5.1).

### 5.4 Audio interface

Current: **Audient iD4 MkII** — sufficient. SM58 on the mic pre (channel 1), coupler into
the DI (channel 2, with a small inline pad if levels run hot; gain staging is a Phase-0
bench item). The two inputs arrive in Windows as one stereo device; **the daemon splits the
channels in software** (§7.2) — no OS/OBS feature dependency. If Paul upgrades (he's
inclined to): any interface with 2+ inputs including one line/DI input works; more line
inputs simply make the phone a first-class channel. Structural impact of upgrading: none.

### 5.5 Other

- **DUMP button:** Stream Deck key (if one lands on the desk anyway), Stream Deck pedal, USB
  foot switch, or a big red USB button (on-camera prop value). Open decision §15.
- **Virtual audio cables:** VB-Audio class, 2–3 installs (free).
- **The retired Panasonic KX-TG1031S:** on-camera set dressing, **never operated** — it
  transmits inside Telstra's licensed n1 uplink band, unlawful in AU and not retunable. Base
  stays unpowered forever. (Full analysis in the rev-3 doc / git history.)

---

## 6. Architecture — call path

```
viewer browser ──mic frames (20 ms PCM/Opus) over WSS──► Cloudflare ──tunnel──► hotline app (Pi, SERVICES)
                                                            │ queue · credits · OAuth · EventSub
                                                            │ consent · recording (raw WAV + dump log)
                                                            │ events: real-time feed (console, daemon)
                                                            │         N-delayed feed (overlay)
                                                            ▼ AudioSocket (8 kHz PCM, localhost TCP)
                                                          Asterisk (same Pi)
                                                            ▼ SIP/RTP — SERVICES ↔ PHONE zones only
                                                          HT802V2 ATA ──RJ11→610──► Telecom 802 🔔
                                                                    └─(line coupler → §7 analog chain)
```

- **Hotline app** (one asyncio service on the Pi; Python vs Node decided at build): serves
  the `/phone` page APIs, `/console`, both event feeds, Twitch OAuth + EventSub
  (HMAC-verified), the queue (pluggable ring policy — manual v1, pbenguin-screen auto-answer
  seam, §15) + credit ledger (own SQLite), the browser-audio bridge (~100 ms
  jitter buffer, decode, resample), per-call recording, and call control via ARI
  ("originate PJSIP/ata, AudioSocket me the audio").
- **Asterisk** (same Pi): the ATA's registrar, G.711 to the phone, AU ring cadence, CNAM =
  caller's Twitch name (vestigial on a rotary; free; any future display phone lights up).
- **Latency budget:** ~150–350 ms per direction (caller network + tunnel + jitter buffer +
  LAN legs) — long-distance-call feel, fully conversational. The 5G uplink's jitter is the
  Phase-1 measurement (§14); fallback ladder in §11.
- **Dump-log path (changed from rev 3):** dumps happen on the PC, so the daemon reports each
  dump over its authenticated WebSocket (via Cloudflare); the Pi writes `strikes` rows
  against the call. Recordings stay server-side and **raw** (pre-dump); the dump log records
  what actually aired.
- The call experience script (lines open → redeem → queue → ring → lift horn → talk → T−10
  beep → hangup → FULFILLED / CANCELED-refund) carries from rev 3 §5 unchanged, including
  the caller-page "mute the stream while on the line" instruction and the future
  voicemail-roulette format idea.

---

## 7. Stream audio chain (the analog bridge)

**Design property this section exists to protect: the streaming PC and the Pi never
exchange a LAN packet.** Call audio crosses from the phone system to the streaming rig as
electricity on a cable; state crosses via the public URL through Cloudflare.

### 7.1 The tap

Line coupler (§5.3) inline between ATA and phone at the desk → (pad if needed) → iD4 DI,
mono. What it hears is the **mixed line audio**: both voices with the ATA's DAC + hybrid
coloration already baked in — a step *more* authentic than rev 3's digital tap. Relative
level of the two voices is tuned via the ATA's RX/TX gains at the bench; if the balance
proves untunable, the §7.6 fallback restores per-leg control. During RINGING the line
carries ring voltage, not audio — the coupler clamps it, and the phone channel gate (§7.4)
is closed pre-answer anyway; the **real bell is heard by the room mic**, as before.

### 7.2 The bleeper daemon (streaming PC)

Captures the iD4 as one stereo device and deinterleaves: **ch 1 = SM58, ch 2 = phone line**.
Owns everything stream-bound:

- **N-second ring buffer** on all audio it emits (N default 4, runtime-tunable; open knob
  §15). Changing N mid-show causes a fill/drain hiccup — set pre-show or flip under a scene
  transition.
- **DUMP:** one press replaces the buffered phone-channel span with the bleep tone, fires
  the CENSORED card via the (equally delayed) overlay, and reports the strike upstream.
  Exactly one channel to nuke — the mixed line — which is simpler than rev 3's
  two-channel sidetone story. Semantics knob (whole-buffer vs hold-to-span) stays open §15.
- **Outputs** via virtual cables into OBS as separate sources: PHONE (delayed), MIC
  (delayed, gated), GAME/desktop audio (routed through the daemon for the same N — OBS's
  per-source sync offset caps out far below broadcast-delay scale; verify current caps at
  the Phase-3 bench). **Video:** OBS "Video Delay (Async)" filter at the same N on
  camera/capture sources (verify max at bench; fallback = video through a delay relay).
- **OBS modes:** phone-show mode = audio sources are the daemon's cables (OBS never captures
  the iD4 directly — that would bypass delay and dump); normal streams capture the iD4 as
  usual. A scene-collection toggle in the runbook.

### 7.3 Events, and the hindsight trick

The daemon makes **one outbound WSS connection** to the hotline's public endpoint
(device-token auth) for the **real-time** feed (call state, caller name, timer); the overlay
browser source pulls the **N-delayed** feed. Gate and dump-report latency rides Cloudflare
(~0.1–0.3 s) — irrelevant, because **gate transitions are applied at event timestamps
*inside* the delay buffer**: the daemon has N seconds of hindsight, so an ANSWERED event
arriving 300 ms late still opens the phone gate at exactly the right sample, and no syllable
is ever clipped. The same mechanism makes feed latency invisible for closing, too.

### 7.4 Gating

- **Phone channel: closed except during calls** (ring buzz, idle line noise never air).
- **Stream mic: crossfades down on CALL_ACTIVE** (full gate default; −12 dB ambience-bed
  knob kept), reverses on hangup. During a call the whole show goes down the line —
  phone-quality everything is the bit — and the double-path comb (room mic + transmitter,
  ~100 ms apart) is killed dead.
- Earpiece-only listening keeps moderation airtight: the caller's voice never exists
  acoustically in the room, so the room mic can't leak it; the only stream copy is the
  phone channel the DUMP key owns.

### 7.5 Voicing

The earpiece-sim EQ drops from *required engineering* (rev 3) to **optional garnish**: the
line already sounds like a phone; the only absent character is the 1970s Receiver 4T
capsule's acoustics. At the Phase-0 bench, A/B the coupler feed against a mic held to the
real earpiece; apply a gentle EQ curve in the daemon only if it earns its place. The purist
upgrade remains the **re-amp box** (spare 4T capsule in an enclosure with a mic).

### 7.6 Named fallback — the digital input stage

The daemon's input stage is explicitly abstract. If the coupler hums beyond fixing
(pad → tap point → isolation) or the voice balance disappoints, the input swaps to the
**hardened digital receiver**: the daemon dials out to the Pi (WS client, token; one
firewall rule LAN→SERVICES; the PC still never listens; fixed-size PCM framing), receives
the two legs as separate channels, and the earpiece-sim EQ becomes mandatory again. Nothing
else in the design changes. (This is Approach B from the 2026-07-12 review; Approach C —
moving the Pi and point-to-point-wiring the ATA — was rejected as buying little over zones
while costing a Pi move and putting all site traffic on the bridge.)

---

## 8. Moderation: broadcast delay + the dump key (carried, condensed)

Radio's actual solution, adopted wholesale: **the call is never delayed — the broadcast
is.** Paul hears the caller raw (that's what makes it a conversation); everything OBS sends
runs N seconds behind the room; bleeps are applied inside that window.

- **No automatic detection in v1.** ASR isn't catch-every-slur good, a doxx is ordinary
  words, and Paul hears everything live — he is the detector, as radio trusts its dump
  operator. The daemon keeps a clean seam (`mark_span(t0,t1)` + `dump_all()`) so ASR could
  bolt on later as an assist, never as a safety dependency.
- **Reaction-time math (why default N=4, not 2):** a slur is recognisable ~0.4–0.6 s after
  onset; recognition + decision + finger ≈ 0.5–1.0 s; so the press lands ~1.0–1.9 s after
  the word began. N=2 leaves 0.1–1.0 s of margin when attentive and negative margin
  mid-laugh; N=4 covers the distracted case (radio gives 6–8 s plus a producer). It's a
  runtime knob — trial 2 live, dial up after the first close call. Cost of bigger N is chat
  lag on top of Twitch's own 4–8 s; a phone show doesn't need frame-tight chat.
- **Deterrence stack:** costs channel points → burns a Twitch account in good standing →
  name on the overlay → consent screen states the ban policy → dumps recorded as strikes.
- **Residual risk, stated honestly:** Paul can blink; then it airs once, he hangs up + bans
  — the residual class every TTS-donation streamer accepts. Twitch holds streamers
  responsible for what airs regardless of source; prompt removal + ban + VOD-edit
  willingness is the accepted posture (skim current Community Guidelines at build).
- Paul→caller direction is never filtered or delayed.

---

## 9. Twitch integration (carried from rev 3 — verified against Helix docs there)

- **Identity:** authorization-code OAuth on the hotline service, routed under
  `thekartoff.com` (cookie `Domain=.thekartoff.com` so the SPA page and the
  `phone.` subdomain share the session). Store immutable Twitch user id (bans survive
  renames), display name, avatar. No viewer scopes beyond identity.
- **Credits:** broadcaster token (`channel:manage:redemptions` + `channel:read:redemptions`;
  requires Affiliate+ — Paul qualifies). App creates/owns the reward via Helix (API-created
  rewards are editable only by the creating app — Paul tunes cost via our console, not the
  Twitch dashboard). Anti-spam knobs: `max_per_stream`, `max_per_user_per_stream`,
  `global_cooldown_seconds`.
- **EventSub** `channel.channel_points_custom_reward_redemption.add`, webhook transport with
  HMAC verification (tunnel provides the public HTTPS callback; WebSocket transport works
  for local dev).
- **Lifecycle:** hold UNFULFILLED → FULFILLED on completed call → CANCELED to refund.
  ⚠ Re-verify the "CANCELED returns points" sentence first thing at build (§14).
- **Redeemer = caller:** the EventSub payload's user id must match the logged-in session's
  user id holding an unspent credit; one live session per user; one queue slot per user;
  credit reserved at queue join, **spent at answer**, auto-refunded at stream end if never
  connected. Ledger: `credits(twitch_user_id, seconds, source, status:
  unspent|reserved|spent|refunded)`.
- **Real money (Phase 4, design-ready):** Stripe Checkout on the call page (~A$0.30 +
  1.7–1.75 %/txn), webhook credits the same ledger; no-refunds-for-bans in the terms; GST
  only past A$75k; accountant question on donation income. **YouTube (later):** no
  channel-points analog; Super Chat via `liveChatMessages.superChatDetails` (verify API
  terms at build); Google OAuth alongside Twitch in the same `identities` table.

---

## 10. Website placement & repo layout

| Piece | Where | Notes |
|---|---|---|
| Caller page `/phone` | `web/` SPA — unlisted URL-only route (like `/heat`) | KART-OFF print design language (per site-redesign spec; frontend-design skill at build). Twitch OAuth is the real gate; unlisted-ness is polish. Announce later = add a nav link. |
| Hotline API + audio WS + EventSub | `hotline/server/` on the Pi — own systemd unit, own SQLite, own port; tunnel ingress **subdomain `phone.thekartoff.com`** (decided — cleaner Cloudflare Access policies than a path rule) | Zero imports from `pi/` code. Deploys decoupled from site tags. |
| `/console` + `/overlay` | Served by the hotline service | §4.4 auth layers; overlay events N-delayed server-side. |
| Bleeper daemon | `hotline/bleeper/` on the streaming PC | Ring buffer, DUMP endpoint (localhost), capture/split/gate, virtual-cable outputs, OBS setup notes. Language decided at build. |

Root `CLAUDE.md` gains a fifth surface row at implementation time. Twitch player + chat
embeds on `/phone` work for anonymous viewers (parent-domain param); posting chat triggers
Twitch's own iframe login — no dependency on our OAuth.

---

## 11. Failure modes (all fail closed)

| Failure | Behavior |
|---|---|
| Daemon crash mid-call | Phone + mic sources go **silent** — OBS only listens to the daemon's cables, so nothing undelayed can air. Restart, buffer refills, resume. |
| Event feed drops | Daemon holds last state (never auto-ungates the mic on a dead feed); **silence backstop**: phone channel quiet past threshold → close phone gate, restore mic. |
| Tunnel/Cloudflare outage mid-call | Browser leg dies → app detects stall → hangs up phone leg. Credit was spent at answer; partial-refund policy = open decision §15. |
| Caller tab dies / mic lost | App detects WS close → grace period → hangup. Skipped-at-ring callers keep their credit. |
| ATA loses registration / VLAN misconfig | Fails closed: no call can ring; console shows PHONE OFFLINE; lines auto-close. ATA-to-internet independently egress-blocked. |
| Pi reboot | Services restart; **lines closed by default on boot**. |
| Recording disk low | Lines refuse to open below threshold. |
| 5G/bridge jitter beyond the jitter buffer | Phase-1 verdict; ladder: WS → **Cloudflare-TURN WebRTC** (both ends outbound, no VPS) → **VPS for the call leg only** (last resort; everything else stays home). |
| Coupler hum / bad balance | Pad → tap point → isolation → §7.6 digital input stage. |
| Hook bounce / dial-spin mid-call | Hook-flash detection off at the ATA; slam-test at Phase 0. |

---

## 12. Data, consent, law (unchanged posture — not legal advice)

1. **Schema (hotline's own SQLite):** `identities` · `credits` · `bans` (immutable user id,
   reason, strike refs) · `calls` (caller, timestamps, seconds bought/used, outcome,
   consent-acceptance timestamp, recording/dump-log paths) · `strikes` (call id, dump
   timestamp, span, action — ingested from daemon reports) · `settings` (reward cost/seconds,
   delay N). Nightly copy to B2.
2. **Consent screen (blocking, per caller):** broadcast live + recorded; profanity may be
   bleeped; slurs/doxxing = instant ban; be funny. Timestamped acceptance makes recording +
   broadcast clean under all-parties rules.
3. **Telecom licensing:** none — private VoIP loop, no PSTN interconnect, no public carriage.
4. **Recordings:** raw WAV (both legs + mix, pre-dump) + dump log, 90-day retention, console
   download (ban evidence, VOD sync, highlights).

---

## 13. Posture roadmap (new)

**Standing property (now):** CGNAT means inbound is impossible; nothing in this design ever
asks for a port forward; every inbound path is the tunnel.

**Milestone — before the first stream:** the public site, API, Discord bot, and scraper
move off the Pi to a small cloud box (likely a Sydney VM, ~A$6–12/mo — its own mini-spec
when scheduled). The Pi becomes a **dedicated phone appliance** in the SERVICES zone; home
runs zero public web content. The hotline is built for that day from day one: own subdomain
through the tunnel, own DB, own unit, no `pi/` imports — the site's departure is a
DNS/deploy event that doesn't touch the phone. (Decision and timing are Paul's; the design
only guarantees the move stays mechanical.)

---

## 14. Runbook

**Shopping list (~A$110–200 one-time at baseline; premium coupler adds ~A$300; zero
monthly):**

1. Grandstream **HT802V2** — ~A$63–89 (AV Mart et al.).
2. **RJ11-to-610-socket adaptor** — Old Phones Australia ~A$15 (sold for Grandstream HT
   ATAs; nomenclature + direction in §5.1); generic eBay AU equivalents ~A$5–10.
3. **Line coupler** — baseline ~A$30–80; premium JK-class ~US$250–300 (§5.3; verify pricing
   at purchase). Small inline pad (~A$10) on standby for the DI input.
4. **DUMP button** — Stream Deck key/pedal, foot switch, or big red USB button (~A$15–130).
5. VB-Audio virtual cables ×2–3 (free). The Panasonic needs nothing, ever.

**Phase 0 — bench day (ATA arrives; ~1–2 h; no network changes — bench on a direct cable):**
§4.2 hardening first, then: 605→adaptor→ATA FXS1; AU impedance + double-ring; softphone
direct-IP INVITE → **does the bell ring?** (loudness wheel up; mode-3 strap if audio-no-ring);
two-way audio + RX/TX gains; slam test + dial-spin test; **coupler inline → iD4: levels, hum,
voice balance**; earpiece A/B reference capture; report which transmitter inset it carries.

**Phase 1 — zones + first internet ring:** UDM zones/VLANs per §4.1; Pi hardening per §4.3;
Asterisk + minimal app; tunnel ingress `phone.thekartoff.com`; bare test page rings the
phone from outside the house; **measure WS-over-5G jitter** → go/no-go on the §11 ladder.

**Phase 2 — product:** OAuth, EventSub, reward, queue, consent, ledger, console (Access),
delayed overlay, recordings + strike ingestion.

**Phase 3 — the delay chain:** daemon (capture/split/delay/dump/gate/outputs), virtual
cables, OBS wiring + **bench the Video Delay (Async) max and audio-offset caps**, DUMP
button, CENSORED card, ban wiring, full dress rehearsal with a friend as caller.

**Phase 4 — money/YT:** §9 Stripe / Super Chat.

Each phase lands behind the unlisted page; nothing touches existing surfaces.

---

## 15. Open decisions (at sign-off) & known-unverified

**Decided at sign-off (2026-07-12):**
- **Delay N = 4 s** (stays a runtime knob; 4 is the committed default).
- **Answer mode: manual ANSWER NEXT for v1**, with a designed seam for a future
  **auto-answer ring policy driven by pbenguin screen state** — the desktop app already
  reports presence/activity to the Pi (verify exact event granularity at build); the queue
  exposes `ring_policy: manual | auto(predicate)` so "auto-ring only on safe screens (e.g.
  menus, not mid-race)" is a policy plug-in later, not a rework.

**Still open, decided as needed during build:** dump semantics (whole-buffer vs
hold-to-span) + which physical button · reward economics (e.g. 25k pts / 60 s,
1/user/stream, 120 s cooldown — console-tunable) · naming (`hotline/` + "THE PORK PHONE"
placeholder) · coupler model (bench pick; premium pre-approved) · interface upgrade timing
(structural impact: none) · partial-refund policy for dropped-after-answer calls. Build-time (not
blocking sign-off): implementation language for the app and the daemon (Python vs Node vs a
small compiled binary — the implementation plan decides).

**Known-unverified (re-check first at build/bench):** the "CANCELED refunds points"
sentence · OBS Video Delay
(Async) max + audio sync-offset cap on current OBS (Phase 3) · coupler levels/hum/balance on
the real line (Phase 0) · iD4 DI level match (Phase 0) · JK coupler AU pricing/availability
(purchase-time) · YouTube Super Chat API terms (Phase 4) · current Twitch Community
Guidelines wording (build).

**Verified since sign-off:** 802 bell vs HT802 ring generator — RINGS (bench day
2026-07-21: HT802V2, Ring Power 55 Vrms, 20 Hz; loudness wheel just below max) ·
WS-audio through tunnel + cellular — HOLDS (Phase 1 internet ring 2026-07-21:
clean two-way audio from a phone hotspot via phone.thekartoff.com, ~1 s
end-to-end lag, no stutter; ladder stays at rung 1, TURN/VPS not needed) ·
ARI externalMedia `encapsulation=audiosocket` — works on Asterisk 22.10.1
(source-built on the Pi; no dialplan fallback) · UniFi firewall — Paul's console
has both UIs; classic "traffic & firewall rules" (LAN In + explicit
established/related return rule) used instead of zone migration, per §4's
"equivalent inter-VLAN rules" clause.

All shape-safe: none can invalidate the architecture, only tweak numbers, models, or which
rung of a named ladder gets used.

---

## 16. Sources & lineage

Primary sources carried from rev 3 (Telecom 802 reference incl. bell/inset/receiver specs ·
ACMA class-licence + spectrum pages · Cloudflare Tunnel protocol docs (HTTP+WS to anonymous
visitors; the no-VPS basis) · Grandstream HT80x admin guide + AU listings · Cisco SPA112 EOL
notice · Twitch Helix + EventSub references · Old Phones Australia RJ11-to-610-socket
adaptor listing + Access Communications 605/610/RJ plugs-and-sockets guide (nomenclature
verified 2026-07-12)). New in this rebuild: Paul's network inventory (UDM-Pro + UniFi PoE switching + inter-building bridge,
Telstra 5G CGNAT) and audio inventory (Audient iD4 MkII, SM58), gathered 2026-07-12.

Lineage: rev 1 (2026-07-11, DECT + VPS + call-path bleep) → rev 2.x (spectrum research;
cordless ruled unlawful) → rev 3 (2026-07-12, rotary-native, Pi-hosted, broadcast delay) →
**this document** (2026-07-12, from-scratch security-first rebuild; product carried, every
structural decision re-derived; Approaches B/C recorded in §7.6). Full deliberations in git
history from `1e533f9`.
