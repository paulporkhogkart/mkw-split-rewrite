# MKW Discord bot

Announces personal bests and world records to Discord, driven by the server. A separate
process from the server, in the same `pi/` package. (Re-creation of the legacy
`legacy/mkwpb2/kart-off/services/discord_bot.py`, but server-driven instead of
Google-Sheets/scraping-driven.)

## What it does (Stage 1)

- Connects to the server's `/v1/events` WebSocket and reacts to:
  - `pb_achieved` → the green **PB** embed (title `<NAME> PERSONAL BEST` / `NEW TRACK RECORD` /
    `THE <DUR> REIGN OF <NAME> IS OVER|CONTINUES`), with TRACK/TIME/DELTA/OVERTOOK/POSITION and a
    "still ahead" footer.
  - `wr_update` → the grey **WR** embed (`WORLD RECORD BY <HOLDER>`), with TRACK/TIME/DELTA and a
    reign footer.
- The lean events are enriched into the legacy embed's rich fields (overtaken / positions /
  still-ahead / reign) by reading the shared `mkw.db` directly (reusing `pi/src/db/`). WAL makes
  concurrent reads alongside the server's writes safe. The bot only reads; it never writes.

Slash commands (`/leaderboard`, `/nemesis`, `/wr`) are **Stage 2** (a separate plan).

## Run

```bash
cd pi
cp .env.example .env      # fill in DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID
node --env-file=.env --no-warnings --import tsx src/bot/index.ts
# or, with the env exported some other way:
npm run bot
```

Run the server (`npm run dev`) in its own process; the bot connects to it over the WebSocket and
reads the same DB file.

## Configuration

Env (see `pi/.env.example`):

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `DISCORD_BOT_TOKEN` | yes | – | bot token (Discord developer portal) |
| `DISCORD_CHANNEL_ID` | yes | – | channel the embeds post to |
| `DISCORD_GUILD_ID` | no | – | guild for instant slash-command sync (Stage 2) |
| `MKW_DB` | no | `mkw.db` | path to the shared server DB |
| `PORT` | no | `8787` | server port (builds the default `BOT_WS_URL`) |
| `BOT_WS_URL` | no | `ws://127.0.0.1:${PORT}/v1/events` | override the events URL |

Non-secret player config (Discord-id → name map for `/nemesis`, and the per-player thumbnail
GIFs) lives in `src/bot/players.config.ts`. Unknown players degrade gracefully (no thumbnail) —
they never crash a post.

## Layout

```
config.ts          env -> BotConfig
players.config.ts  ID_TO_NAME + THUMBNAIL_GIFS (+ defensive gifFor/nameForId)
types.ts           embed-data types
format.ts          time-diff / duration / overtaken / positions formatters (legacy-faithful)
enrich.ts          event + DB -> embed data
embeds/pb.ts       green PB EmbedBuilder
embeds/wr.ts       grey WR EmbedBuilder
ws.ts              reconnecting WebSocket client (Node global WebSocket)
dispatch.ts        event -> embed -> send
client.ts          discord.js Announcer (ready-buffer + channel send)
index.ts           entry: wires it together
```
Reign queries live in `pi/src/db/reign.ts`. Embed output is locked by snapshot-style tests
(`embeds/*.test.ts`) so the look stays faithful to the legacy bot.

## Operating on the Pi

Run as a second systemd unit alongside the server unit, same working directory so `MKW_DB`
resolves to the one shared file.
