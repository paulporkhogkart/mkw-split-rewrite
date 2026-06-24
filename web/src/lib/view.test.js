import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("defaults to the live card wall", () => {
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("#/unknown")).toBe("live");
    expect(viewFromHash("#/map")).toBe("live"); // old route no longer matches
  });
  it("returns turf for the turf hash (territory is an old-hash alias)", () => {
    expect(viewFromHash("#/turf")).toBe("turf");
    expect(viewFromHash("#turf")).toBe("turf");
    expect(viewFromHash("#/territory")).toBe("turf");   // old bookmarks still resolve
  });
  it("returns heat for the unlisted heat hash", () => {
    expect(viewFromHash("#/heat")).toBe("heat");
    expect(viewFromHash("#heat")).toBe("heat");
  });
  it("returns version for the unlisted version hash", () => {
    expect(viewFromHash("#/version")).toBe("version");
    expect(viewFromHash("#version")).toBe("version");
  });
});
