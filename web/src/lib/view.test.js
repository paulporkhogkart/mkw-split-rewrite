import { describe, it, expect } from "vitest";
import { viewFromPath, playerSlugFromPath, courseSlugFromPath } from "./view.js";

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

describe("players routing", () => {
  it("routes /players and /players/:slug to the players view", () => {
    expect(viewFromPath("/players")).toBe("players");
    expect(viewFromPath("/players/paul")).toBe("players");
  });
  it("extracts the slug (null on the index)", () => {
    expect(playerSlugFromPath("/players")).toBeNull();
    expect(playerSlugFromPath("/players/paul")).toBe("paul");
    expect(playerSlugFromPath("/turf")).toBeNull();
  });
});

describe("tracks routing", () => {
  it("routes /tracks and /tracks/:slug to the courses view", () => {
    expect(viewFromPath("/tracks")).toBe("courses");
    expect(viewFromPath("/tracks/rainbow_road")).toBe("courses");
  });
  it("extracts the course slug (null on the index)", () => {
    expect(courseSlugFromPath("/tracks")).toBeNull();
    expect(courseSlugFromPath("/tracks/rainbow_road")).toBe("rainbow_road");
    expect(courseSlugFromPath("/players/paul")).toBeNull();
  });
});
