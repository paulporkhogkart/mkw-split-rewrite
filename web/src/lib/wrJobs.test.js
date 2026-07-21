import { describe, it, expect } from "vitest";
import { STATUS_META, splitRows, summary, detailOf, relTime, parseUtc } from "./wrJobs.js";

const job = (over = {}) => ({
  wr_id: 1, course: "Mario Circuit", course_slug: "mario_circuit", cc: 150,
  holder_name: "JaK", record_str: "1:02.934", is_current: 1,
  status: "queued", attempts: 0, last_error: null, updated_at: null,
  lease_owner: null, next_eligible_at: null, trail_points: null, ...over,
});

describe("STATUS_META", () => {
  it("covers every server status", () => {
    for (const s of ["done", "in_progress", "parked", "unprocessable", "not_queued", "cooldown", "queued"])
      expect(STATUS_META[s], s).toBeTruthy();
  });
});

describe("splitRows", () => {
  it("splits current from superseded and sorts problem states to the top", () => {
    const rows = [
      job({ wr_id: 1, status: "done" }),
      job({ wr_id: 2, status: "queued" }),
      job({ wr_id: 3, status: "parked" }),
      job({ wr_id: 4, status: "in_progress" }),
      job({ wr_id: 5, status: "cooldown" }),
      job({ wr_id: 6, is_current: 0, status: "done" }),
    ];
    const { current, superseded } = splitRows(rows);
    expect(superseded.map((j) => j.wr_id)).toEqual([6]);
    // problems (parked, cooldown) first, then in_progress, queued, done
    expect(current.map((j) => j.wr_id)).toEqual([3, 5, 4, 2, 1]);
  });

  it("keeps the server's course order within a status band (stable sort)", () => {
    const rows = [
      job({ wr_id: 1, course: "Acorn Heights", status: "queued" }),
      job({ wr_id: 2, course: "Mario Circuit", status: "queued" }),
    ];
    expect(splitRows(rows).current.map((j) => j.wr_id)).toEqual([1, 2]);
  });
});

describe("summary", () => {
  it("counts done/queued/stuck and current-WR trail coverage", () => {
    const rows = [
      job({ wr_id: 1, status: "done" }),
      job({ wr_id: 2, status: "queued" }),
      job({ wr_id: 3, status: "in_progress" }),
      job({ wr_id: 4, status: "cooldown" }),
      job({ wr_id: 5, status: "parked" }),
      job({ wr_id: 6, status: "unprocessable" }),
      job({ wr_id: 7, is_current: 0, status: "done" }),
    ];
    expect(summary(rows)).toEqual({ done: 2, queued: 2, stuck: 3, coverage: "1/6" });
  });
});

describe("relTime / parseUtc", () => {
  const now = Date.parse("2026-07-21T12:00:00Z");
  it("parses SQLite UTC datetimes", () => {
    expect(parseUtc("2026-07-21 11:57:00").toISOString()).toBe("2026-07-21T11:57:00.000Z");
    expect(parseUtc(null)).toBeNull();
  });
  it("renders past and future times relative to now", () => {
    expect(relTime("2026-07-21 11:57:00", now)).toBe("3 m ago");
    expect(relTime("2026-07-21 12:42:00", now)).toBe("in 42 m");
    expect(relTime("2026-07-20 12:00:00", now)).toBe("1 d ago");
    expect(relTime("2026-07-01 12:00:00", now)).toBe("20 d ago");
    expect(relTime(null, now)).toBe("—");
  });
});

describe("detailOf", () => {
  const now = Date.parse("2026-07-21T12:00:00Z");
  it("shows point count when done", () => {
    expect(detailOf(job({ status: "done", trail_points: 1732 }), now)).toBe("1732 pts");
  });
  it("shows worker and attempt when in progress", () => {
    expect(detailOf(job({ status: "in_progress", lease_owner: "machine-a", attempts: 2 }), now))
      .toBe("machine-a · attempt 2");
  });
  it("shows next retry and error when cooling down", () => {
    expect(detailOf(job({ status: "cooldown", next_eligible_at: "2026-07-21 12:42:00",
      last_error: "engine_failed boom" }), now)).toBe("retry in 42 m — engine_failed boom");
  });
  it("explains unprocessable", () => {
    expect(detailOf(job({ status: "unprocessable" }), now)).toBe("no video or unresolved character");
  });
  it("shows the last error on a queued row, truncated to 80 chars", () => {
    const long = "x".repeat(100);
    const d = detailOf(job({ status: "queued", last_error: long }), now);
    expect(d).toHaveLength(80);
    expect(d.endsWith("…")).toBe(true);
  });
  it("is empty for a clean queued row", () => {
    expect(detailOf(job(), now)).toBe("");
  });
});
