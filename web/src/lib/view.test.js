import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("defaults to the live card wall", () => {
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("#/unknown")).toBe("live");
  });
  it("returns map for the map hash", () => {
    expect(viewFromHash("#/map")).toBe("map");
    expect(viewFromHash("#map")).toBe("map");
  });
});
