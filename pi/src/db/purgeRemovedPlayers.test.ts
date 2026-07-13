import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { purgeRemovedPlayers } from './purgeRemovedPlayers';

/** Seed a DB with a keeper player (Paul, id 1) and Alex (id 3), Alex having rows in every
 *  player-referencing table, plus Paul rows that must survive. */
function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,auth_token_hash) VALUES (1,'Paul','hashP'),(3,'Alex','hashA')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,3)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // runs: Paul id 10 (keeper), Alex id 20
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,3,1,150,'finished','live',112000,1)");
  db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,0,54000),(10,1,54000),(20,0,56000),(20,1,56000)");
  db.exec("INSERT INTO run_trails(run_id,codec,n,max_t_ms,data) VALUES (10,1,1,1000,X'00'),(20,1,1,1000,X'00')");
  db.exec("INSERT INTO ghost_imports(run_id,player_id,course_id,cc,action) VALUES (20,3,1,150,'new')");
  db.exec("INSERT INTO screen_intervals(season_id,player_id,screen,started_ms,ended_ms) VALUES (1,3,'racing',1,2)");
  db.exec("INSERT INTO activity_events(ts,type,season_id,player_id) VALUES (1,'presence',1,3),(2,'presence',1,1)");
  db.exec("INSERT INTO player_alignment(player_id,dx,dy,scale,sample_count) VALUES (1,0,0,1,1),(3,0,0,1,1)");
  return db;
}

const count = (db: any, sql: string, ...args: any[]) =>
  (db.prepare(sql).get(...args) as { c: number }).c;

describe('purgeRemovedPlayers', () => {
  it('deletes every Alex row across all tables and the players row', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Alex'")).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM runs WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM run_laps WHERE run_id=20')).toBe(0);   // cascade
    expect(count(db, 'SELECT COUNT(*) c FROM run_trails WHERE run_id=20')).toBe(0); // cascade
    expect(count(db, 'SELECT COUNT(*) c FROM ghost_imports WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM screen_intervals WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM activity_events WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM player_alignment WHERE player_id=3')).toBe(0);
    expect(count(db, 'SELECT COUNT(*) c FROM season_rosters WHERE player_id=3')).toBe(0);
  });

  it('leaves the keeper player and their rows untouched', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM runs WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM run_laps WHERE run_id=10')).toBe(2);
    expect(count(db, 'SELECT COUNT(*) c FROM run_trails WHERE run_id=10')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM activity_events WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM season_rosters WHERE player_id=1')).toBe(1);
    expect(count(db, 'SELECT COUNT(*) c FROM player_alignment WHERE player_id=1')).toBe(1);
  });

  it('is idempotent — a second run is a no-op and does not throw', () => {
    const db = seeded();
    purgeRemovedPlayers(db);
    expect(() => purgeRemovedPlayers(db)).not.toThrow();
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
  });

  it('is a no-op on a DB with no Alex', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    expect(() => purgeRemovedPlayers(db)).not.toThrow();
    expect(count(db, "SELECT COUNT(*) c FROM players WHERE display_name='Paul'")).toBe(1);
  });
});
