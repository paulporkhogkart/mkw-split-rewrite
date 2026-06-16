import { describe, it, expect } from "vitest";
import { nosignalBadgeLabel } from "./nosignal.js";

describe("nosignalBadgeLabel", () => {
  it("reports the matched brand in auto mode", () => {
    expect(nosignalBadgeLabel({ auto: true, brand: "elgato" })).toBe("Auto · matched Elgato");
    expect(nosignalBadgeLabel({ auto: true, brand: "ugreen" })).toBe("Auto · matched UGREEN");
  });
  it("reports the Elgato default when auto matches nothing", () => {
    expect(nosignalBadgeLabel({ auto: true, brand: null })).toBe("Auto · Elgato default (no card match)");
  });
  it("reports manual when the user hand-edited", () => {
    expect(nosignalBadgeLabel({ auto: false, brand: null })).toBe("Manual (custom)");
  });
});
