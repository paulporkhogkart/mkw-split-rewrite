import { describe, it, expect } from "vitest";
import { directorStep } from "./chipDirector.js";

const e = (o = {}) => ({ screen: "RACING", character: "Mario", costume: "Base",
  kart: "Standard Kart", final_time: null, online: true, ...o });

describe("directorStep", () => {
  it("swap on a select screen -> spawn", () => {
    const r = directorStep(e({ screen: "KART_SELECT" }), e({ screen: "KART_SELECT", kart: "B Dasher" }));
    expect(r).toEqual({ combo: "mario__base__b_dasher", action: "select" });
  });
  it("leaving kart select with a kart -> flourish", () => {
    const r = directorStep(e({ screen: "KART_SELECT" }), e({ screen: "COURSE_SELECT" }));
    expect(r.action).toBe("confirm");
  });
  it("finish -> flourish once", () => {
    expect(directorStep(e(), e({ final_time: "1:50.517" })).action).toBe("confirm");
    expect(directorStep(e({ final_time: "1:50.517" }), e({ final_time: "1:50.517" })).action).toBeNull();
  });
  it("combo change off-select (or first sight) -> idle", () => {
    expect(directorStep(null, e()).action).toBe("idle");
    expect(directorStep(e(), e({ character: "Luigi" })).action).toBe("idle");
  });
  it("steady state -> no action", () => {
    expect(directorStep(e(), e()).action).toBeNull();
  });
  it("no character -> chip hidden", () => {
    expect(directorStep(e(), e({ character: null })).combo).toBeNull();
  });
});
