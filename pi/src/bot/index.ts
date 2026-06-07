import { openDb } from '../db/connect';
import { loadConfig } from './config';
import { Announcer } from './client';
import { startEventStream } from './ws';
import { dispatch } from './dispatch';
import { installCommands } from './commands/install';

const cfg = loadConfig();
const db = openDb(cfg.dbPath);                 // shared with the server (WAL); reads only
const announcer = new Announcer(cfg.token, cfg.channelId);

installCommands(announcer.client, db, { guildId: cfg.guildId });
announcer.start().catch((err) => { console.error('[bot] login failed', err); process.exit(1); });
const stream = startEventStream(cfg.wsUrl, (ev) => dispatch(db, ev, (embed) => { void announcer.send(embed); }));

const shutdown = () => { stream.close(); process.exit(0); };
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
console.log(`[bot] started; ws=${cfg.wsUrl} db=${cfg.dbPath}`);
