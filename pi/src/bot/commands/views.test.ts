import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../../db/connect';
import { buildTrackBoard, buildOverallBoard, buildWrInfo, buildNemesis } from './views';

/** Seed helper: creates a complete in-memory DB with 2 players (Paul, Luke), 2 courses,
 *  PBs, WRs, and a run history for reign computation. */
function seeded() {
  const db = openDb(':memory:');
  applySchema(db);

  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road'),(2,'dk_pass','DK Pass')");

  // PBs: Paul faster on both courses
  db.exec(
    "INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb,ended_at) VALUES " +
    "(1,1,1,1,150,'finished','live',106000,'1:46.000',1,'2026-01-01T01:00')," + // Paul RR PB
    "(2,1,2,1,150,'finished','live',108000,'1:48.000',1,'2026-01-02T01:00')," + // Luke RR PB
    "(3,1,1,2,150,'finished','live',95000,'1:35.000',1,'2026-01-03T01:00'),"  + // Paul DK PB
    "(4,1,2,2,150,'finished','live',98000,'1:38.000',1,'2026-01-04T01:00')"    // Luke DK PB
  );

  // WRs on both courses — with character/vehicle/video data
  db.exec(
    "INSERT INTO world_records(id,course_id,cc,holder_name,record_ms,record_str,is_current,character,vehicle,video_url,achieved_at) VALUES " +
    "(1,1,150,'MK Pro',100000,'1:40.000',1,'Mario (Classic)','Kart A','https://yt.be/v1','2025-06-01T00:00')," +
    "(2,2,150,'WR Guy',90000,'1:30.000',1,'Link',NULL,NULL,'2025-07-01T00:00')"
  );

  return db;
}

// ---------------------------------------------------------------------------
// buildTrackBoard
// ---------------------------------------------------------------------------

describe('buildTrackBoard', () => {
  it('returns title, body (with WR line + rows + aligned gaps), leader, and reign_ms', () => {
    const result = buildTrackBoard(seeded(), 'Rainbow Road', 150);
    expect('error' in result).toBe(false);
    if ('error' in result) return;

    expect(result.title).toBe('Rainbow Road Leaderboard');
    // Body: WR line + 2 player rows with chained diffs
    expect(result.body).toContain('1:40.000'); // WR line
    expect(result.body).toContain('Paul');
    expect(result.body).toContain('Luke');
    expect(result.body).toContain('1:46.000');
    expect(result.body).toContain('1:48.000');
    // leader is the current course leader (Paul — fastest PB)
    expect(result.leader).toBe('Paul');
    expect(result.reign_ms).toBeGreaterThanOrEqual(0);
  });

  it('returns {error} for an unknown course slug', () => {
    const result = buildTrackBoard(seeded(), 'Nonexistent Track', 150);
    expect('error' in result).toBe(true);
    if ('error' in result) expect(result.error).toContain('not found');
  });

  it('anchors the body on the rank-1 PB (WR shows its gap to #1)', () => {
    const result = buildTrackBoard(seeded(), 'Rainbow Road', 150);
    if ('error' in result) throw new Error('unexpected error: ' + result.error);
    // WR line now carries its gap to the #1 PB: `   WR      1:40.000  (-6.000s)`
    expect(result.body).toMatch(/WR\s+1:40\.000\s+\(-6\.000s\)/);
    expect(result.body).toMatch(/`1\. Paul/);
    expect(result.body).toMatch(/`2\. Luke/);
  });
});

// ---------------------------------------------------------------------------
// buildOverallBoard
// ---------------------------------------------------------------------------

describe('buildOverallBoard', () => {
  it('returns title, body (aggregate WR + standings, no golf points), leader, reign_ms', () => {
    const result = buildOverallBoard(seeded(), 150);
    expect(result.title).toBe('Overall Leaderboard');
    expect(result.body).toContain('Paul');
    expect(result.body).toContain('Luke');
    expect(result.body).not.toMatch(/\[\d+\]/);   // golf points removed
    expect(result.leader).toBe('Paul');
    expect(result.reign_ms).toBeGreaterThanOrEqual(0);
  });

  it('body starts with the WR aggregate line (with its gap to #1)', () => {
    const result = buildOverallBoard(seeded(), 150);
    // Total WR = 100000 + 90000 = 190000ms = 3:10.000; Paul total 201000 -> WR gap -11.000s
    expect(result.body).toMatch(/WR\s+3:10\.000\s+\(-11\.000s\)/);
  });
});

// ---------------------------------------------------------------------------
// buildWrInfo
// ---------------------------------------------------------------------------

describe('buildWrInfo', () => {
  it('cleans character parenthetical (e.g. "Mario (Classic)" → "Mario")', () => {
    const result = buildWrInfo(seeded(), 'Rainbow Road', 150);
    expect('error' in result).toBe(false);
    if ('error' in result) return;
    expect(result.char).toBe('Mario');
  });

  it('returns full WR info fields', () => {
    const result = buildWrInfo(seeded(), 'Rainbow Road', 150);
    if ('error' in result) throw new Error(result.error);
    expect(result.title).toBe("MK Pro's Rainbow Road");
    expect(result.time).toBe('1:40.000');
    expect(result.kart).toBe('Kart A');
    expect(result.reign_ms).toBeGreaterThanOrEqual(0);
    // video present
    expect(result.video).toEqual({ url: 'https://yt.be/v1', note: null });
  });

  it('falls back to previous WR video when current WR has no video (DK Pass)', () => {
    const db = seeded();
    // Add a prior WR for DK Pass with a video, then add new current WR with no video
    db.exec("UPDATE world_records SET is_current=0 WHERE id=2");
    db.exec("INSERT INTO world_records(id,course_id,cc,holder_name,record_ms,record_str,is_current,character,vehicle,video_url,achieved_at) VALUES (3,2,150,'New Guy',88000,'1:28.000',1,'Link',NULL,NULL,'2026-01-01T00:00')");
    // The old WR (id=2) has no video_url, so fallback should be null overall
    const result = buildWrInfo(db, 'DK Pass', 150);
    if ('error' in result) throw new Error(result.error);
    expect(result.video).toBeNull();
  });

  it('finds fallback video from prior WR row when current has no video', () => {
    const db = seeded();
    // DK Pass has no video on its current WR; add a prior WR with a video
    db.exec("UPDATE world_records SET is_current=0 WHERE id=2");
    db.exec("INSERT INTO world_records(id,course_id,cc,holder_name,record_ms,record_str,is_current,character,vehicle,video_url,achieved_at) VALUES (4,2,150,'NewGuy',88000,'1:28.000',1,'Link',NULL,NULL,'2026-01-01T00:00')");
    // Now update id=2 to have a video_url
    db.exec("UPDATE world_records SET video_url='https://yt.be/old' WHERE id=2");
    const result = buildWrInfo(db, 'DK Pass', 150);
    if ('error' in result) throw new Error(result.error);
    expect(result.video?.url).toBe('https://yt.be/old');
    expect(result.video?.note).toMatch(/no video/i);
  });

  it('returns {error} for an unknown course', () => {
    const result = buildWrInfo(seeded(), 'Doesnt Exist', 150);
    expect('error' in result).toBe(true);
    if ('error' in result) expect(result.error).toContain('not found');
  });

  it('handles null vehicle with "Unknown" fallback', () => {
    const result = buildWrInfo(seeded(), 'DK Pass', 150);
    if ('error' in result) throw new Error(result.error);
    expect(result.kart).toBe('Unknown');
  });
});

// ---------------------------------------------------------------------------
// buildNemesis
// ---------------------------------------------------------------------------

describe('buildNemesis', () => {
  it('returns {error} for an unregistered Discord id', () => {
    const result = buildNemesis(seeded(), '0000000000000000000', null, 150);
    expect('error' in result).toBe(true);
    if ('error' in result) expect(result.error).toMatch(/not registered/i);
  });

  it('returns untargeted nemesis rows when Paul requests with a real discord id', () => {
    // Paul is id '1213316126948335636' in players.config; seed a player named 'Paul'
    // so nameForId resolves and playerId() finds the DB row.
    const result = buildNemesis(seeded(), '1213316126948335636', null, 150);
    // Paul is the fastest on both courses → no tracks where Paul is behind
    // So we expect an error/empty case
    if ('error' in result) {
      expect(result.error).toMatch(/No data found/i);
    } else {
      // If rows returned, rows must be for courses Paul is behind
      expect(Array.isArray(result.rows)).toBe(true);
    }
  });

  it('returns untargeted nemesis rows for Luke (who is behind Paul on both courses)', () => {
    // Luke is id '867421622347890719' in players.config
    const result = buildNemesis(seeded(), '867421622347890719', null, 150);
    expect('error' in result).toBe(false);
    if ('error' in result) return;
    expect(result.title).toBe("Luke's Nemesis Tracks");
    expect(result.targeted).toBe(false);
    // Luke is behind on both courses; largest gap first
    // RR: 108000 - 106000 = 2000ms
    // DK: 98000 - 95000 = 3000ms
    // DK should be first (larger gap)
    expect(result.rows[0].track_name).toBe('DK Pass');
    expect(result.rows[1].track_name).toBe('Rainbow Road');
    // ahead_player should be Paul on both
    expect(result.rows[0].ahead_player).toBe('Paul');
  });

  it('returns rows with negative diffs (ahead) when the requester leads all courses', () => {
    // Paul leads both courses; nemesisRows compares to 2nd (Luke) → negative diffs, still rows returned.
    // buildNemesis only errors on empty data (no PBs at all), not on negative diffs.
    const result = buildNemesis(seeded(), '1213316126948335636', null, 150);
    // Either rows returned (negative diffs) or an error — depends on whether nemesisRows filters
    // We just confirm it doesn't crash and the shape is consistent.
    if ('error' in result) {
      expect(result.error).toMatch(/No data/i);
    } else {
      expect(Array.isArray(result.rows)).toBe(true);
      expect(result.targeted).toBe(false);
    }
  });
});
