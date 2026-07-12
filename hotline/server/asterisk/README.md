# Asterisk config for the Pork Phone (Pi)

Install: Asterisk is NOT in Raspberry Pi OS / Debian Bookworm's default repos
(pulled during the bookworm freeze). Pick at deploy time, in order of preference:
  1. `sudo apt install asterisk` — works only if a repo provides it
     (check first: `apt-cache policy asterisk`).
  2. Debian sid/unstable pin or a maintained third-party repo (verify signing).
  3. Build from source (asterisk.org LTS tarball, ./configure && make menuselect
     — enable res_ari, res_audiosocket, chan_audiosocket — && make && make install).
Record which route was taken + `asterisk -rx "core show version"` below at Task 15.

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

MicroSIP stand-in for the ATA (Phase 1, no hardware): register MicroSIP on
Paul's PC as endpoint `ata` (username ata, the SIP password, server = Pi LAN
IP). To Asterisk it IS the ATA. Swap to the HT802V2 later by pointing the
real ATA at the same registrar with the same creds — zero config drift.
