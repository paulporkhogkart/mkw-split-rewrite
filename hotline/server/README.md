# hotline/server — the Pork Phone Pi service

Spec: docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md

Run tests:   cd hotline/server && python -m pytest
Run app:     cd hotline/server && python -m hotline
Audio contract: 16-bit LE mono 8 kHz PCM, 20 ms frames (320 bytes).
WS /ws/audio: binary messages = one 320-byte frame; text messages = JSON control.
WS /ws/events?feed=rt|delayed&token=... : JSON events.
Env: HOTLINE_ENV, HOTLINE_HTTP_PORT, HOTLINE_AUDIOSOCKET_PORT, HOTLINE_ADMIN_TOKEN,
     HOTLINE_DATA_DIR, HOTLINE_DELAY_N, HOTLINE_ARI_URL, HOTLINE_ARI_USER,
     HOTLINE_ARI_PASSWORD, HOTLINE_ECHO (dev echo mode).
