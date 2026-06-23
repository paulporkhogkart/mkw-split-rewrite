import type { EmbedBuilder } from 'discord.js';
import { openDb } from '../db/connect';
import { loadConfig } from './config';
import { Announcer } from './client';
import { startEventStream } from './ws';
import { dispatch } from './dispatch';
import { installCommands } from './commands/install';
import { loadState, saveState } from './state';
import { announceMissedPbs } from './catchup';
import { reportService } from '../version/serviceStatus';
import { repoVersion } from '../version/repoVersion';

const cfg = loadConfig();
const db = openDb(cfg.dbPath);                 // shared with the server (WAL); reads only, except the one boot-time version self-report below
// Self-report our deployed version so /v1/version can show the bot's running build (a separate
// process the server can't otherwise see). This is the bot's one allowed write to the shared DB.
try { reportService(db, 'bot', repoVersion(), Date.now()); }
catch (err) { console.error('[bot] version self-report failed', err); }
const announcer = new Announcer(cfg.token, cfg.channelId);
const send = (embed: EmbedBuilder) => { void announcer.send(embed); };

// Bot-owned announce watermark (NOT in the server DB) - the bot keeps track of how far it
// has announced, so a missed PB is caught up from the shared DB. The server stays unaware.
const statePath = process.env.BOT_STATE ?? 'bot-state.json';
const state = loadState(statePath, db);
const catchUp = () => {
  try { announceMissedPbs(db, { send, state, persist: (s) => saveState(statePath, s) }); }
  catch (err) { console.error('[bot] catch-up failed', err); }
};

installCommands(announcer.client, db, { guildId: cfg.guildId });
announcer.start().catch((err) => { console.error('[bot] login failed', err); process.exit(1); });

catchUp();                                       // 1) on startup (embeds queue until logged in)
const stream = startEventStream(
  cfg.wsUrl,
  (ev) => { if (ev.type === 'pb_achieved') catchUp(); else dispatch(db, ev, send); },  // live PB -> nudge
  catchUp,                                        // 2) on every (re)connect
);

const shutdown = () => { stream.close(); process.exit(0); };
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
console.log(`[bot] started; ws=${cfg.wsUrl} db=${cfg.dbPath} state=${statePath}`);
