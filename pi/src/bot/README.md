# MKW Discord bot

Announces personal bests and world records to Discord, driven by the server. A separate
process from the server, in the same `pi/` package. (Re-creation of the legacy
`legacy/mkwpb2/kart-off/services/discord_bot.py`, but server-driven instead of
Google-Sheets/scraping-driven.)

## Announcements (Stage 1)

- Connects to the server's `/v1/events` WebSocket and reacts to:
  - `pb_achieved` → the green **PB** embed (title `<NAME> PERSONAL BEST` / `NEW TRACK RECORD` /
    `THE <DUR> REIGN OF <NAME> IS OVER|CONTINUES`), with TRACK/TIME/DELTA/OVERTOOK/POSITION and a
    "still ahead" footer.
  - `wr_update` → the grey **WR** embed (`WORLD RECORD BY <HOLDER>`), with TRACK/TIME/DELTA and a
    reign footer.
- The lean events are enriched into the legacy embed's rich fields (overtaken / positions /
  still-ahead / reign) by reading the shared `mkw.db` directly (reusing `pi/src/db/`). WAL makes
  concurrent reads alongside the server's writes safe. The bot only reads; it never writes.

## Slash commands (Stage 2)

Registered on startup (guild-scoped — instant — when `DISCORD_GUILD_ID` is set, else global). All
read the shared `mkw.db`; the `track`/`player` options have **autocomplete**.

- **`/leaderboard [track]`** — a track board (WR line + ranked PBs with chained gaps) when a track
  is given, else the **overall** board (aggregate time + golf-score points). Footer: `BEHOLD THE
  <DUR> REIGN OF <LEADER>` (+ the leader's GIF when known).
- **`/wr <track>`** — the current world record for a track (`<holder>'s <track>`, TIME/CHAR/KART,
  `<DUR> REIGN` footer), then posts the WR video (current, else the most-recent prior WR that has one).
- **`/nemesis [player]`** — the tracks where you're furthest behind the leader, or behind a chosen
  player; paginated 5/track with ◀/▶ buttons (5-min timeout, restricted to you). Requires your
  Discord id in `players.config.ts`'s `ID_TO_NAME`.

Embed builders are pure + snapshot-tested (`embeds/commands.test.ts`); the discord.js interaction
glue lives in `commands/install.ts`. Pure data-assembly is in `commands/views.ts` (tested), reads in
`db/leaderboards.ts` + `db/lookups.ts` + `db/reign.ts`.

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
config.ts            env -> BotConfig
players.config.ts    ID_TO_NAME + THUMBNAIL_GIFS (+ defensive gifFor/nameForId)
types.ts             embed-data types
format.ts            time-diff / duration / overtaken / positions / leaderboard / nemesis
                       formatters + shared alignDiffColumn (legacy-faithful)
enrich.ts            announcement event + DB -> embed data
embeds/pb.ts         green PB EmbedBuilder
embeds/wr.ts         grey WR EmbedBuilder
embeds/commands.ts   blue command EmbedBuilders (leaderboard / wr / nemesis)
ws.ts                reconnecting WebSocket client (Node global WebSocket)
dispatch.ts          announcement event -> embed -> send
client.ts            discord.js Announcer (ready-buffer + channel send; exposes .client)
commands/views.ts    pure command data-assembly (DB -> view objects)
commands/defs.ts     SlashCommandBuilder defs + filterChoices (autocomplete)
commands/install.ts  command registration + interactionCreate routing + nemesis pagination
index.ts             entry: wires it together
```
Reads live in `pi/src/db/`: `reads.ts`, `leaderboards.ts` (overall standings + golf points + WR
aggregate + nemesis), `lookups.ts` (autocomplete lists), `reign.ts` (WR / track / course-leader /
overall reign). Embed output is locked by snapshot-style tests (`embeds/*.test.ts`) so the look
stays faithful to the legacy bot.

## Operating on the Pi

Run as a second systemd unit alongside the server unit, same working directory so `MKW_DB`
resolves to the one shared file.
