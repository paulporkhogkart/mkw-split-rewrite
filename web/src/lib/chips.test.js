import { describe, it, expect } from "vitest";
import { slugify, chipsFor, chipUrl } from "./chips.js";

describe("slugify (parity with pi/src/db/slug.ts)", () => {
  it("drops apostrophes and underscores non-alnum runs", () => {
    expect(slugify("Bowser's Castle")).toBe("bowsers_castle");
    expect(slugify("Koopa Troopa")).toBe("koopa_troopa");
    expect(slugify("Mario Bros. Circuit")).toBe("mario_bros_circuit");
    expect(slugify("DK Pass")).toBe("dk_pass");
  });
});

describe("chipUrl", () => {
  it("builds a public chips path", () => {
    expect(chipUrl("courses", "dk_pass")).toBe("/chips/courses/dk_pass.png");
  });
});

describe("chipsFor", () => {
  const pbRow = (payload, courseSlug = "dk_pass") => ({
    kind: "event",
    event: { type: "pb", course: { slug: courseSlug, name: "DK Pass" }, payload },
  });

  it("pb: course, kart, character (combo with base costume)", () => {
    const chips = chipsFor(pbRow({ character: "Koopa Troopa", kart: "Baby Blooper", costume: null }));
    expect(chips.map(c => c.src)).toEqual([
      "/chips/courses/dk_pass.png",
      "/chips/karts/baby_blooper.png",
      "/chips/combos/koopa_troopa__base.png",
    ]);
    expect(chips[2].fallback).toBe("/chips/combos/koopa_troopa__base.png");
  });

  it("pb with a costume: combo uses the costume, fallback is __base", () => {
    const chips = chipsFor(pbRow({ character: "Peach", kart: "Hot Rod", costume: "Aero" }));
    const combo = chips.find(c => c.src.includes("combos"));
    expect(combo.src).toBe("/chips/combos/peach__aero.png");
    expect(combo.fallback).toBe("/chips/combos/peach__base.png");
  });

  it("racing session: course + character, no kart", () => {
    const chips = chipsFor({ kind: "session", cls: "racing",
      course: { slug: "crown_city", name: "Crown City" }, character: "Mario", costume: null });
    expect(chips.map(c => c.src)).toEqual([
      "/chips/courses/crown_city.png",
      "/chips/combos/mario__base.png",
    ]);
  });

  it("turf/wr: course only; presence/off-track: none", () => {
    expect(chipsFor({ kind: "event", event: { type: "wr",
      course: { slug: "dk_pass", name: "DK Pass" }, payload: {} } }).map(c => c.src))
      .toEqual(["/chips/courses/dk_pass.png"]);
    expect(chipsFor({ kind: "event", event: { type: "presence", course: null, payload: {} } })).toEqual([]);
    expect(chipsFor({ kind: "session", cls: "menus", course: null })).toEqual([]);
  });
});
