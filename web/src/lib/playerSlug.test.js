import { describe, it, expect } from "vitest";
import { playerSlug } from "./playerSlug.js";
it("mirrors the Pi slugify (lowercase, drop apostrophes, join on non-alnum)", () => {
  expect(playerSlug("Paul")).toBe("paul");
  expect(playerSlug("Paul Pork")).toBe("paul_pork");
  expect(playerSlug("D’Angelo")).toBe("dangelo");
});
