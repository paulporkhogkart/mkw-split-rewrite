import { describe, it, expect } from "vitest";
import { slug, comboKey } from "./chipKey.js";

describe("slug", () => {
  it("lowercases, underscores spaces, strips punctuation", () => {
    expect(slug("Baby Daisy")).toBe("baby_daisy");
    expect(slug("Bowser Jr.")).toBe("bowser_jr");
    expect(slug("B Dasher")).toBe("b_dasher");
    expect(slug("Chargin' Chuck")).toBe("chargin_chuck");
  });
  it("hyphens fold to underscores (pack truth: para_biddybud)", () => {
    expect(slug("Para-Biddybud")).toBe("para_biddybud");
  });
  it("null-safe", () => {
    expect(slug(null)).toBeNull();
    expect(slug("")).toBeNull();
  });
});

describe("comboKey", () => {
  it("kart combo", () =>
    expect(comboKey({ character: "Baby Daisy", costume: "Base", kart: "B Dasher" }))
      .toBe("baby_daisy__base__b_dasher"));
  it("costume folds in, leading position handled by slugs not display order", () =>
    expect(comboKey({ character: "Toad", costume: "Burger Bud", kart: "Mach Rocket" }))
      .toBe("toad__burger_bud__mach_rocket"));
  it("char-only while no kart picked", () =>
    expect(comboKey({ character: "Luigi", costume: null, kart: null })).toBe("luigi__base"));
  it("no character -> null", () =>
    expect(comboKey({ character: null, costume: null, kart: "B Dasher" })).toBeNull());
  it("null argument returns null", () =>
    expect(comboKey(null)).toBeNull());
});
