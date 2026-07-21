# Asterisk config for the Pork Phone (Pi)

Install: Asterisk is NOT in Raspberry Pi OS / Debian Bookworm's default repos
(pulled during the bookworm freeze). Pick at deploy time, in order of preference:
  1. `sudo apt install asterisk` — works only if a repo provides it
     (check first: `apt-cache policy asterisk`).
  2. Debian sid/unstable pin or a maintained third-party repo (verify signing).
  3. Build from source (asterisk.org LTS tarball, ./configure && make menuselect
     — enable res_ari, res_audiosocket, chan_audiosocket, app_audiosocket
     (res_=core, chan_=externalMedia driver, app_=the dialplan AudioSocket()
     app the fallback route uses) — && make && make install). A source build
     installs no systemd unit: run `make config` to add one, or the
     `systemctl restart asterisk` below has nothing to restart.
Record which route was taken + `asterisk -rx "core show version"` below at Task 15.

## Task 15 verification record (2026-07-21)

- **OS reality check:** the Pi runs Debian 13 (trixie), not Bookworm — asterisk has
  no apt candidate there either. **Route 3 (source build) taken.**
- **Version:** `Asterisk 22.10.1` (asterisk-22-current.tar.gz, LTS), built on the
  Pi 3B itself: `install_prereq install`, `./configure --with-pjproject-bundled
  --with-jansson-bundled`, menuselect-enabled res/chan/app_audiosocket, `make -j2`
  (~30 min), `make install`. NO `make samples` — minimal explicit confs instead:
  the four templates plus asterisk.conf (runuser/rungroup asterisk), modules.conf
  (autoload), logger.conf. Runs as system user `asterisk` via a hand-written
  systemd unit; hotline.service already carried After/Wants=asterisk.service.
- **`module show like audiosocket`:** all three (res/chan/app) Running.
- **externalMedia audiosocket verdict: WORKS.** 22.10.1 accepts
  `encapsulation=audiosocket`; test-ring end-to-end with two-way audio and
  per-leg recordings — the dialplan fallback below was NOT needed.
- Registration: HT802V2 registered as `ata` (qualify RTT ~4 ms). Note the V2
  needed Account Active ON + "Use Random SIP Port" unticked (bench-day fix).

Copy each `*.tmpl` into /etc/asterisk/ (merge, don't clobber existing dialplan
if any), substituting __ATA_IP__, __PI_LAN_IP__, __SIP_PASSWORD__,
__ARI_PASSWORD__, __AUDIOSOCKET_PORT__ (default 9101). Then
`sudo systemctl restart asterisk`.

Note: HOTLINE_ARI_PASSWORD in /etc/hotline/hotline.env MUST equal
__ARI_PASSWORD__ in ari.conf — two files, one secret.

Verify at install (Task 15):
1. `asterisk -rx "core show version"` — record it.
2. `asterisk -rx "module show like audiosocket"` — res_audiosocket +
   chan_audiosocket loaded.
3. ARI externalMedia audiosocket support: try the app's test-ring; if
   originate/externalMedia rejects `encapsulation=audiosocket`, fall back to
   the dialplan route in extensions.conf.tmpl (app originates to
   PJSIP/ata with context=pork, extension 100 — set PORK_UUID channel var,
   already passed by AriClient.originate_phone).
4. AudioSocket frame kinds against res_audiosocket docs (0x00/0x01/0x10).

Note: the AudioSocket UUID (externalMedia data= / ${PORK_UUID}) must be the canonical dashed form — res_audiosocket parses it with libuuid's uuid_parse, which rejects 32-char dash-less hex.

MicroSIP stand-in for the ATA (Phase 1, no hardware): register MicroSIP on
Paul's PC as endpoint `ata` (username ata, the SIP password, server = Pi LAN
IP). To Asterisk it IS the ATA. Swap to the HT802V2 later by pointing the
real ATA at the same registrar with the same creds — zero config drift.
