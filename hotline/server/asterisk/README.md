# Asterisk config for the Pork Phone (Pi)

Install: `sudo apt install asterisk` (Asterisk 20 on Bookworm). Copy each
`*.tmpl` into /etc/asterisk/ (merge, don't clobber existing dialplan if any),
substituting __ATA_IP__, __PI_LAN_IP__, __SIP_PASSWORD__, __ARI_PASSWORD__,
__AUDIOSOCKET_PORT__ (default 9101). Then `sudo systemctl restart asterisk`.

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
