import { describe, it, expect } from "vitest";
import { playerKey } from "./playerKey.js";

describe("playerKey", () => {
  it("lowercases and takes the first name token, so a rename still resolves the asset", () => {
    expect(playerKey("Paul")).toBe("paul");
    expect(playerKey("paul pork")).toBe("paul");   // the rename still maps to the "paul" assets
    expect(playerKey("Gub")).toBe("gub");
    expect(playerKey("Aliias")).toBe("aliias");
  });
  it("is safe on empty / nullish names", () => {
    expect(playerKey("")).toBe("");
    expect(playerKey(null)).toBe("");
    expect(playerKey(undefined)).toBe("");
  });
});
