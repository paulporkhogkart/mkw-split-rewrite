import { openDb, applySchema } from '../db/connect';
import { setPlayerColor } from '../db/players';

// Curate a player's ghost-trail colour server-side (served via /v1/roster → every client
// picks it up, no app rebuild). Usage: npm run set-color <player-display-name> <#rrggbb>
const [name, color] = process.argv.slice(2);
if (!name || !color) { console.error('usage: set-color <player-display-name> <#rrggbb>'); process.exit(1); }
const db = openDb(process.env.MKW_DB ?? 'mkw.db');
applySchema(db);
setPlayerColor(db, name, color);
console.log(`Set ${name}'s trail colour to ${color}.`);
