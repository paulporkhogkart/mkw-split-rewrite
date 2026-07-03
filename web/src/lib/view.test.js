import { describe, it, expect } from "vitest";
import { viewFromPath } from "./view.js";

describe("viewFromPath", () => {
  it("defaults to the live card wall at the root or an unknown path", () => {
    expect(viewFromPath("/")).toBe("live");
    expect(viewFromPath("")).toBe("live");
    expect(viewFromPath("/unknown")).toBe("live");
  });
  it("returns turf for /turf (with or without a trailing slash)", () => {
    expect(viewFromPath("/turf")).toBe("turf");
    expect(viewFromPath("/turf/")).toBe("turf");
  });
  it("returns heat for the unlisted /heat path", () => {
    expect(viewFromPath("/heat")).toBe("heat");
  });
  it("returns version for the unlisted /version path", () => {
    expect(viewFromPath("/version")).toBe("version");
  });
});
