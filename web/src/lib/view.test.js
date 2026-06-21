import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("defaults to the live card wall", () => {
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("#/unknown")).toBe("live");
    expect(viewFromHash("#/map")).toBe("live"); // old route no longer matches
  });
  it("returns territory for the territory hash", () => {
    expect(viewFromHash("#/territory")).toBe("territory");
    expect(viewFromHash("#territory")).toBe("territory");
  });
  it("returns heat for the unlisted heat hash", () => {
    expect(viewFromHash("#/heat")).toBe("heat");
    expect(viewFromHash("#heat")).toBe("heat");
  });
});
