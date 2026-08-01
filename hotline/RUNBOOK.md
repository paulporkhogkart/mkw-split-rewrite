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
| 3 | **Telephone line coupler** (the analog stream tap) | Preferred: **used JK Audio Inline Patch**, eBay US — discontinued 2025ish, used units ~US$90–150 + ship, confirm the listing ships to AU · Cheap fallback: **Retell 145** line recording connector (UK, ~£22 — only if an RJ11-plug variant, the stock one is a BT plug; mic-level 3.5mm out is fine, iD4 has gain) · DIY standby: Jaycar MA1510 600:600 telephone transformer + 100 nF X2 cap bridged across the pair, ~A$25 over the counter | ~A$180 landed / ~A$50 / ~A$25 | Must be a **bridged tap**: couples audio through a DC-blocking cap, draws no loop current, never touches hook state. **Do NOT buy a broadcast hybrid** (JK AutoHybrid, Broadcast Host, etc.) — they answer by drawing loop current, so the ATA sees the call as picked up before the 802 rings. Criteria: transformer isolation, **ring-voltage protection**, pass-through (a A$5 RJ11 double adaptor gives any bridged tap pass-through). **Inline Patch PSU** (user-guide spec): **16 VAC 160 mA**, OEM wall-wart is 120 V US-only → replace with Jaycar **MP3021** (16 VAC 1.25 A, bare ends) + soldered barrel plug — AC so no polarity; grab both 2.1 mm and 2.5 mm (5.5 mm OD) plugs ~A$3 ea and fit whichever matches the socket when the unit lands. Unloaded it'll read ~18–20 VAC — normal for unregulated, the Patch's internal regulators expect it. **ARRIVED 2026-08-01 — install + tap bench = Step 3b.** |
| 4 | **DUMP button** (can defer to Phase 3) | Stream Deck key if you own one (A$0), else USB foot switch / big red USB button — eBay "USB foot switch single" | A$0–130 | On-camera prop value is a plus. |
| 5 | Inline attenuator pad (standby) | Near-certain unnecessary with the Inline Patch: its outputs have front-panel level knobs, and the iD4 DI takes +12 dBu max — turn the Patch down, not the signal path. If bench day still surprises: **Hosa ATT-448** (Amazon AU ~A$30) or **Shure A15AS** switchable 15/20/25 dB (~A$90, any AU music store) — both AU-domestic in days, so there's nothing to pre-import. | A$0 — skip | XLR barrel pads; chain would be Patch XLR out → pad → XLR-to-6.35 mm TS lead → iD4 ch2. |

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

**First boot — harden before it can phone home.** Out of the box the ATA DHCPs onto
whatever network it sees and contacts Grandstream's cloud (GDMS/provisioning) with
default admin credentials. You do NOT need an isolated bench cable — a direct
PC-to-ATA link has no DHCP server, and the recovery path (dialling `***` into its
voice menu) is closed to you: the 802 is pulse-dial, no `*` key. At your current
threat level (pre-streaming, CGNAT, trusted LAN) the network is fine. Best order:

1. *(5-min UniFi task, any time before the parcel lands)* Create the **PHONE VLAN**:
   new network on the UDM, **internet access unticked**. This is bench-grade isolation
   (LAN-reachable, zero internet path) *and* the ATA's permanent home — spec §4.2's
   "isolated bench connection" for free.
2. First-boot the ATA on that VLAN (tagged desk port or any port on it), find its IP
   in the UniFi client list, browse to the web UI from your machine.
3. Run the hardening checklist: change BOTH passwords · HTTPS-only UI · disable GDMS
   cloud, TR-069, all auto-provisioning, auto-firmware-upgrade · STUN/NAT helpers off ·
   **hook-flash detection OFF** · strong SIP password · static/reserved IP.

Lazy fallback (also acceptable today): plug it into the normal LAN and run the
checklist immediately — the exposure is a few minutes of outbound-only phone-home
behind CGNAT and a default-credential UI visible only to your own devices. Firmware
updates are manual either way: download from grandstream.com on your PC, upload via
the web UI — the device never needs internet.

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

## Step 3b — Coupler install + tap bench (JK Inline Patch — arrived 2026-08-01)

The coupler landed as a used **JK Audio Inline Patch**
([user guide](https://www.jkaudio.com/downloads/user-guides/JK-Audio-Inline-Patch-User-Guide.pdf) ·
[datasheet](https://www.jkaudio.com/downloads/datasheets/JK-Audio-Inline-Patch-Datasheet.pdf)).
It is an active back-to-back-hybrid tap, not a passive transformer — but on **Norm**
it meets every Step-0 criterion (bridged, no loop current, ring + Caller ID pass
untouched, 1500 VAC isolation) and adds per-voice level knobs the passive options
never had.

### Wiring map (verify before first test)

- **Direction:** ATA FXS1 → RJ11 lead → JK **<Phone Line>** jack · 802's 605 plug →
  610 adaptor → JK **<Phone>** jack. The phone goes on the Phone side; the ATA is
  "the wall".
- **The tap:** back-panel **<Mixed Mono>** 3.5 mm jack → aux lead → 3.5→6.35 mm
  adaptor → iD4 DI (ch2). Both voices, mono; a stereo plug in this jack is explicitly
  allowed (manual p.3). Output is −10 dBu nominal / +12 dBu max, 50 Ω — consumer line
  level, DI gain low, **no pad** (as predicted in Step 0 row 5).
- **The other three minijacks — know them apart:**
  - **Stereo out** = two OUTPUTS, one voice per channel (caller on one, your voice on
    the other) — a stereo lead into the mono DI lands one voice and dumps the other.
    Not our chain (first hookup 2026-08-01 had the aux here — harmless, wrong jack).
  - **Send in** (3.5 mm mono; likewise the XLR-F) = INJECTS audio into the call.
    Unused; keep the **Send Level knob fully down**.
  - **<N.O. Contacts>** (Remote) = a tip-to-ring SHORT SEIZES THE LINE. Never plug
    audio leads here.
- **Off-Hook/Norm switch stays on Norm.** Off-Hook (or a Remote-jack short) seizes
  the line: the ATA sees off-hook → page shows `off the hook`, claims 409, and after
  ~a minute the ATA's howler plays into the tap. There is no "patch on" button to
  remember — on Norm the outputs are simply live whenever a call is up.
- PSU spec **16 VAC 160 mA** (the soldered MP3021 rig). The manual's hum FAQ blames
  substitute supplies first — if hum ever appears, suspect the wart; also the
  odd-but-official tip: turning **From Line UP** reduces line-resistance hum.
- XLR out exists (caller-mostly, −4 dBu nom) but is not our chain; if ever used
  unbalanced, do NOT tie pin 3 to pin 1 — the active differential output halves
  (manual FAQ 1).

### Bench checklist (completes Step 3 items 4–5)

1. **Regression** — the JK now sits in the ring/hook/audio path, so re-run the
   F-items with it inline on Norm: page claimable · lift → `off the hook` · cradle →
   claimable · web call → bell rings → answer → two-way audio → clean hangup ·
   hook-slam + dial-spin immune. (Bell weaker than before? Nudge ATA Ring Power up
   a notch from 55.)
2. **Separation tune** (manual p.6, pulse-dial variant — an 802 has no touch tone to
   hold): From Phone fully down, From Line fully up, live call with the far end
   muted, hum a steady note into the handset, set **<Separation>** at the quietest
   point on the iD4 meter. Then From Line ≈ 12:00, From Phone up to taste.
3. **Balance + levels:** the mixed channel is the whole on-air conversation during
   calls — spec §2 req 5 + §7.4: the SM58 crossfades DOWN on CALL_ACTIVE ("the
   whole show goes down the line — phone-quality everything is the bit"), so your
   line voice IS your broadcast voice. Balance a natural two-way: caller a touch
   forward, you fully intelligible (From Line = caller, From Phone = you). The JK's
   ~20 dB separation is a mixing nicety, not moderation — DUMP nukes the whole
   mixed channel by design (§7.2: "exactly one channel to nuke", deliberately
   simpler than rev 3's two-channel sidetone story; per-leg isolation, if ever
   truly wanted, is the §7.6 digital input stage — not an analog split, not a new
   interface). **ATA Rx/Tx gains stay 0 dB** — they move the handset AND the tap;
   the JK knobs move only the tap.
4. Make test calls from a **hotspot device, not the monitoring PC** (browser call +
   iD4 monitoring on one machine = feedback and confusion).
5. **Hum/noise pass** at silence. Escalation ladder unchanged: PSU → From Line up →
   pad → tap point → isolation → spec §7.6 digital input stage.
6. **Earpiece A/B reference** (spec §7.5 optional-garnish EQ): SM58 held to the
   earpiece — sweep + speech, saved next to the same speech off the tap.
7. **Report back:** coupler verdict · final knob positions · transmitter inset
   (No. 13 vs 20E — still unreported).

### OBS, until Plan 3

The iD4 arrives in Windows as one stereo device: **left = SM58, right = phone**. A
plain OBS capture of it is fine for private tests (expect a ring thump pre-answer;
the phone-channel gate is Plan 3), but the show path remains "bleeper daemon splits /
delays / DUMPs; OBS captures the daemon's virtual cables" (spec §7.2) — don't build
scenes on direct iD4 capture.

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

---

## Off-hook detection (SNMP hook poll)

The page shows `off the hook` and refuses calls while the handset is lifted.
Driven by an SNMPv2c poll of the ATA every 2 s. Feature is DORMANT until the
env keys below are set.

### One-time: enable SNMP on the ATA (V2 admin UI)

System Settings → scroll to the **SNMP Settings** block (just after TR-069):

1. Enable SNMP: **Yes** · SNMP Version: **Version 2c** · SNMP Port: **161**
2. SNMPv1/v2c Community: paste a long random string (this is a read password;
   generate with `openssl rand -hex 16`)
3. Leave ALL trap fields empty (Trap IP Address blank = no traps)
4. Apply / reboot if prompted

### One-time: find the hook OID (the gating experiment)

From the Pi (`ssh pi@192.168.4.21`):

    sudo apt install snmp
    # handset CRADLED:
    snmpwalk -v2c -c <community> -On 192.168.3.226 > /tmp/onhook.txt
    # lift the handset (no call), then:
    snmpwalk -v2c -c <community> -On 192.168.3.226 > /tmp/offhook.txt
    diff /tmp/onhook.txt /tmp/offhook.txt

The OID that flips is the signal. Note its off-hook value(s). Re-check it flips
during a live call and after the far side hangs up while the handset stays up.

RESULT 2026-07-22 (experiment done, config live on the Pi): FXS1 hook state =
`.1.3.6.1.4.1.42397.1.2.2.1.1.0.0`, values `On Hook` / `Off Hook` (FXS2 is
`...1.2.2.1.2.0.0`). Walk audited: no secrets in the MIB (SIP username visible,
password not), v2c stands. Note: SNMP only starts answering after an ATA
REBOOT, not just Apply. Native tone plan while off the hook: dial tone, then a
loud off-hook howler for a while, then permanent silence — accepted (detection
unaffected; silence option = auto-dial into an Answer()+Wait() dialplan line
if it ever bugs Paul). T1-T3 physical tests passed same day.
AUDIT the walk output for anything secret-looking (SIP passwords, server
addresses) before keeping files; delete both files after extracting the OID.
If nothing hook-shaped flips: the SNMP approach is dead on this firmware, use
the spec's §1.8 auto-dial fallback instead.

### Enable on the Pi

Append to `/etc/hotline/hotline.env` (values from the experiment):

    HOTLINE_SNMP_HOST=192.168.3.226
    HOTLINE_SNMP_COMMUNITY=<community>
    HOTLINE_SNMP_HOOK_OID=<oid from the diff, numeric form>
    HOTLINE_SNMP_OFFHOOK_VALUES=<value(s) meaning off-hook, comma-separated>

then `sudo systemctl restart hotline`.

### Physical test matrix

T1 lift idle → page `off the hook` within ~3 s · T2 cradle → `idle` ·
T3 web call rings → lift → answers normally, two-way audio · T4 lift during
the claim/ring race → caller fails fast, page recovers to `off the hook` ·
T5 off-hook then ATA power-yank → `phone unplugged` · T6 leave off-hook
30 min → state holds.

### Bench/debug

`POST /admin/line-sim?state=offhook|clear&token=<admin>` drives the state by
hand (works in echo mode; in real mode the poller re-asserts truth on its
next tick).

---

## Closing the line

To stop calls, yank the ATA's **power cord** (not the 605 phone cord). The page at
`phone.thekartoff.com/` shows "phone unplugged" within ~30 seconds. Plugging the power
back in reopens the line automatically. Note: `/test` remains token-gated for benching
only; production calls ring on the subdomain root.
