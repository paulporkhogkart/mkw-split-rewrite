# The Pork Phone — viewer call-in hotline (design)

**Date:** 2026-07-11 · **rev 3** 2026-07-12 (three review rounds folded in) · **Status:**
DRAFT — awaiting Paul's sign-off · **Surface:** new `hotline/` service (on the Pi host) + a
bleeper daemon (streaming PC) + unlisted `web/` page + one ATA + **Paul's Telecom 802 rotary
phone**. No VPS, no new monthly cost, no radio anywhere in the design.

The bit: viewers spend channel points on the KART-OFF site to ring a real rotary phone on
Paul's desk, live on stream — real bell, real horn, real narrowband crust. Paul answers by
lifting the horn; the conversation is fully real-time in both directions; a
radio-station-grade **broadcast delay** (the whole outgoing stream runs a few seconds behind
the room) plus a desk **DUMP button** keeps anything bad off the air.

**Revision history (compressed):** rev 1 (2026-07-11) was designed around a US Panasonic DECT
phone, a Sydney VPS, and a 2.5 s predictive bleep inside the call path. Three review rounds
on 2026-07-12 replaced all three pillars: the VPS fell to WS-framed audio over the existing
Cloudflare tunnel (rev 1's "the tunnel can't carry call media" was true only of UDP
protocols); the call-path bleep fell to a stream-side broadcast delay (a delayed call isn't a
conversation — and adversarial speech beats ASR, so moderation is manual, built on Paul's own
ears); and the cordless phone fell to the rotary (the US DECT unit transmits inside Telstra's
licensed uplink band — unlawful to operate in Australia and not retunable; it's now set
dressing, §3.3). Full deliberations live in git history from `1e533f9`.

---

## 1. Answers to the brief (index)

| Question in the brief | Answer | Section |
|---|---|---|
| How does the phone receive calls online, no phone number, no location leak? | An analog telephone adapter (ATA) gives the phone a fake "phone line"; it registers to Asterisk **on the Pi, over the LAN — the ATA never touches the internet**. Viewer audio reaches the Pi as WebSocket frames through the existing Cloudflare tunnel, so callers only ever see Cloudflare edge IPs. No PSTN, no number, no ports opened at home, no VPS. **No modification to the phone itself.** | §3, §4 |
| Channel points → seconds of call time; prove the redeemer is the caller | Twitch custom reward + EventSub redemption webhook + "Log in with Twitch" on the call page; the redemption and the login carry the same immutable Twitch user id. Unused redemptions are refunded via API. | §7 |
| Bleep system a solo host can operate + blacklist | Radio-style **broadcast delay**: the call is fully real-time, but everything OBS sends to Twitch/YouTube runs N seconds behind the room (default 4, runtime-tunable). Paul hears the caller raw; one press of the desk **DUMP button** replaces the buffered phone audio with the bleep before it airs. No automatic detection in v1 — Paul's ears are the detector. BAN button blacklists the Twitch user id (and optionally bans from the channel). | §6 |
| Hide the call page unlisted on thekartoff.com? | Yes — `/phone`, URL-only, exactly like `/heat` and `/version` today. Twitch OAuth is the real gate; unlisted-ness is just polish until announce. | §8 |
| Twitch login? YouTube login? YouTube's channel-points equivalent? | Twitch OAuth now. YouTube has **no channel-points analog**; its money events are Super Chat / memberships (readable via API). The credits ledger is provider-agnostic so Google OAuth + Super Chat (or Stripe for real money on any platform) slot in later. | §7.5 |
| Do embedded stream/chat on the site require viewer login? | No. Twitch embeds view anonymously; *sending* chat prompts a Twitch login inside Twitch's own iframe. No dependency on our OAuth either way. | §8.4 |
| How does chat hear the call? (was: "speakerphone every time?") | Superseded by the rotary: there is no speakerphone. Paul talks on the handset; the stream gets both legs as **separate digital channels** from the server (the ATA is the splitter), with an **earpiece-sim** EQ on the caller channel so chat hears what Paul's ear hears. His stream mic auto-gates during calls. | §9 |
| "Probably can't test until we modify it" | Wrong in a good way: the day the ATA arrives, a free softphone on the PC can direct-IP ring the rotary on the LAN — full physical bench test before any cloud work, no modification ever. | §11 Phase 0 |

---

## 2. Requirements (from Paul's brief, as revised through review)

1. A physical phone on Paul's desk rings with real, two-way calls placed by viewers from a
   web page — **Paul's Telecom 802 rotary** (round 3; the US Panasonic that prompted rev 1 is
   set dressing, §3.3). No real phone number. Nothing that exposes Paul's home IP, address,
   or identity to callers.
2. **Channel points → N seconds** of call time, with proof that the spender is the caller.
   Real money later; possibly YouTube later.
3. **Bleep/censor** workable by one person, **manual-reactive by preference** (round 1
   revision: a hovering finger + broadcast delay replaces the original "can't bleep
   reactively" assumption; automatic detection dropped from v1); **blacklist** anyone who
   forces a bleep.
4. Call page on the existing site if sensible, unlisted; viewer identity via platform login.
5. **Chat hears what Paul hears** (round 3 revision of the old speakerphone plan): caller
   audio with the real earpiece's character (§9 earpiece-sim), Paul's voice through the 802's
   own transmitter, stream mic auto-gated during calls.
6. Hardware purchases, phone internals modification, home network changes all allowed.
   Paul is in Australia (Bellarine Peninsula, VIC). Product name: **the Pork Phone**.

Out of scope for v1 (design keeps the door open): real-money purchases, YouTube identity and
Super Chat credits, multiple streamers' phones, queue-jump pricing, caller video.

---

## 3. Hardware

### 3.1 The phone: Telecom Australia 802 (Paul's, identified and confirmed)

Identified 2026-07-12 from the moulded **605 plug** (four spades, one wide — the AU
600-series line plug; "EE/85" ≈ 1985 date code, "268/74" mould/part codes, triangle T·P =
moulder's mark) plus a recon-style "T.W.M R/C CENTRE" service sticker; **Paul confirmed the
802 visually** against britishtelephones.com/aus/800.htm. The 802 (1971→) is the standard
Telecom rotary — the most common phone ever in AU service.

Why it's the ideal unit for this design:

- **Legal: perfect.** Corded = zero radio = no class licence, no bands, nothing to argue
  about. (This is what killed the cordless plan — §3.3.)
- **Sound: natively crusty.** Narrowband transmitter + G.711 *is* the archetypal phone
  sound; the ring is a REAL electromechanical bell; no EQ needed on Paul's side.
- **Interaction: native.** Lift the horn = ATA off-hook = answered (timer starts); hang it =
  call ends. Line-powered — no PSU, no batteries, nothing to charge.

Bench-relevant specs (from the 800-series reference):

- **Bell accepts 16–50 Hz ringing** — the HT802's ~20–25 Hz generator sits comfortably
  inside, so ringing odds are high; the remaining variable is drive (REN/voltage). The bell
  has a **subscriber loudness wheel** (minimum = low buzz, never fully silent) — check it's
  wound up before declaring the bell dead.
- **Bell wiring gotcha:** some Telecom units were strapped for "mode 3"/extension
  configurations (bell return via the third wire). Symptom: audio fine, no ring. Fix: strap
  the bell across the line inside the case — screwdriver-level, well documented among AU
  collectors.
- **Transmitter inset:** carbon **No. 13** on earlier units, electronic **20E** on later
  production — a 1985-corded unit could carry either; both are authentically narrowband,
  carbon is crustier, and insets are collector-swappable if maximum crust is wanted.
- **Receiver capsule: "Receiver 4T"** — the exact spare to hunt if the §9 re-amp box is ever
  built.
- **Dial: decadic 10 pps** — irrelevant (all calls are inbound), but **disable hook-flash
  handling on the ATA** so mid-call dial-fiddling or hook bounce can't trigger anything.
- **No keys, no display:** the DUMP trigger is a desk button (§6); the caller's Twitch name
  lives on the overlay + console (Asterisk still sets CNAM — vestigial, harmless, and any
  future display phone lights up with it).
- **Adapter — direction matters:** buy an **"RJ11 plug → 605 SOCKET" adaptor** (the phone's
  605 plug goes INTO it; RJ11 end into the ATA; ~A$5–10 on eBay AU). The far more common
  reverse adaptor (605 plug → RJ11 socket, for plugging modern gear into old wall sockets)
  is useless here. AU retro-phone users run exactly this chain (802 → adaptor → VoIP ATA)
  routinely.

### 3.2 The ATA: Grandstream HT802V2 (the one purchase that matters)

- **Buy: Grandstream HT802V2** (2-port FXS ATA) — in stock in AU (AV Mart A$63 on sale, RRP
  ~A$89; HT802 ~A$70 widely). One port is enough (HT801 = 1-port twin); the V2 is the
  current, cheap, safe pick.
- **Do NOT buy a Cisco SPA112**: end-of-sale June 2020, support ended May 2025.
- Config for the 802: **AU SLIC impedance** · **AU double-ring cadence** (it's a config
  field) · ring frequency/voltage settings if the bell needs coaxing · **RX/TX gain** to suit
  the old capsules (earpiece volume is fixed on the phone) · **hook-flash detection off** ·
  registrar = Asterisk on the Pi over the LAN (TLS/SRTP unnecessary — SIP/RTP never leaves
  the house) · supports **direct IP-to-IP calls without a registrar** — that's the Phase-0
  bench, no server needed.

### 3.3 The retired Panasonic (set dressing only — never operate)

The US KX-TG1031S + KX-TGA101 (DECT 6.0, 2007) that prompted this project transmits at
**1920–1930 MHz — inside Telstra's licensed band-1/n1 uplink**, outside the ACMA cordless
class licence (AU cordless = 1880–1900 MHz): unlawful to operate in Australia, and the band
is fixed in chip firmware + RF filters — not retunable. **The base (the part that beacons)
stays unpowered forever; the unit lives on the desk as a prop.** One lawful, optional
garnish: a radio-silent sampling session (handset only, fresh cells, base boxed — with no
base it only listens; menu ringer preview → record WAVs; TAD voice from YouTube), after which
the app *could* inject sampled ring/answer sounds into the stream mix at ATA events — pure
software, parked unless ever wanted. (Rev 2's full lawful-sound-port menu — era-twin AU
Panasonic, Gigaset ringtone upload, Bluetooth costume build — is preserved in git history;
the rotary made it moot.)

---

## 4. Architecture

### 4.1 Privacy model first (it drives the shape)

- thekartoff.com already fronts the Pi via a **Cloudflare Tunnel**. Rev 1 assumed the tunnel
  "can't carry call media" — true for the classic VoIP protocols (SIP/RTP and WebRTC media are
  UDP; the tunnel gives anonymous visitors HTTP + WebSockets only, raw TCP needs client-side
  `cloudflared access`, UDP only via WARP private networks) — but **false for audio we frame
  ourselves**: 20 ms PCM/Opus frames over WSS is exactly how Twilio Media Streams and every
  streaming-ASR API carries live audio. We control both ends of the browser leg, so nothing
  forces WebRTC/SIP onto the internet at all.
- ⇒ **everything server-side lives on the Pi.** The browser leg rides the existing tunnel; the
  **ATA registers to Asterisk on the Pi over the LAN and never touches the internet**. Zero
  inbound ports at home, zero new listening services.
- **Location-leak check: nothing new is revealed.** Callers' browsers only ever connect to
  Cloudflare edge IPs (the `/phone` page and the audio WS are proxied like the rest of the
  site); the Pi connects *outbound* to Cloudflare; WebRTC — which can expose candidate IPs in
  SDP — is not used. Strictly better than a VPS with a DNS-only A record. What remains public
  is what was already public: Paul is Australian.
- No PSTN anywhere ⇒ no phone number exists to leak, and no telecom licensing applies (private
  VoIP loop, not a public carriage service — see §10.3).

### 4.2 Chosen stack — "Pi brain + Asterisk plumbing" (no VPS)

```
viewer browser ──mic frames (20ms PCM/Opus) over WSS──► Cloudflare tunnel ──► hotline app (Pi)
                                                          │  queue · credits · OAuth · EventSub
                                                          │  recording (WAV + dump log)
                                                          │  phone channels ──WS/LAN──► bleeper daemon (streaming PC, §6/§9)
                                                          ▼  AudioSocket (16-bit/8kHz PCM over local TCP)
                                                        Asterisk (same Pi)
                                                          │  PJSIP endpoint: ATA registration (LAN), CNAM=Twitch name
                                                          ▼  SIP/RTP — LAN only, never leaves the house
                                                        HT802V2 ATA ──RJ11→605 adaptor──► Telecom 802 🔔
                                                        [desk DUMP button ──HTTP──► bleeper daemon]
```

- **hotline app** (one asyncio service on the Pi host — Python or Node, decide at build):
  serves the page/console/overlay APIs + WebSockets, Twitch OAuth + EventSub, the queue and
  credit ledger (its **own** SQLite, not the Pi's main DB), the browser-audio bridge (jitter
  buffer ~100 ms, decode, resample), per-call recording, and streams the two phone channels +
  call-state events to the bleeper daemon over the LAN. All product logic in one testable
  process — it can be exercised end-to-end with WAV files and no telephony at all.
- **Asterisk** (same Pi; boring, rock-solid): the ATA's registrar, G.711 to the phone, ring
  cadence/CNAM delivery, and an **AudioSocket** bridge — a trivial TCP protocol that hands
  the app raw 8 kHz PCM in both directions. Call placement via ARI: app says "originate
  PJSIP/ata, then AudioSocket me the audio", Asterisk does telephony.
- **No delay anywhere in the call path** — the bleep lives in the broadcast (§6).
  Caller→phone ≈ 150–350 ms (caller's network + tunnel + jitter buffer + LAN legs);
  phone→caller the same. Long-distance-call feel, fully conversational both ways.
- Honest tradeoff vs WebRTC: WS rides TCP, so a caller on lossy wifi stutters rather than
  degrading gracefully. Acceptable at one-caller-at-a-time scale; the upgrade path if it ever
  annoys is real WebRTC relayed via Cloudflare's TURN service (both ends connect outbound —
  still no VPS).
- Pi load: one concurrent call = one Opus decode + resample + a G.711 leg ≈ nothing next to
  the site + bot. Recordings land on local disk (§10).

### 4.3 Alternatives considered

| Option | Shape | Why not v1 |
|---|---|---|
| **Sydney VPS (the rev-1 design)** | hotline app + Asterisk on a ~A$12/mo VPS; ATA registers outbound over TLS/SRTP; browser leg = WebRTC (aiortc) | Rested on "the tunnel can't carry call media" — true for UDP/WebRTC/SIP, **false for WS-framed audio** (§4.1). The VPS bought a monthly bill, a second box to patch, and *worse* privacy (DNS-only A record). Falls back in only if the Pi/tunnel path proves unstable in Phase 1. |
| **WebRTC into the Pi via Cloudflare TURN** | real WebRTC, both ends connect outbound, signaling over the tunnel | Loss-tolerant media (UDP) without a VPS — but more moving parts (ICE, TURN credentials, egress pricing) for a marginal v1 gain. The designated upgrade path if WS audio stutters for real callers. |
| **All-Asterisk browser leg** | chan_pjsip WSS + JsSIP, app only does AudioSocket processing | Asterisk's browser-WebRTC config is its fiddliest corner, and it needs media ports the tunnel can't carry. |
| **LiveKit self-host / Twilio managed** | as rev 1 | Extra services / per-minute fees to solve problems (NAT traversal, global media relay) the LAN-only design no longer has. |

Stack facts checked: Cloudflare Tunnel carries HTTP + WebSockets to anonymous visitors (raw
TCP requires client-side `cloudflared access`; UDP only via WARP private networks) — verified
against Cloudflare's routing/protocol docs 2026-07-12; this is the basis of the no-VPS shape.
Asterisk AudioSocket long-stable (knowledge-based — pin versions at build time).

---

## 5. The phone experience (how a call actually runs)

1. **Lines open.** Paul toggles LINES OPEN in his console → overlay shows the CTA card with
   the reward name; the reward auto-pauses/unpauses on Twitch to match.
2. **Redeem + queue.** Viewer redeems "📞 CALL THE PORK PHONE (60s)", opens
   `thekartoff.com/phone`, logs in with Twitch, passes the consent screen (§10.2), does a mic
   check, joins the queue. Page shows position + LINES state, and tells them to **mute the
   stream while on the line** (they'd otherwise hear themselves N + Twitch-latency seconds
   late).
3. **Ring.** Paul clicks ANSWER NEXT (or auto-ring next in queue): the app first wakes the
   caller's page over its queue WebSocket and brings up their audio WebSocket (mic goes live
   only now — no idle connections; if their tab has gone away they're skipped, credit left
   unspent), then originates the phone leg; **the real bell rings**, heard naturally by the
   room mic. The overlay + console show the caller's Twitch name and INCOMING….
4. **Answer.** Paul lifts the horn — off-hook = answered. Timer (their purchased seconds)
   starts at ATA answer, shown on overlay + console + caller's page. The daemon crossfades:
   stream mic gates down, the two phone channels take over (§9).
5. **Talk.** Fully real-time, both directions. Paul's free hand hovers on the desk **DUMP
   button** — one press dumps the last N seconds of the phone channels before they air (§6).
6. **Wrap.** T−10 s: courtesy beep into both legs. T0: app plays a "time's up" tone and hangs
   up (console EXTEND +30 s exists). Redemption marked FULFILLED. Next caller.
7. **If it goes wrong.** BAN button: hangs up, blacklists the Twitch id (optionally bans from
   channel via Helix), auto-refund withheld (they spent the points on being banned). Recording
   + dump log retained as evidence (§10).
8. **Never connected?** Stream ends / lines close with unspent credit ⇒ redemption CANCELED via
   API = points auto-refunded. Clean channel-point hygiene.

Future format idea (post-v1): **software voicemail roulette** — the app answers, records the
caller's message, plays it into the stream on Paul's cue; he "picks up" the good ones live.
Recreates the old answering-machine-screening bit with no TAD hardware.

---

## 6. Moderation: broadcast delay + the dump key (the load-bearing feature)

Radio's actual solution, adopted wholesale: **the call is never delayed — the broadcast is.**
Paul hears the caller raw and instantly, which is what makes it a conversation; everything
OBS sends to Twitch/YouTube runs **N seconds behind the room** (default 4, runtime-tunable),
and bleeps are applied inside that window. (Rev 1 buffered the call path instead so nothing
bad ever reached the room — review verdict: that breaks conversational turn-taking, and it's
Paul's room; his ears can take it. Only the broadcast needs protecting.)

- **No automatic detection in v1** (review decision). Streaming ASR is genuinely good now, but
  it is not catch-every-slur-with-zero-misses good — mangled pronunciations, accents, and
  creative phrasing beat wordlists, and a doxx is just ordinary words. In this design it's
  also redundant: Paul hears everything live, so *he* is the detector — the same trust radio
  places in its dump operator. The bleeper keeps a clean seam (`mark_span(t0, t1)` +
  `dump_all()`) so ASR could bolt on later as an assist; it is not a safety dependency.
- **The dump key is a desk button** — Stream Deck key, USB foot switch, or a big red USB
  "CENSOR" button next to the phone (on-camera prop value) — HTTP straight to the bleeper
  daemon, near-instant. (The rotary has no keys; on any future tone-dial phone, `#` mid-call
  → ATA DTMF → Asterisk → daemon also works, ~150–300 ms — the design keeps both triggers.)
- **Dump semantics:** one press replaces the **entire current buffer of both phone channels**
  (caller channel + Paul's phone-mic channel — handset sidetone means his transmitter carries
  a faint copy of the caller, so they dump together) with the bleep tone, and fires a
  CENSORED card on the (equally delayed) overlay. Game audio and overlay carry on under the
  beep; the stream mic is gated during calls anyway (§9). Predictable, covers the word's
  start (already mid-buffer by the time anyone reacts); radio dumps work exactly this way.
  Each dump is logged as a strike against the call.
- **Reaction-time math (why the default is 4 s, not 2):** a slur is only recognisable near its
  *end* (~0.4–0.6 s after it starts); recognition + decision + finger ≈ 0.5–1.0 s; trigger
  path ≈ instant — so the press lands ~1.0–1.9 s after the word began. N=2 leaves 0.1–1.0 s
  of margin when Paul is attentive, and **negative margin when he's mid-laugh, mid-sentence,
  or reading chat**. N=4 covers the distracted case (radio gives its operators 6–8 s *plus* a
  producer). It's a ring-buffer length — a runtime knob, not a commitment: Paul can trial 2 s
  live and dial up after the first close call. The only cost of a bigger N is chat lag, and
  Twitch already adds 4–8 s of its own; a phone show doesn't need frame-tight chat.
- **The delay chain (streaming PC, where OBS lives):**
  - OBS's built-in Stream Delay is useless here — it buffers *encoded* output, which can't be
    bleeped. The delay lives source-side, pre-encoder — which also means every output
    (Twitch, YouTube, local recording) is protected uniformly.
  - **Audio:** all stream-bound audio routes through the **bleeper daemon** (N-second ring
    buffer + HTTP dump endpoint) into OBS via virtual audio devices: the two phone channels
    (§9), the stream mic (auto-gated during calls, §9), and capture-card audio. What the
    daemon holds, the dump key can overwrite.
  - **Video:** "Video Delay (Async)" filter on the camera/capture source set to the same N
    (seconds-scale, unlike the 500 ms-capped Render Delay filter — verify the cap at the
    Phase 3 bench; fallback is routing video through a delay relay too).
  - **Overlay:** the hotline app delays overlay *events* by N server-side before pushing to
    the OBS browser source — delaying data beats delaying pixels. The caller's page and
    Paul's console stay real-time; only stream-facing surfaces are delayed.
  - Toggling the delay mid-stream causes a visible hiccup (buffer fill/drain) — flip it under
    a scene transition, or just run delayed for the whole phone show.
- **Earpiece-only listening makes manual moderation near-airtight:** the caller's voice never
  exists acoustically in the room — it lives only in Paul's earpiece and in the digital
  caller channel. The stream's sole copy of the caller is that channel, so the bleep is
  surgical and nothing can leak via the room mic (gated during calls regardless). The only
  residual copy is the faint sidetone on Paul's phone channel, which the dump covers.
- **Residual risk, stated honestly:** Paul can blink, and the slur *does* reach his ears.
  Then it airs once, he hangs up + bans — the same residual class every TTS-donation streamer
  accepts. Twitch holds streamers responsible for what airs regardless of source; prompt
  removal/ban + willingness to VOD-edit is the accepted mitigation posture (knowledge-based;
  skim current Community Guidelines at build).
- **Deterrence stack in front of the tech:** costs channel points → burns a Twitch account in
  good standing → name on the overlay and stream → consent screen states the ban policy →
  dumps are recorded as strikes. Trolling economics are poor.
- Paul→caller direction is never filtered or delayed. Recordings capture the **raw** feed plus
  the dump log (spans + timestamps): evidence of both what was said and what actually aired.

---

## 7. Twitch integration

### 7.1 Identity — "Log in with Twitch"
Standard authorization-code OAuth handled by the hotline service (routed under
`thekartoff.com` via the existing tunnel, so the session cookie is same-site and the SPA page
fetches with credentials — no third-party-cookie pain). We store the immutable Twitch
**user id** (survives renames — bans stick), display name, avatar. No scopes needed for
viewers beyond identity.

### 7.2 Credits — channel points (verified against Helix docs)
- One-time: Paul authorizes the app (broadcaster token, scopes `channel:manage:redemptions`
  + `channel:read:redemptions`; requires Affiliate/Partner — Paul qualifies).
- App creates/owns the reward via `POST /helix/channel_points/custom_rewards`
  (cost/title/cooldowns configurable; **note:** API-created rewards are editable *only by the
  creating app* — Paul edits cost etc. through our console, not the Twitch dashboard).
- Native anti-spam knobs confirmed: `max_per_stream`, `max_per_user_per_stream`,
  `global_cooldown_seconds`.
- **EventSub** `channel.channel_points_custom_reward_redemption.add` (webhook transport with
  HMAC verification on the hotline service — the tunnel provides the public HTTPS callback;
  WebSocket transport confirmed available too — handy for local dev).
- Redemption lifecycle via `PATCH …/redemptions`: hold **UNFULFILLED** on arrival → **FULFILLED**
  when the call completes → **CANCELED** to refund (docs excerpt showed the statuses; the
  points-are-returned-on-CANCELED sentence is the one API fact to re-confirm first thing at
  build — the design leans on it for auto-refunds).
- Ledger: `credits(twitch_user_id, seconds, source, status: unspent|reserved|spent|refunded)`.
  Credit is *reserved* at queue join, *spent* at ATA answer, auto-refunded at stream end.

### 7.3 Verifying the redeemer is the caller
The EventSub payload carries the redeemer's user id; the session carries the logged-in user
id; a queue join requires both to match an unspent credit. One live session per user, one
queue slot per user, credit consumed at answer. Done — no codes to paste, no honor system.

### 7.4 Real money (phase 4, design-ready)
**Stripe Checkout** directly on the call page (user already authenticated) — platform-agnostic,
works for YouTube viewers too, ~A$0.30 + 1.7–1.75 % per transaction, webhook credits seconds
into the same ledger. Bits (`channel.cheer` EventSub exists) are possible but message-parsing
jank; prefer Stripe. Ops notes: no-refunds-for-bans policy in the terms; AU GST only matters
past A$75k turnover; donations may still be assessable income — one accountant question.

### 7.5 YouTube (later, slots in)
No channel-points analog exists. Money events = **Super Chat / Super Stickers / memberships**;
`liveChatMessages.superChatDetails` exposes amount + author channel id (API details to verify
at build — not research-verified). Identity = Google OAuth alongside Twitch in the same
`identities` table; a Super Chat then credits the matching channel id, or YouTube viewers just
use Stripe. The ledger schema above already fits all of this.

---

## 8. Website placement

### 8.1 Where each piece lives
| Piece | Where | Why |
|---|---|---|
| **Caller page `/phone`** | `web/` SPA (unlisted URL-only route, like `/heat`) | Same-site cookies with the hotline API; KART-OFF print design language; announceable later by simply adding a nav link. |
| **Hotline API + audio WS + EventSub** | `hotline/` service on the Pi host — own systemd unit, own SQLite, own local port, routed through the **existing Cloudflare tunnel** (subdomain `phone.thekartoff.com` or a path ingress rule — both proxied, no exposed A record) | WS-framed audio rides the tunnel (§4.1); the Pi's existing server code is untouched; deploys decoupled from site tags. |
| **Paul's console `/console` + OBS overlay `/overlay`** | Served by the hotline service (admin token; overlay token in the browser-source URL; overlay events delayed N s server-side, §6) | Private tooling — iterate without site deploys. |
| **Bleeper daemon** | Small service on the **streaming PC** (N-second ring buffer, dump HTTP endpoint, virtual-audio outputs, mic gate) | The delay must live where OBS lives — the Pi can't touch the PC's audio path. |

### 8.2 Repo layout
New top-level `hotline/` with two halves: `server/` (the Pi service — app + AudioSocket
client + Asterisk config templates + systemd units + deploy script; Python asyncio or Node,
decide at build) and `bleeper/` (the streaming-PC daemon: ring buffer, dump endpoint,
virtual-audio outputs, mic gate, earpiece-sim EQ, OBS setup notes). `web/` gains the `/phone`
route + components. Root `CLAUDE.md` gains a fifth surface row. The Pi's existing server code
is untouched — separate service, separate DB, same host and tunnel.

### 8.3 Design language
The page is public-facing KART-OFF material: "KART-OFF print" language (per the site
redesign spec) — the call page should read like event collateral (THE PORK PHONE HOTLINE),
with the queue/status as a ticket stub. Overlay matches stream package. (frontend-design skill
at build time; no visuals locked in this spec.)

### 8.4 Embeds note (asked in brief)
Twitch player + chat embeds work for anonymous viewers (parent-domain param required); posting
chat triggers Twitch's own login inside the iframe. Embedding live stream + chat on `/phone`
next to the queue would be a nice touch and needs nothing from our auth.

---

## 9. Getting the call onto the stream (audio plan)

- **The ATA is the splitter — no hardware tap exists or is needed.** The two directions
  already exist as separate digital streams at the app: the **caller channel** (browser →
  app; taken *post*-8 kHz-resample so it's naturally phone-grade) and **Paul's phone channel**
  (802 transmitter → ATA → Asterisk → app; natively crusty, no EQ needed). The app forwards
  both to the PC bleeper daemon as **separate channels → separate OBS mixer sources** —
  individually EQ-able, individually meterable, jointly dumpable (§6). (Yes, the call audio
  rides the LAN from the Pi to the PC — the same WS/TCP framing as everything else, a few
  milliseconds — and the daemon surfaces it to OBS through the virtual audio devices.)
- **Earpiece simulation — "they hear what I hear":** the digital caller channel is tapped
  *upstream* of the handset, so raw it sounds cleaner than what Paul actually hears through a
  1970s receiver capsule. Paul's own side needs nothing — the transmitter is *in* the
  captured chain, his crust is real — but the caller channel gets an **earpiece-sim** stage
  in the daemon: at the Phase-0 bench, record the real earpiece (sweep + speech, mic held to
  it), then match by ear — band-pass + resonant peaks + a touch of saturation, or a
  convolution IR — until the stream A/Bs against the physical thing. Purist upgrade if ever
  wanted: a **re-amp box** — a spare **Receiver 4T** capsule in a small enclosure with a mic,
  physically playing the caller channel through the genuine transducer.
- **Stream-mic auto-gating:** the daemon already owns the stream mic (§6), so on CALL_ACTIVE
  it crossfades — stream mic down (default: full gate; a −12 dB ambience bed is a runtime
  knob if full mute feels dead), phone channels up; reverse on hangup. During a call, *all*
  of Paul is phone-quality: the whole show goes down the line, which is the bit. This also
  solves the double-path problem — without gating, his voice would reach the stream twice
  (room mic + phone transmitter, ~100 ms apart = audible comb filtering).
- **The bell rings acoustically** — the stream mic is open pre-answer, so the real bell is
  heard naturally in the room mix, no injection needed.
- **Echo: best case of any plan.** Earpiece-only means the caller's voice never plays aloud,
  so there's no acoustic return path — the caller hears only normal handset sidetone of
  themselves.

---

## 10. Data, consent, law (quick notes — not legal advice)

1. **Schema (hotline's own SQLite on the Pi):** `identities` (twitch user id, names, avatar) ·
   `credits` (§7.2) · `bans` (user id, reason, strike refs) · `calls` (caller, timestamps,
   seconds bought/used, outcome, recording/dump-log paths) · `strikes` (call id, dump
   timestamp, span, action) · `settings` (reward cost/seconds, delay length N). Nightly copy
   to B2 (it already lives on the Pi).
2. **Consent screen (blocking, per caller):** "your call is broadcast live and recorded;
   profanity may be bleeped; slurs/doxxing = instant ban; be funny." Click-through consent
   makes the recording + broadcast clean even under all-parties states (NSW etc.), since every
   party consented in writing. Keep the acceptance timestamped in `calls`.
3. **Telecom licensing:** none — private VoIP loop, no PSTN interconnect, not supplying
   carriage to the public.
4. **Recordings:** WAV (both legs + mix, raw pre-dump) + dump log per call, 90-day retention,
   console download (ban evidence, VOD sync, highlights).

---

## 11. Real-world runbook for Paul

**Shopping list (~A$70–100 one-time, no monthly cost):**
1. **Grandstream HT802V2** ATA — ~A$63–89 (AV Mart had A$63; any AU VoIP shop).
2. **RJ11-plug → 605-socket adaptor** (~A$5–10, eBay AU — direction matters, §3.1); RJ11
   lead usually boxed with the ATA.
3. **Dump button:** Stream Deck key if one's already on the desk, else a USB foot switch or
   big red USB button (~A$15–25 — on-camera prop value).
4. Software-only on the streaming PC: a virtual audio cable (VB-Audio class, free) for the
   bleeper outputs.
5. The Panasonic needs **nothing, ever** (it never powers on). Exception: a set of fresh AAAs
   if the optional §3.3 sampling session ever happens — handset only, base stays boxed.

**Phase 0 — bench day (when the ATA arrives, ~1 hour, no cloud):**
1. Phone's 605 plug → adaptor → ATA FXS port 1; set AU SLIC/impedance, AU double-ring
   cadence, **hook-flash detection off**.
2. On the PC: MicroSIP/Zoiper → **direct-IP INVITE to the ATA's LAN IP** (registrar-less
   mode is stock HT80x) → **does the bell ring?** The bench day's headline question — odds
   high (§3.1: the bell accepts 16–50 Hz; loudness wheel wound up?). Audio-but-no-ring = the
   mode-3 bell strap (§3.1); weak ring = ATA ring settings / re-tension / booster.
3. Lift the horn, talk to yourself from the PC: audio both ways, transmitter level (adjust
   ATA TX/RX gain), earpiece loudness, hangup detection on hanging the horn (slam it a few
   times — hook bounce must not false-hangup; dial-spin mid-call must do nothing).
4. **Earpiece-sim reference capture (§9):** play a sweep + some speech down the phone leg
   and record the earpiece with a mic held to it — the target the daemon's caller-EQ gets
   matched against.
5. Report back: bell verdict + audio/gain findings + which transmitter inset it has (carbon
   No. 13 vs electronic 20E — sticker or listen).

**Phase 1 — first internet ring (Pi):** Asterisk + hotline skeleton on the Pi; ATA registers
over the LAN; tunnel ingress for the audio WS; a bare test page rings the phone from outside
the house; end-to-end latency + audio check (this phase also proves or kills the
WS-over-tunnel bet — the VPS is the fallback if it stutters). **Phase 2 — product:** Twitch
OAuth + reward + EventSub + queue + console + overlay + recording. **Phase 3 — the delay
chain (streaming PC):** bleeper daemon + virtual audio + OBS per-source delays + desk DUMP
button + stream-mic gating + earpiece-sim EQ + CENSORED overlay card + strike/ban wiring;
bench the OBS filter caps. **Phase 4 — money/YT** (§7.4/7.5). Each phase lands behind the
unlisted page; nothing touches existing surfaces.

---

## 12. Open decisions (answer at sign-off) & known-unverified

1. **Delay length N** (§6): Paul proposes 2 s; the reaction-time math says 4 s covers the
   distracted case. Written default = 4, runtime-tunable — trial both live.
2. **Dump semantics + button** (§6): whole-phone-channel-buffer dump (written default,
   radio-style) vs hold-to-bleep-a-span; and which physical button (Stream Deck / foot
   switch / big red USB button).
3. **Reward economics:** default cost/seconds (e.g. 25k points / 60 s, 1 per user per stream,
   120 s cooldown) — Paul tunes live via console.
4. **Auto-ring next vs manual ANSWER NEXT** (recommend manual — Paul controls pacing).
5. Service/branding name: `hotline/` + "THE PORK PHONE" placeholder.

**Known-unverified (re-check first at build):** CANCELED-refunds-points sentence; **the 802's
bell vs the HT802 ring generator** (Phase 0 test #1 — odds high, fallbacks in §3.1/§11); OBS
"Video Delay (Async)" maximum + audio sync-offset cap on current OBS (Phase 3 bench);
WS-audio behaviour through the tunnel under real caller networks (Phase 1 proves it; named
fallback = the rev-1 VPS); YouTube Super Chat API scope terms; current Twitch Community
Guidelines wording. All shape-safe: none can invalidate the architecture, only tweak
numbers/defaults.

---

## 13. Sources (primary)

Telecom 802 identification: britishtelephones.com/aus/800.htm (bell 16–50 Hz, loudness
control, inset No. 13/20E, Receiver 4T, dial history) + the phone's own 605-plug mouldings ·
ACMA cordless class licence + 1.9 GHz arrangements pages · spectrum-tracker Telstra n1
listing · AMTA illegal-devices notice · AU 2100 MHz refarm coverage (Telstra/Optus
post-3G-shutdown) · rfnsa.com.au (local-tower verification tool) · Cloudflare Tunnel
routing/protocol docs (HTTP + WS to anonymous visitors; raw TCP needs client-side
`cloudflared access`; UDP only via WARP private networks) · Grandstream HT80x Admin Guide
(PDF) + AV Mart/Arrow AU listings + Whirlpool AU config threads · Cisco SPA112 EOL notice ·
eBay AU / Access Communications listings for RJ11→605-socket adaptors · Twitch Helix API
reference + EventSub docs · Panasonic KX-TG103x manual (via manualslib + Panasonic NA) — now
relevant only to the retired prop.

Research note: rev 1's core phone/ATA/spectrum claims were adversarially verified against
the manual/ACMA text. Rev 3 (2026-07-12) is the rotary-native rewrite folding in all three
review rounds; the superseded cordless-era material (Panasonic feature research, DECT mod
options, lawful sound-port menu, AU-twin listing hunt) is preserved in git history
(`1e533f9` → rev 2.2).
