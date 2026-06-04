import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';

const name = process.argv[2];
if (!name) { console.error('usage: mint-token <player-display-name>'); process.exit(1); }
const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
const token = mintToken(db, name);
console.log(`Token for ${name} (store it now — not shown again):\n${token}`);
