# The Pork Phone — viewer call-in hotline (design)

**Date:** 2026-07-11 · **Status:** DRAFT — awaiting Paul's review · **Surface:** new `hotline/`
service (VPS) + unlisted `web/` page + one ATA + the Panasonic phone. Zero changes to the Pi,
the engine, or any existing route.

The bit: viewers spend channel points on the KART-OFF site to ring a real 2007 Panasonic
cordless phone on Paul's desk, live on stream. The phone displays their Twitch name on its
caller-ID screen, Paul answers on speakerphone (confirmed: the handset has one), and a
radio-station-grade profanity delay — that Paul operates by *reading ahead*, not by reflexes —
keeps slurs off the stream and out of the room entirely.

---

## 1. Answers to the brief (index)

| Question in the brief | Answer | Section |
|---|---|---|
| How does the phone receive calls online, no phone number, no location leak? | An analog telephone adapter (ATA) gives the phone a fake "phone line"; it connects *outbound* to a small Sydney VPS that is the actual "phone company". No PSTN, no number, no ports opened at home, callers only ever see the VPS. **No modification to the phone itself — none needed.** | §3, §4 |
| Channel points → seconds of call time; prove the redeemer is the caller | Twitch custom reward + EventSub redemption webhook + "Log in with Twitch" on the call page; the redemption and the login carry the same immutable Twitch user id. Unused redemptions are refunded via API. | §7 |
| Bleep system a solo host can operate + blacklist | The caller's audio passes through a ~2.5 s buffer on the VPS *before it reaches the phone*. Live captions show Paul what's about to play ~2 s before the room hears it; one DUMP key mutes it. A streaming speech-to-text wordlist auto-bleeps on top. Ban button writes the Twitch user id to a blacklist (and optionally bans them from the channel). Nothing bad ever needs to air — not even in Paul's room. | §6 |
| Hide the call page unlisted on thekartoff.com? | Yes — `/phone`, URL-only, exactly like `/heat` and `/version` today. Twitch OAuth is the real gate; unlisted-ness is just polish until announce. | §8 |
| Twitch login? YouTube login? YouTube's channel-points equivalent? | Twitch OAuth now. YouTube has **no channel-points analog**; its money events are Super Chat / memberships (readable via API). The credits ledger is provider-agnostic so Google OAuth + Super Chat (or Stripe for real money on any platform) slot in later. | §7.5 |
| Do embedded stream/chat on the site require viewer login? | No. Twitch embeds view anonymously; *sending* chat prompts a Twitch login inside Twitch's own iframe. No dependency on our OAuth either way. | §8.4 |
| Speakerphone every time — can it toggle/auto-answer? | The KX-TGA101 handset **has a dedicated SP-PHONE key** (verified in the manual). One key-press answers a ringing call straight to speakerphone. There is no in-cradle full auto-answer ("Auto Talk" is lift-to-answer only). Two fallback audio modes if speakerphone disappoints: answering-machine screening mode, and a clean digital tap into OBS. | §5, §9 |
| "Probably can't test until we modify it" | Wrong in a good way: the day the ATA arrives, a free softphone on the PC can direct-IP ring the phone on the LAN — full physical bench test before any cloud work. | §11 Phase 0 |

---

## 2. Requirements (from Paul's brief)

1. Physical Panasonic **KX-TG1031S** base + **KX-TGA101** handset (US model, in transit) rings
   with real, two-way calls placed by viewers from a web page. No real phone number. Nothing
   that exposes Paul's home IP, address, or identity to callers.
2. **Channel points → N seconds** of call time, with proof that the spender is the caller.
   Real money later; possibly YouTube later.
3. **Bleep/censor** workable by one person who cannot place bleeps reactively in real time;
   **blacklist** anyone who forces a bleep.
4. Call page on the existing site if sensible, unlisted; viewer identity via platform login.
5. Paul's audio plan A is **speakerphone always** (chat hears what he hears, phone-quality,
   funnier). Fallback: route call audio into OBS as its own channel with a filter.
6. Hardware purchases, phone internals modification, home network changes all allowed.
   Paul is in Australia. Product name used in this doc: **the Pork Phone** (rename freely).

Out of scope for v1 (design keeps the door open): real-money purchases, YouTube identity and
Super Chat credits, multiple streamers' phones, queue-jump pricing, caller video.

---

## 3. Verified hardware facts (research, 2026-07-11)

Verified directly against the Panasonic operating manual (family manual for KX-TG103x, whose
accessory handset is the KX-TGA101) by multiple independent checks:

| Fact | Detail | Confidence |
|---|---|---|
| **Speakerphone: YES** | Handset has a dedicated `{s} (SP-PHONE)` key (manual p.13 "Handset View"). Pressing it while ringing answers hands-free. Budget-line so it is **half-duplex** (one direction at a time) — see §9 echo notes. | Verified (manual p.13 + retailer specs) |
| **Headset jack: YES** | 2.5 mm jack on the handset (p.13); Panasonic headsets KX-TCA60/86/88HA/92/93/94/95 (p.5, p.38). Bonus analog tap option. | Verified |
| **Auto Talk** | Lift-handset-to-answer without pressing TALK (toggle, p.24). **No in-cradle auto-answer exists.** | Verified (p.17, p.24) |
| **Answering system** | Built-in digital TAD. **Call screening is documented through the *handset* speaker** ("While a caller is leaving a message, you can listen through the handset's speaker… answer by pressing {C}"). Base speaker is documented for message playback; base-speaker *screening* unconfirmed — test on arrival. | Manual-quoted; verify physically |
| **Caller ID display** | "The calling party's **name** and phone number are displayed"; last-50 caller log. Alphanumeric CNAM works ⇒ **the handset can display the caller's Twitch name**, and the phone's caller list becomes a physical artifact of the stream. | Manual-quoted |
| **Radio** | DECT 6.0, **1.92–1.93 GHz**, ~100 mW (manual p.47 Specifications). | Verified |
| **Power** | **120 V AC 60 Hz only.** Wall adaptors: PQLV207V (base), PQLV209V (charger). ⚠️ **Do not plug into AU 230 V mains through a pin adapter — it will cook.** See §11 shopping list. | Verified (p.4, p.47) |
| Vintage | Released 2007, discontinued; one handset ships pre-registered with the base. | Verified |

### 3.1 The Australian DECT wrinkle (flag, Paul decides)

- The ACMA cordless class licence authorises DECT at **1880–1900 MHz only**; no listed cordless
  band covers 1920–1930 MHz. Operating this US phone in AU is therefore **outside the class
  licence** (technically unlawful to operate).
- Worse than a paperwork issue: **1920–1930 MHz sits inside Telstra's licensed band-1/n1
  *uplink* (1920–1980 MHz)**, still allocated and in LTE/NR use after the 3G shutdown. A DECT
  base beacons continuously even when idle.
- Practical read (honest, not legal advice): ~100 mW indoors is very unlikely to bother a cell
  tower, and enforcement against a hobbyist is essentially unheard of — but it *can* degrade
  n1-uplink for phones in the same room, i.e. self-jamming Paul's own mobile. Mitigations:
  keep the base away from where phones live, power it only during phone segments, or buy the
  compliant twin — any AU-market DECT phone with CID + speakerphone (~A$50 new) drops into this
  design unchanged, because the ATA doesn't care what phone it rings. The US unit *looks* the
  part; the AU unit is the lawful understudy. **Decision left to Paul.**

### 3.2 ATA (the one purchase that matters)

- **Buy: Grandstream HT802V2** (2-port FXS ATA) — in stock in AU (AV Mart, **A$63** on sale,
  RRP ~A$89; HT802 ~A$70 widely; Arrow Computers sells it as the official SPA112 replacement).
  One port is enough (HT801 = 1-port twin) but the V2 is the current, cheap, safe pick.
- **Do NOT buy a Cisco SPA112**: end-of-sale June 2020, support ended May 2025 — six years of
  unpatched firmware on a device that terminates SIP.
- HT80x confirmed features (admin guide): **Bellcore/Telcordia FSK caller-ID** incl. name
  (exactly what a US Panasonic expects — an AU practitioner thread confirms Bellcore scheme is
  the one that works on these); configurable **ring cadence** (default already the US
  2 s on/4 s off — authentic ring out of the box); **SIP over TLS + SRTP** (forceable);
  **direct IP-to-IP calls without a registrar** (useful for the Phase-0 bench test); per-port
  SLIC impedance setting (US phone ⇒ USA).
- Practitioner gotcha from that thread: caller-ID display fails on handsets with dead/corroded
  batteries — check the NiMH cells in this 2007 phone early (see Phase 0).

---

## 4. Architecture

### 4.1 Privacy model first (it drives the shape)

- thekartoff.com already fronts the Pi via a **Cloudflare Tunnel** — home IP hidden, and the
  tunnel is HTTP/WS-only, so **live call audio cannot ride it**. ⇒ the call server lives on a
  **small Sydney VPS** (`phone.thekartoff.com`, plain A record; Vultr/DO/Binary Lane class,
  1–2 GB, ~A$10–15/mo — pick at build time).
- The **ATA connects outbound** from Paul's LAN to the VPS (SIP REGISTER over **TLS**, media
  **SRTP**, short re-register + keepalive to hold the NAT pinhole). **Zero inbound ports at
  home, no WireGuard needed, nothing at home is scannable.** Home IP is visible only inside
  Paul's own VPS logs — never to callers.
- Callers' browsers speak WebRTC (DTLS-SRTP) to the VPS only. WebRTC ICE will expose the VPS
  IP in SDP — that's a Sydney datacenter address, which is fine (Paul is publicly Australian).
- No PSTN anywhere ⇒ no phone number exists to leak, and no telecom licensing applies (private
  VoIP loop, not a public carriage service — see §10.3).

### 4.2 Chosen stack — Option B: "Python brain + Asterisk plumbing" (one VPS)

```
viewer browser ──WebRTC (Opus/DTLS-SRTP)──► hotline app (Python/asyncio, aiortc)
                                              │  queue · credits · OAuth · ASR tee
                                              │  [2.5s bleepable ring buffer] ──► captions/console/overlay (WS)
                                              │  recording (WAV + transcript)
                                              ▼  AudioSocket (16-bit/8kHz PCM over local TCP)
                                            Asterisk (same VPS)
                                              │  PJSIP endpoint: ATA registration (TLS+SRTP), CNAM=Twitch name
                                              ▼  SIP/RTP over the internet (encrypted, outbound-held NAT path)
                                            HT802V2 ATA (Paul's LAN) ──RJ11──► KX-TG1031S base ──DECT──► handset 🔔
```

- **hotline app** (one Python service, mirrors the engine's asyncio style): serves the
  page/console/overlay APIs + WebSockets, Twitch OAuth + EventSub, the queue and credit
  ledger (SQLite), the WebRTC leg (aiortc), the **bleep buffer**, streaming ASR, and per-call
  recording. All product logic in one testable process — it can be exercised end-to-end with
  WAV files and no telephony at all.
- **Asterisk** (boring, rock-solid): the ATA's registrar (NAT traversal is its bread and
  butter), G.711 to the phone, ring cadence/CID delivery, and an **AudioSocket** bridge — a
  trivial TCP protocol that hands the app raw 8 kHz PCM in both directions. Call placement via
  ARI: app says "originate PJSIP/ata, then AudioSocket me the audio", Asterisk does telephony.
- Caller→phone audio passes: browser → app (jitter buffer, decode, resample) → ASR tee →
  **2.5 s ring buffer (the bleep insert)** → AudioSocket → Asterisk → SRTP → ATA → phone.
  Phone→caller returns unbuffered (Paul must never feel delayed). Latency: caller speech
  reaches the room ≈ 2.7 s later; Paul's replies reach the caller in ~0.2 s — satellite-link
  vibes, workable and honestly funnier on a crusty phone.

### 4.3 Alternatives considered

| Option | Shape | Why not v1 |
|---|---|---|
| **A. All-Asterisk** | Browser speaks WebRTC *to Asterisk* (chan_pjsip WSS + JsSIP), app only does AudioSocket processing | Asterisk's browser-WebRTC config (WSS certs, DTLS, ICE) is the fiddliest part of Asterisk; aiortc replaces it with ~50 lines of Python we fully control. Fallback if aiortc misbehaves. |
| **C. LiveKit self-host** | livekit-server + Redis + livekit-sip (+ agents) | Actively maintained (v1.6.0 Jul 2026) and genuinely good — but three services, and livekit-sip is **not a SIP registrar**, so the ATA can't register to it; the home leg would need WireGuard + direct-IP INVITEs. More ops for no v1 gain. Re-evaluate if this grows into multi-streamer rooms. |
| **D. Twilio managed** | Voice JS SDK (browser) + SIP Domain registration for the ATA (Sydney edge exists) + bidirectional Media Streams (AU1 region) for the bleep | Least self-hosting *until the bleep*: bidirectional streams are one-per-call and inbound-track-only, so the interpose becomes two Twilio calls cross-patched through our WebSocket server — we end up running the media brain anyway, now with per-minute fees, a min 600 s re-register (needs separate ATA keepalive), and a closed dependency. Good plan-B if self-host SIP fights us; ~US$0.008/min both legs is negligible at our scale. |

Stack facts checked: livekit/sip actively released through Jul 2026; Twilio SIP registration +
Sydney edge + AU1 media streams all documented. aiortc and Asterisk AudioSocket are
long-stable (knowledge-based — pin versions at build time).

---

## 5. The phone experience (how a call actually runs)

1. **Lines open.** Paul toggles LINES OPEN in his console → overlay shows the CTA card with
   the reward name; the reward auto-pauses/unpauses on Twitch to match.
2. **Redeem + queue.** Viewer redeems "📞 CALL THE PORK PHONE (60s)", opens
   `thekartoff.com/phone`, logs in with Twitch, passes the consent screen (§10.2), does a mic
   check, joins the queue. Page shows position + LINES state.
3. **Ring.** Paul clicks ANSWER NEXT (or auto-ring next in queue): the app first wakes the
   caller's page over its queue WebSocket and brings up their WebRTC leg (mic goes live only
   now — no idle connections; if their tab has gone away they're skipped, credit left
   unspent), then originates the phone leg; **the handset rings with their Twitch name on the
   caller-ID display** (CNAM, truncated ~15 chars; number field carries queue position).
   Overlay shows INCOMING….
4. **Answer.** Paul hits **SP-PHONE** — call is live on speakerphone. Timer (their purchased
   seconds) starts at ATA answer, shown on overlay + console + caller's page.
5. **Talk.** Captions of what's *about to* play scroll on Paul's console ~2 s ahead of the
   room. DUMP key (Stream Deck/hotkey → HTTP) bleeps; wordlist auto-bleeps regardless (§6).
6. **Wrap.** T−10 s: courtesy beep into both legs. T0: app plays a "time's up" tone and hangs
   up (console EXTEND +30 s exists). Redemption marked FULFILLED. Next caller.
7. **If it goes wrong.** BAN button: hangs up, blacklists the Twitch id (optionally bans from
   channel via Helix), auto-refund withheld (they spent the points on being banned). Recording
   + transcript retained as evidence (§10).
8. **Never connected?** Stream ends / lines close with unspent credit ⇒ redemption CANCELED via
   API = points auto-refunded. Clean channel-point hygiene.

Alternate warm-up format (works day 1, zero conversation risk): **screening mode** — let the
TAD answer; the caller's message plays aloud (handset speaker per manual — physically confirm
speaker path on arrival) while Paul plays; he lifts/TALKs to "pick up" the good ones. Voicemail
roulette. The bleep buffer still protects the room in this mode; record a KART-OFF greeting.

---

## 6. Moderation: the predictive bleep (the load-bearing feature)

Radio solves profanity with a broadcast delay + a dump button — but that needs a producer.
The insight here: **all caller audio passes through our server before it exists anywhere in
the room**, so the delay lives *upstream of the phone*, and the operator can *read ahead*.

- **Ring buffer:** 125 × 20 ms frames (2.5 s, tunable 1.5–4 s) on the caller→phone path only.
- **ASR tee:** pre-buffer audio streams to a word-level ASR (Deepgram/AssemblyAI/Speechmatics
  class: streaming, word timestamps, custom keyword boosting for the slur list, decent with
  accents; ~US$0.005–0.01/min — pennies per stream; pick + price-check at build, keep the
  interface vendor-agnostic; local faster-whisper is the offline fallback but interim-result
  latency on a small VPS makes it the backup, not the plan).
- **Auto-bleep:** interim transcripts arrive within ~0.3–1 s of speech — comfortably inside
  the 2.5 s window. Wordlist hit ⇒ zero/1 kHz-tone exactly that word-span in the buffer
  (word timings give the span), flag the console, count a strike. Two lists: **hard list**
  (slurs, doxx patterns) = bleep + auto-hangup + auto-ban; **soft list** (garden profanity) =
  bleep only or let through — configurable; it's an Aussie stream, "fuck" is ambience.
- **Human dump:** captions render ahead-of-air with flagged words highlighted; DUMP mutes the
  entire current buffer + inserts the beep. Paul reads it before the room hears it — reflexes
  not required. (Radio pays US$1,500+ for this box; ours is a Python deque.)
- **Residual risk, stated honestly:** ASR can miss a mangled slur and Paul can blink. Then it
  airs once, he hangs up + bans — the same residual class every TTS-donation streamer accepts,
  minus the part where it was never supposed to reach his room at all. Twitch holds streamers
  responsible for what airs regardless of source; prompt removal/ban + willingness to
  VOD-edit is the accepted mitigation posture (knowledge-based; skim current Community
  Guidelines at build).
- **Deterrence stack in front of the tech:** costs channel points → burns a Twitch account in
  good standing → name on the phone screen and stream → consent screen states the ban policy →
  strikes are recorded. Trolling economics are poor.
- Paul→caller direction is never filtered or delayed.

---

## 7. Twitch integration

### 7.1 Identity — "Log in with Twitch"
Standard authorization-code OAuth on `phone.thekartoff.com` (VPS handles the redirect; session
cookie is same-site with `thekartoff.com`, so the SPA page fetches with credentials, no
third-party-cookie pain). We store the immutable Twitch **user id** (survives renames — bans
stick), display name, avatar. No scopes needed for viewers beyond identity.

### 7.2 Credits — channel points (verified against Helix docs)
- One-time: Paul authorizes the app (broadcaster token, scopes `channel:manage:redemptions`
  + `channel:read:redemptions`; requires Affiliate/Partner — Paul qualifies).
- App creates/owns the reward via `POST /helix/channel_points/custom_rewards`
  (cost/title/cooldowns configurable; **note:** API-created rewards are editable *only by the
  creating app* — Paul edits cost etc. through our console, not the Twitch dashboard).
- Native anti-spam knobs confirmed: `max_per_stream`, `max_per_user_per_stream`,
  `global_cooldown_seconds`.
- **EventSub** `channel.channel_points_custom_reward_redemption.add` (webhook transport with
  HMAC verification on the VPS; WebSocket transport confirmed available too — handy for local
  dev without a public callback).
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
| **Caller page `/phone`** | `web/` SPA (unlisted URL-only route, like `/heat`) | Same-site cookies with the VPS API; KART-OFF print design language; announceable later by simply adding a nav link. |
| **Hotline API + WebRTC + EventSub** | `hotline/` service on the VPS (`phone.thekartoff.com`, Caddy TLS, DNS-only A record — not proxied: WebRTC media terminates here anyway) | Tunnel can't carry call media; Pi stays sacred; deploys decoupled from site tags. |
| **Paul's console `/console` + OBS overlay `/overlay`** | Served by the VPS service directly (admin token; overlay token in the browser-source URL) | Private tooling — iterate without site deploys. |

### 8.2 Repo layout
New top-level `hotline/` (Python service on **aiohttp** — one asyncio loop shared with
aiortc, fewest deps — + AudioSocket client + asterisk config templates + Caddyfile + systemd
units + deploy script). `web/` gains
the `/phone` route + components. Root `CLAUDE.md` gains a fifth surface row. Pi untouched.

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

## 9. Getting the call onto the stream (audio plans)

- **Plan A — speakerphone (Paul's preference, confirmed possible):** SP-PHONE answers
  hands-free; the room mic hears both sides; chat hears exactly what Paul hears through a 2007
  speaker. Caveats: half-duplex speakerphone gates whoever talks second (authentically clunky,
  arguably part of the bit), and the caller hears their own voice back ~2.7 s late via the
  room mic → phone path whenever the speakerphone mic is live. If callers find it too
  disorienting, that's Plan B's cue. **Bench-verify** speakerphone loudness/quality Phase 0.
- **Plan A′ — screening mode:** TAD answers, message plays aloud, Paul optionally picks up
  (§5). Zero echo (one-way), zero dead-air risk, great warm-up format.
- **Plan B — digital tap:** the app already owns the post-bleep caller audio; an
  OBS browser source plays that same feed ("control audio via OBS" → own mixer channel +
  band-pass 300–3400 Hz "phone" EQ for the aesthetic), while Paul talks on the handset
  normally (earpiece — no echo, room mic carries only Paul). Cleanest sound, still
  phone-flavoured, available with zero extra hardware whenever Plan A disappoints. The
  2.5 mm headset jack is a hardware variant of the same idea if ever wanted.
- Ring cadence stays US-default (authentic), CNAM = Twitch name, and the handset's 50-entry
  caller list quietly becomes a physical guestbook of everyone who ever called the show.

## 10. Data, consent, law (quick notes — not legal advice)

1. **Schema (VPS SQLite):** `identities` (twitch user id, names, avatar) · `credits` (§7.2) ·
   `bans` (user id, reason, strike refs) · `calls` (caller, timestamps, seconds bought/used,
   outcome, recording/transcript paths) · `strikes` (call id, word, timestamp, action) ·
   `settings` (reward cost/seconds, buffer length, lists). Nightly copy to the Pi or B2.
2. **Consent screen (blocking, per caller):** "your call is broadcast live and recorded;
   profanity may be bleeped; slurs/doxxing = instant ban; be funny." Click-through consent
   makes the recording + broadcast clean even under all-parties states (NSW etc.), since every
   party consented in writing. Keep the acceptance timestamped in `calls`.
3. **Telecom licensing:** none — private VoIP loop, no PSTN interconnect, not supplying
   carriage to the public.
4. **Recordings:** WAV (both legs + mix) + transcript per call, 90-day retention, console
   download (ban evidence, VOD sync, highlights).

## 11. Real-world runbook for Paul

**Shopping list (~A$100 one-time + ~A$12–18/mo):**
1. **Grandstream HT802V2** ATA — ~A$63–89 (AV Mart had A$63; any AU VoIP shop).
2. **Power for the phone:** a small **230→120 V step-down converter** (~A$25–40, foolproof) —
   or read the PQLV207V wall-wart's DC output label on arrival and buy a matching AU DC
   supply (verify voltage/polarity/barrel before this route). **Never a pin adapter alone.**
3. RJ11 lead (usually boxed with the ATA). Fresh AAA NiMH cells for the handset (2007 stock
   will be dead, and dead cells break caller-ID display).
4. **Sydney VPS**, 1–2 GB (Vultr/DO/Binary Lane, ~A$10–15/mo) + DNS A record
   `phone.thekartoff.com` (Cloudflare, DNS-only).
5. ASR account (Deepgram-class, usually free credit) — pennies/min thereafter.

**Phase 0 — bench day (when phone + ATA arrive, ~1 hour, no cloud):**
1. Check the wall-wart label; power via step-down only. Fit fresh cells.
2. Confirm on the physical handset: SP-PHONE key answers a ring; find Auto Talk (p.24) and
   the TAD screening behaviour (which speaker plays the message aloud?); record a greeting.
3. Plug base line jack → ATA FXS port 1. On the PC: MicroSIP/Zoiper → **direct-IP INVITE to
   the ATA's LAN IP** (registrar-less mode is stock HT80x) → **the phone rings** → answer on
   speakerphone, talk to yourself from the PC. This proves ring voltage, cadence, CID text,
   speakerphone quality, and echo behaviour before any server exists.
4. Report back: speakerphone verdict (Plan A vs B) + TAD speaker finding + wall-wart output.

**Phase 1 — first internet ring (VPS):** provision VPS, Caddy + Asterisk + hotline skeleton;
ATA registers over TLS/SRTP; a bare test page rings the phone; end-to-end latency + audio
check. **Phase 2 — product:** Twitch OAuth + reward + EventSub + queue + console + overlay +
recording. **Phase 3 — moderation:** buffer + ASR + captions + dump/ban. **Phase 4 — money/YT**
(§7.4/7.5). Each phase lands behind the unlisted page; nothing touches existing surfaces.

## 12. Risks & open questions (answer at spec review)

1. **DECT band stance** (§3.1): run the US unit as-is / segment-only power / buy the AU twin
   as the lawful runner? (Recommend: bench it, keep it away from your mobile, decide after
   feeling the bit; the AU twin is A$50 insurance.)
2. **Buffer length default** 2.5 s — trade conversational snap vs read-ahead margin (1.5–4 s
   runtime-tunable; my default 2.5).
3. **Soft-profanity policy:** bleed through (recommended) or bleep-all?
4. **Reward economics:** default cost/seconds (e.g. 25k points / 60 s, 1 per user per stream,
   120 s cooldown) — Paul tunes live via console.
5. **Auto-ring next vs manual ANSWER NEXT** (recommend manual — Paul controls pacing).
6. Service/branding name: `hotline/` + "THE PORK PHONE" placeholder.

**Known-unverified (re-check first at build):** CANCELED-refunds-points sentence; Deepgram
current streaming price/keyterm API; exact VPS plan; YouTube Super Chat API scope terms;
current Twitch Community Guidelines wording. All shape-safe: none of these can invalidate the
architecture, only tweak numbers/vendors.

## 13. Sources (primary)

Panasonic KX-TG103x operating manual via manualslib (IDs 3814901/259519) + help.na.panasonic.com
family PDF · ACMA cordless class licence + 1.9 GHz arrangements pages · spectrum-tracker
Telstra n1 listing · AMTA illegal-devices notice · Grandstream HT80x Admin Guide (PDF) + AV
Mart/Arrow AU listings + Whirlpool AU config threads · Cisco SPA112 EOL notice · Twitch Helix
API reference + EventSub docs · livekit/sip GitHub (v1.6.0 Jul 2026) + LiveKit self-host SIP
docs · Twilio Voice JS SDK / SIP Registration (Sydney edge) / Media Streams (AU1) docs.
Research note: core phone + ATA + spectrum claims were adversarially verified against the
manual/ACMA text; remaining claims are primary-source-quoted but single-pass (an API session
limit cut the verification fan-out short) — flagged inline where it matters.
