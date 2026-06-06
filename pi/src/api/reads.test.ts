import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { mintToken } from '../db/players';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1)");
  return createApp(db, new EventHub());
}

describe('public reads (no token)', () => {
  it('GET /v1/leaderboard returns rows', async () => {
    const res = await appWith().request('/v1/leaderboard?course=Rainbow%20Road&cc=150');
    expect(res.status).toBe(200);
    expect((await res.json())[0].display_name).toBe('Paul');
  });
  it('GET /v1/seasons works', async () => {
    const res = await appWith().request('/v1/seasons');
    expect(res.status).toBe(200);
    expect((await res.json()).length).toBe(1);
  });
});

describe('GET /v1/me/pbs (token)', () => {
  it('401s without a token, returns the caller\'s PBs with one', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');

    expect((await app.request('/v1/me/pbs')).status).toBe(401);

    const res = await app.request('/v1/me/pbs', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});

function trailsDb() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,2,1,150,'finished','live',112000,1)");
  db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,1,36000),(10,2,72000),(10,3,108000)");
  db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,100,200,0.9),(20,0,300,400,0.8)");
  return db;
}

describe('GET /v1/me/pb-splits (token)', () => {
  it('401s without a token; returns total + splits with one; 400 on unknown course', async () => {
    const db = trailsDb();
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    expect((await app.request('/v1/me/pb-splits?course=Rainbow%20Road')).status).toBe(401);
    const res = await app.request('/v1/me/pb-splits?course=Rainbow%20Road', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ total_ms: 108000, splits: { 1: 36000, 2: 72000, 3: 108000 } });
    expect((await app.request('/v1/me/pb-splits?course=nope', { headers: { authorization: `Bearer ${token}` } })).status).toBe(400);
  });
});

describe('GET /v1/trails (optional token)', () => {
  it('returns roster trails; is_me false anon, true for the owner token; 400 on unknown course', async () => {
    const db = trailsDb();
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    const anon = await app.request('/v1/trails?course=Rainbow%20Road');
    expect(anon.status).toBe(200);
    const anonBody = await anon.json();
    expect(anonBody.map((t: any) => t.player)).toEqual(['Paul', 'Luke']);
    expect(anonBody.every((t: any) => t.is_me === false)).toBe(true);
    const mine = await app.request('/v1/trails?course=Rainbow%20Road', { headers: { authorization: `Bearer ${token}` } });
    const mineBody = await mine.json();
    expect(mineBody.find((t: any) => t.player === 'Paul').is_me).toBe(true);
    expect(mineBody.find((t: any) => t.player === 'Luke').is_me).toBe(false);
    expect((await app.request('/v1/trails?course=nope')).status).toBe(400);
  });
});

describe('GET /v1/roster', () => {
  it('lists the season roster; is_me flags the token holder', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
    db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');
    const anon = await (await app.request('/v1/roster')).json();
    expect(anon.map((r: any) => [r.display_name, r.is_me])).toEqual([['Luke', false], ['Paul', false]]);
    const mine = await (await app.request('/v1/roster', { headers: { authorization: `Bearer ${token}` } })).json();
    expect(mine.find((r: any) => r.display_name === 'Paul').is_me).toBe(true);
  });
});

describe('GET /v1/players/:id/trails', () => {
  it('returns the player trails by mode; 400 on unknown course', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,started_at,ended_at,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,'a','a1',1),(20,1,1,1,150,'finished','live',110000,'b','b1',0)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,1,1,0.9),(20,0,2,2,0.9)");
    const app = createApp(db, new EventHub());
    const last = await (await app.request('/v1/players/1/trails?course=Rainbow%20Road&mode=last&n=5')).json();
    expect(last.map((r: any) => r.run_id)).toEqual([20, 10]);
    const pbs = await (await app.request('/v1/players/1/trails?course=Rainbow%20Road&mode=pbs')).json();
    expect(pbs.map((r: any) => r.run_id)).toEqual([10]);
    expect((await app.request('/v1/players/1/trails?course=nope&mode=pbs')).status).toBe(400);
  });
});
