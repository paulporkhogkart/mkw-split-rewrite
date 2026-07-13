# The Pork Phone — Paul's real-world runbook

Plan 1 (server core) is built and merged: the Pi service, Asterisk configs, deploy
scripts, and a bench test page all exist and are tested (58 tests). What remains is
**purchasing, plugging in, and two guided sessions**. Steps are ordered so nothing
blocks: Steps 1–2 need zero hardware and can happen while parts ship.

Spec: `docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md` ·
Plan: `docs/superpowers/plans/2026-07-12-pork-phone-plan1-server-core.md` (Task 15 = Step 2 here)

---

## Step 0 — Place the orders (today, ~10 min)

| # | Item | Where / search term | ~Cost | Notes |
|---|------|--------------------|-------|-------|
| 1 | **Grandstream HT802V2** ATA | AV Mart or any AU VoIP shop — search "HT802V2" | A$63–89 | The one purchase that matters. **Not** a Cisco SPA112 (EOL 2025, unpatchable). HT802 (V1) is fine if V2 unavailable. |
| 2 | **RJ11-to-610-socket adaptor** | [oldphones.com.au — "RJ11 to 610 Socket"](https://oldphones.com.au/product/rj11-to-610-socket/) (preferred: sold specifically for Grandstream HT ATAs) or eBay AU "RJ11 plug to 610 socket adaptor" | A$15 (eBay ~A$5–10) | **Direction check before buying:** it must have a 610 SOCKET (the receptacle) that your phone's 605 PLUG goes into, RJ11 on the other end. The common reverse product (605 plug with an RJ11 hole) is useless here. |
| 3 | **Telephone line coupler** (the analog stream tap) | Baseline: eBay/Amazon "transformer isolated telephone line audio coupler" (Sescom-class) · Premium (pre-approved): **JK Audio Inline Patch** — check AU pro-audio dealers or US import | A$30–80 / ~US$250–300 | Purchase criteria: transformer isolation, **ring-voltage protection**, RJ11 pass-through, line-level-ish output. |
| 4 | **DUMP button** (can defer to Phase 3) | Stream Deck key if you own one (A$0), else USB foot switch / big red USB button — eBay "USB foot switch single" | A$0–130 | On-camera prop value is a plus. |
| 5 | Inline attenuator pad (standby — decide at bench) | Only if the coupler runs hot into the iD4 DI | ~A$10 | Don't buy yet. |

**Do NOT buy:** a VPS (none in the design, zero monthly), anything for the Panasonic
(prop — never powered), an audio interface (iD4 MkII is sufficient), network gear
(UDM-Pro does the zones in config). **Also check at the desk:** one spare wired
ethernet port + patch cable for the ATA (it joins the PHONE VLAN on a tagged switch
port).

---

## Step 1 — Tonight, zero hardware: hear the system (5 min, your PC)

```powershell
cd hotline\server
python -m pip install -r requirements-dev.txt    # once
$env:HOTLINE_ENV="dev"; $env:HOTLINE_DATA_DIR="./devdata"; $env:HOTLINE_ECHO="1"
python -m hotline
```

Browser → `http://127.0.0.1:9100/test` → tick **echo-test mode** (wear headphones —
it disables echo-cancellation on purpose) → CONNECT MIC → RING PHONE → talk.

**Pass =** you hear yourself back at phone quality (~100–300 ms behind) and the event
log narrates `call_ringing → call_active → … → call_ended`. That's the worklets,
jitter buffer, 20 ms pump, and recorder proven end-to-end. The raw recording lands in
`hotline\server\devdata\recordings\<call-id>\` (caller.wav / phone.wav / mix.wav —
play mix.wav back). `Ctrl+C` to stop.

---

## Step 2 — Guided session with Claude: Pi deploy + first internet ring (zero hardware)

**Say "let's do T15"** in a session when you have: SSH to the Pi, Cloudflare dashboard
access, MicroSIP installed on your PC (free — it plays the ATA), and a phone hotspot
for the outside-the-LAN test. ~1–2 h. We walk plan Task 15 together:

1. Pull the repo on the Pi → run `hotline/server/deploy/install.sh` → set real tokens
   in `/etc/hotline/hotline.env` (generate: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Install Asterisk — `hotline/server/asterisk/README.md` has the honest Bookworm
   options (it is NOT in the default repos); we record the route + version taken.
3. Fill the config templates: `__PI_LAN_IP__`, passwords, and `__ATA_IP__` = **your
   PC's LAN IP for now** (MicroSIP is the "ATA"; note `__ATA_IP__` appears in **two**
   places in pjsip.conf).
4. MicroSIP registers as `ata` → SSH-tunnel to the Pi → `/test` page → RING →
   **MicroSIP rings** → answer → two-way audio → recording lands on the Pi.
5. Add the Cloudflare tunnel ingress (`phone.thekartoff.com` → `127.0.0.1:9100`, per
   `deploy/tunnel-ingress.md`) → from a hotspot device: `https://phone.thekartoff.com/test`
   → full ring from **outside the house**. We note the WS-jitter verdict (fallback
   ladder if it stutters: Cloudflare-TURN WebRTC → VPS-for-calls-only).

**Milestone: Phase 1 done, A$0 spent.** From here, hardware arrival makes it real.

---

## Step 3 — Bench day (when ATA + adaptor arrive; ~1–2 h at the desk)

**Before the ATA ever touches your network** — harden it on a direct PC-to-ATA cable
(spec §4.2): change BOTH passwords · HTTPS-only UI · disable GDMS cloud, TR-069, all
auto-provisioning, auto-firmware-upgrade · STUN/NAT helpers off · **hook-flash
detection OFF** · strong SIP password · static/reserved IP.

Then the Phase-0 checklist (spec §14):

1. Wire the desk chain: 802's 605 plug → **adaptor** → **coupler** (pass-through) →
   RJ11 lead → **ATA FXS port 1**. Set AU SLIC impedance + AU double-ring cadence.
2. Softphone direct-IP call to the ATA (registrar-less; MicroSIP can do this) →
   **does the bell ring?** Loudness wheel wound up first (minimum = quiet buzz, never
   silent). Audio-fine-but-no-ring = the mode-3 bell strap fix (screwdriver job,
   documented among AU collectors). Weak ring = ATA ring voltage/frequency settings.
3. Lift the horn: two-way audio; tune ATA RX/TX gains (earpiece volume is fixed on the
   phone). Slam the hook a few times — must NOT false-hangup. Spin the dial mid-call —
   must do nothing.
4. Coupler → iD4 DI (channel 2): check levels, hum, and the two-voice balance (tune
   via ATA gains). Hum → try tap point / pad / isolation before declaring defeat.
5. Hold a mic to the earpiece and record a sweep + some speech — the A/B reference for
   the (optional) earpiece-sim EQ.

**Report back:** bell verdict · gain settings · which transmitter inset it has (carbon
No. 13 vs electronic 20E — sticker or listen) · coupler verdict.

---

## Step 4 — Guided session: zones + the real ATA goes live (~1 h)

With Claude: configure the UDM-Pro per spec §4.1 — SERVICES zone (Pi), PHONE VLAN
(ATA on a tagged desk port; SIP/RTP to the Pi only; internet egress independently
blocked), LAN rules. Then swap MicroSIP → real ATA: same registrar + creds, point the
ATA at the Pi, re-substitute `__ATA_IP__` (both pjsip.conf places) to the ATA's
PHONE-VLAN IP, reload Asterisk. `/test` from the hotspot → **the real 802 rings from
the internet.**

---

## Step 5 — Software that comes next (Claude's queue, no action from you)

- **Plan 2:** Twitch OAuth + EventSub + reward + queue + credits ledger + the real
  `/phone` page (KART-OFF print) + console + overlay.
- **Plan 3:** bleeper daemon on the streaming PC — capture/split the iD4, N=4 delay,
  DUMP button, mic gating, OBS wiring.
- **Phase 4:** Stripe / YouTube.
- **Before first stream (posture roadmap, spec §13):** site/API/bot move off the Pi.
