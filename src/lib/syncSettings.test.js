import { describe, it, expect } from "vitest";
import { resolveServerUrl, DEFAULT_SERVER_URL } from "./syncSettings.js";

describe("resolveServerUrl", () => {
  it("absent key (null) → deploy default URL (first run pre-fill)", () => {
    expect(resolveServerUrl(null)).toBe(DEFAULT_SERVER_URL);
    expect(DEFAULT_SERVER_URL).toBe("https://api.thekartoff.com");
  });

  it("deliberately cleared (\"\") → stays blank (uploading disabled)", () => {
    expect(resolveServerUrl("")).toBe("");
  });

  it("stored value → returned verbatim", () => {
    expect(resolveServerUrl("https://example.test")).toBe("https://example.test");
  });
});
