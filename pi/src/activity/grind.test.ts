import { describe, it, expect } from 'vitest';
import { GrindTracker } from './grind';

describe('GrindTracker', () => {
  it('counts same-course attempts and closes on PB', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000); g.note(1, 5, 2000); g.note(1, 5, 3000); // 3 attempts
    expect(g.close(1, 3000)).toEqual({ count: 3, durationMs: 2000, courseId: 5 });
    expect(g.close(1, 4000)).toBeNull(); // nothing open after close
  });

  it('closes the prior segment when the course changes', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000); g.note(1, 5, 2000);
    const closed = g.note(1, 6, 5000); // moved to course 6
    expect(closed).toEqual({ count: 2, durationMs: 1000, courseId: 5 });
  });

  it('returns null on first note for a player', () => {
    const g = new GrindTracker();
    expect(g.note(1, 5, 1000)).toBeNull();
  });

  it('tracks multiple players independently', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000); g.note(1, 5, 2000); // player 1: 2 attempts on course 5
    g.note(2, 7, 1000); g.note(2, 7, 3000); // player 2: 2 attempts on course 7
    expect(g.close(1, 2000)).toEqual({ count: 2, durationMs: 1000, courseId: 5 });
    expect(g.close(2, 3000)).toEqual({ count: 2, durationMs: 2000, courseId: 7 });
  });

  it('new segment starts after a course-change close', () => {
    const g = new GrindTracker();
    g.note(1, 5, 1000);
    g.note(1, 6, 2000); // closes course 5, starts course 6
    g.note(1, 6, 3000); // second attempt on course 6
    const closed = g.close(1, 4000);
    expect(closed).toEqual({ count: 2, durationMs: 2000, courseId: 6 });
  });
});
