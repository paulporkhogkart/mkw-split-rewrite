import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { join } from "node:path";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolveFile, contentType, createStaticServer } from "./serve.mjs";

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

// Exercise the real request handler over HTTP against a temp dist dir, so the
// SPA-fallback / 404 / cache-control behavior is regression-guarded (the pure
// helpers above don't cover it).
describe("createStaticServer (http)", () => {
  let dir, server, base;
  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), "thekartoff-web-"));
    await mkdir(join(dir, "assets"), { recursive: true });
    await writeFile(join(dir, "index.html"), "<!doctype html><title>shell</title>");
    await writeFile(join(dir, "assets", "app.js"), "export const x = 1;");
    server = createStaticServer(dir);
    await new Promise((res) => server.listen(0, res));
    base = `http://127.0.0.1:${server.address().port}`;
  });
  afterAll(async () => {
    await new Promise((res) => server.close(res));
    await rm(dir, { recursive: true, force: true });
  });

  it("serves a real asset with its content-type and a cache header", async () => {
    const r = await fetch(`${base}/assets/app.js`);
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/javascript; charset=utf-8");
    expect(r.headers.get("cache-control")).toContain("max-age");
    expect(await r.text()).toContain("export const x");
  });

  it("falls back to index.html (no-cache) for an extension-less SPA route", async () => {
    const r = await fetch(`${base}/some/client/route`);
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(r.headers.get("cache-control")).toBe("no-cache");
    expect(await r.text()).toContain("shell");
  });

  it("404s a missing asset that has an extension", async () => {
    const r = await fetch(`${base}/assets/missing.js`);
    expect(r.status).toBe(404);
  });

  it("falls back to index.html for a trailing-slash client route", async () => {
    const r = await fetch(`${base}/turf/`);
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(await r.text()).toContain("shell");
  });

  it("serves index.html at /", async () => {
    const r = await fetch(`${base}/`);
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/html; charset=utf-8");
  });
});
