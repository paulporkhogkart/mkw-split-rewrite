import { describe, it, expect, beforeEach } from 'vitest';
import { SessionTracker, STABLE_MS, type SessionView } from './sessionTracker';

const P = 7;
const frame = (screen: string | null, courseId: number | null = null,
               character: string | null = null, costume: string | null = null) =>
  ({ screen, courseId, character, costume });

let now = 0;
let opens: SessionView[];
let finals: SessionView[];
let drops: number[];
let t: SessionTracker;

beforeEach(() => {
  now = 0; opens = []; finals = []; drops = [];
  t = new SessionTracker({
    now: () => now,
    emitOpen: (s) => opens.push(s),
    emitFinal: (s) => finals.push(s),
    emitDrop: (id) => drops.push(id),
  });
});
const at = (ms: number) => { now = ms; };
const last = <T,>(a: T[]): T => a[a.length - 1];

describe('open at start', () => {
  it('opens once a class has held for STABLE_MS, stamped at its first frame', () => {
    at(0); t.onFrame(P, frame('MAIN_MENU'));
    expect(opens).toHaveLength(0);                 // still pending
    at(STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));
    expect(opens).toHaveLength(1);
    expect(opens[0]).toMatchObject({ cls: 'menus', state: 'open', started_ts: 0, player_id: P });
  });
});

describe('debounce', () => {
  it('swallows a one-frame foreign blip inside a stable session', () => {
    at(0); t.onFrame(P, frame('MAIN_MENU'));
    at(STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));   // menus open
    at(STABLE_MS + 100); t.onFrame(P, frame('CHARACTER_SELECT'));   // blip
    at(STABLE_MS + 200); t.onFrame(P, frame('MAIN_MENU'));          // back
    expect(opens).toHaveLength(1);   // no new session
    expect(finals).toHaveLength(0);
  });
});

describe('genuine transition', () => {
  it('finalises the old (ended at the new key first appearing) and opens the new contiguously', () => {
    at(0); t.onFrame(P, frame('MAIN_MENU'));
    at(STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));   // menus open @0
    at(2000); t.onFrame(P, frame('CHARACTER_SELECT')); // pending char @2000
    at(2000 + STABLE_MS); t.onFrame(P, frame('CHARACTER_SELECT')); // commit
    expect(finals).toHaveLength(1);
    expect(finals[0]).toMatchObject({ cls: 'menus', started_ts: 0, ended_ts: 2000 });
    expect(last(opens)).toMatchObject({ cls: 'character_select', started_ts: 2000 });
  });
});

describe('runs', () => {
  it('opens a racing session on the first attempt and increments on the next', () => {
    at(1000); t.noteRun(P, 5);
    expect(last(opens)).toMatchObject({ cls: 'racing', course_id: 5, attempts: 1, state: 'open' });
    at(2000); t.noteRun(P, 5);
    expect(last(opens)).toMatchObject({ course_id: 5, attempts: 2 });
  });

  it('a different course finalises the old racing session and opens the new', () => {
    at(1000); t.noteRun(P, 5); t.noteRun(P, 5);   // 2 attempts on 5
    at(3000); t.noteRun(P, 6);
    expect(finals).toHaveLength(1);
    expect(finals[0]).toMatchObject({ cls: 'racing', course_id: 5, attempts: 2 });
    expect(last(opens)).toMatchObject({ course_id: 6, attempts: 1 });
  });

  it('records a PB on the open racing session', () => {
    at(1000); t.noteRun(P, 5); t.notePb(P, 5);
    at(2000); t.onOffline(P);
    expect(finals[0]).toMatchObject({ cls: 'racing', course_id: 5, attempts: 1, pbs: 1 });
  });

  it('continues an already-open (presence) racing session instead of reopening', () => {
    at(0); t.onFrame(P, frame('RACING', 5, 'Peach', 'Base'));
    at(STABLE_MS); t.onFrame(P, frame('RACING', 5, 'Peach', 'Base'));   // racing open
    const before = opens.length;
    at(STABLE_MS + 500); t.noteRun(P, 5);
    expect(opens.length).toBe(before + 1);          // an update, not a 2nd session
    expect(last(opens)).toMatchObject({ course_id: 5, attempts: 1, character: 'Peach' });
  });
});

describe('drop rule', () => {
  it('drops a racing session that ends with zero completed attempts', () => {
    at(0); t.onFrame(P, frame('RACING', 5));
    at(STABLE_MS); t.onFrame(P, frame('RACING', 5));   // racing open, attempts 0
    expect(last(opens)).toMatchObject({ cls: 'racing', attempts: 0 });
    const id = last(opens).session_id;
    at(4000); t.onFrame(P, frame('MAIN_MENU'));
    at(4000 + STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));   // transition away
    expect(drops).toContain(id);
    expect(finals.some(f => f.cls === 'racing')).toBe(false);
  });

  it('keeps a short menus session (the debounce floor is enough; no duration drop)', () => {
    at(0); t.onFrame(P, frame('MAIN_MENU'));
    at(STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));   // menus open @0
    at(STABLE_MS + 200); t.onFrame(P, frame('CHARACTER_SELECT'));
    at(STABLE_MS + 200 + STABLE_MS); t.onFrame(P, frame('CHARACTER_SELECT'));   // commit
    expect(finals).toHaveLength(1);
    expect(finals[0].cls).toBe('menus');
    expect(drops).toHaveLength(0);
  });
});

describe('held screens continue racing', () => {
  it('a RACE_MENU / RESET mid-grind does not transition', () => {
    at(0); t.onFrame(P, frame('RACING', 5));
    at(STABLE_MS); t.onFrame(P, frame('RACING', 5)); t.noteRun(P, 5);   // racing open w/ attempt
    const n = opens.length;
    at(STABLE_MS + 100); t.onFrame(P, frame('RACE_MENU'));   // held, course null
    at(STABLE_MS + 200); t.onFrame(P, frame('RESET'));        // held
    at(STABLE_MS + 300); t.onFrame(P, frame('RACING', 5));
    expect(opens.length).toBe(n);          // no new opens
    expect(finals).toHaveLength(0);
  });
});

describe('late course fill', () => {
  it('fills a null course from the first run without reopening', () => {
    at(0); t.onFrame(P, frame('RACING', null));
    at(STABLE_MS); t.onFrame(P, frame('RACING', null));   // racing open, course null
    expect(last(opens)).toMatchObject({ cls: 'racing', course_id: null });
    at(2000); t.noteRun(P, 5);
    expect(last(opens)).toMatchObject({ course_id: 5, attempts: 1 });
    expect(finals).toHaveLength(0);        // same session
  });
});

describe('offline', () => {
  it('finalises the open session', () => {
    at(0); t.onFrame(P, frame('MAIN_MENU'));
    at(STABLE_MS); t.onFrame(P, frame('MAIN_MENU'));
    at(5000); t.onOffline(P);
    expect(finals).toHaveLength(1);
    expect(finals[0]).toMatchObject({ cls: 'menus', started_ts: 0, ended_ts: 5000 });
  });
});

describe('openSessions snapshot', () => {
  it('returns every player current open session', () => {
    at(0); t.onFrame(1, frame('MAIN_MENU'));
    at(STABLE_MS); t.onFrame(1, frame('MAIN_MENU'));
    t.noteRun(2, 9);
    const snap = t.openSessions();
    expect(snap.map(s => s.player_id).sort()).toEqual([1, 2]);
    expect(snap.every(s => s.state === 'open')).toBe(true);
  });
});
