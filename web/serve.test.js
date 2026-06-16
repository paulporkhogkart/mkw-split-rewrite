import { describe, it, expect } from "vitest";
import { join } from "node:path";
import { resolveFile, contentType } from "./serve.mjs";

const DIST = join(process.cwd(), "dist");

describe("contentType", () => {
  it("maps known extensions and defaults to octet-stream", () => {
    expect(contentType("/x/app.js")).toBe("text/javascript; charset=utf-8");
    expect(contentType("/x/index.html")).toBe("text/html; charset=utf-8");
    expect(contentType("/x/logo.svg")).toBe("image/svg+xml");
    expect(contentType("/x/blob.bin")).toBe("application/octet-stream");
  });
});

describe("resolveFile", () => {
  it("maps '/' to index.html under dist", () => {
    expect(resolveFile("/", DIST)).toBe(join(DIST, "index.html"));
  });
  it("maps an asset path under dist", () => {
    expect(resolveFile("/assets/app.js", DIST)).toBe(join(DIST, "assets", "app.js"));
  });
  it("strips a query string", () => {
    expect(resolveFile("/assets/app.js?v=2", DIST)).toBe(join(DIST, "assets", "app.js"));
  });
  it("never escapes dist via traversal", () => {
    const r = resolveFile("/../../etc/passwd", DIST);
    expect(r.startsWith(DIST)).toBe(true);
  });
});
