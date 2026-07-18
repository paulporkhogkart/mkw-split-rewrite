import { describe, it, expect } from "vitest";
import { bgPos, frameRect, sheetCss, createChipPlayer } from "./chipSheet.js";

const ENTRY = {
  kart: true, idle_resume: 51,
  anims: {
    spawn: { frames: 13, cols: 4, rows: 4 },
    idle: { frames: 60, cols: 8, rows: 8 },
    flourish: { frames: 31, cols: 6, rows: 6 },
  },
};
const CHAR = { kart: false, idle_resume: 0, anims: { idle: { frames: 40, cols: 7, rows: 6 }, flourish: { frames: 27, cols: 6, rows: 5 } } };
const FPS = 30;

const player = (entry = ENTRY) => {
  let t = 0;
  const p = createChipPlayer({ entry, fps: FPS, fw: 205, fh: 216, now: () => t });
  return { p, at: (ms) => { t = ms; return p.tick(t); } };
};

describe("frameRect", () => {
  it("maps frame index to row-major source rects", () => {
    expect(frameRect(0, 8, 205, 216)).toEqual({ sx: 0, sy: 0 });
    expect(frameRect(3, 8, 205, 216)).toEqual({ sx: 615, sy: 0 });
    expect(frameRect(8, 8, 205, 216)).toEqual({ sx: 0, sy: 216 });
  });
});

describe("bgPos / sheetCss", () => {
  it("maps frame index to row-major grid offsets", () => {
    expect(bgPos(0, 8, 205, 216)).toBe("0px 0px");
    expect(bgPos(3, 8, 205, 216)).toBe("-615px 0px");
    expect(bgPos(8, 8, 205, 216)).toBe("0px -216px");
  });
  it("sizes the element and background to the grid", () => {
    expect(sheetCss(ENTRY, "idle", 205, 216)).toEqual({
      width: "205px", height: "216px", backgroundSize: "1640px 1728px",
    });
  });
});

describe("createChipPlayer", () => {
  it("starts looping idle and wraps", () => {
    const { p, at } = player();
    expect(at(0)).toMatchObject({ anim: "idle", frame: 0 });
    expect(at(1000 / FPS * 59)).toMatchObject({ frame: 59 });
    expect(at(1000 / FPS * 60)).toMatchObject({ frame: 0 }); // wrap
  });

  it("select() plays spawn once then hands to idle frame 0", () => {
    const { p, at } = player();
    p.select();
    expect(at(0)).toMatchObject({ anim: "spawn", frame: 0 });
    at(1000 / FPS * 12);                                   // last spawn frame
    expect(at(1000 / FPS * 13)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("select() is interruptible - re-select restarts spawn", () => {
    const { p, at } = player();
    p.select(); at(1000 / FPS * 6);
    p.select();
    expect(at(1000 / FPS * 6)).toMatchObject({ anim: "spawn", frame: 0 });
  });

  it("confirm() plays flourish once then enters idle at idle_resume (kart)", () => {
    const { p, at } = player();
    p.confirm();
    expect(at(0)).toMatchObject({ anim: "flourish", frame: 0 });
    expect(at(1000 / FPS * 31)).toMatchObject({ anim: "idle", frame: 51 });
  });

  it("confirm() on a char hard-cuts to idle frame 0", () => {
    const { p, at } = player(CHAR);
    p.confirm();
    expect(at(1000 / FPS * 27)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("select() on a char (no spawn) restarts idle", () => {
    const { p, at } = player(CHAR);
    at(1000 / FPS * 10);
    p.select();
    expect(at(1000 / FPS * 10)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("bg matches the current frame's grid cell", () => {
    const { p, at } = player();
    const s = at(1000 / FPS * 9); // idle frame 9, cols 8 -> col 1 row 1
    expect(s.bg).toBe(bgPos(9, 8, 205, 216));
  });
});
