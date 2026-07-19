import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { join } from "node:path";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
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

async function withServer(distDir, chipsDir, fn) {
  const srv = createStaticServer(distDir, { chipsDir });
  await new Promise((r) => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  try { return await fn(base); } finally { srv.close(); }
}

function chipsFixture() {
  const root = mkdtempSync(join(tmpdir(), "chips-"));
  mkdirSync(join(root, "chips-v1", "chips"), { recursive: true });
  writeFileSync(join(root, "chips-v1", "chips", "manifest.json"),
    JSON.stringify({ version: 1, combos: {} }));
  writeFileSync(join(root, "chips-v1", "chips", "a__idle.webp"), "RIFFfake");
  writeFileSync(join(root, "current"), "chips-v1");  // text-file form of `current`
  return root;
}

describe("/chips/anim/", () => {
  const dist = mkdtempSync(join(tmpdir(), "dist-"));
  writeFileSync(join(dist, "index.html"), "<html>spa</html>");

  it("serves the current manifest with short cache and injected base", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/manifest.json`);
      expect(r.status).toBe(200);
      expect(r.headers.get("cache-control")).toContain("max-age=300");
      expect(r.headers.get("x-chips-tag")).toBe("chips-v1");
      const j = await r.json();
      expect(j.base).toBe("/chips/anim/chips-v1/");
    });
  });

  it("serves tagged assets immutable", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/a__idle.webp`);
      expect(r.status).toBe(200);
      expect(r.headers.get("content-type")).toBe("image/webp");
      expect(r.headers.get("cache-control")).toContain("immutable");
    });
  });

  it("404s missing chip files without SPA fallback", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/nope.webp`);
      expect(r.status).toBe(404);
      const r2 = await fetch(`${base}/chips/anim/manifest.json`, { method: "GET" });
      expect(r2.status).toBe(200);
    });
  });

  it("without chipsDir the prefix 404s (extension) as before", async () => {
    await withServer(dist, undefined, async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/a__idle.webp`);
      expect(r.status).toBe(404);
    });
  });
});

describe("chips lock route", () => {
  let dir, server, base;
  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), "thekartoff-lock-"));
    await writeFile(join(dir, "index.html"), "<!doctype html>");
    await writeFile(join(dir, "the.lock"),
      "tag chips-v1\nbase https://example.com/dl\nabc123  chips-mario.tar\n");
    server = createStaticServer(dir, { lockFile: join(dir, "the.lock") });
    await new Promise((res) => server.listen(0, res));
    base = `http://127.0.0.1:${server.address().port}`;
  });
  afterAll(async () => {
    await new Promise((res) => server.close(res));
    await rm(dir, { recursive: true, force: true });
  });

  it("serves the lock as text with a short max-age, without needing chipsDir", async () => {
    const r = await fetch(`${base}/chips/anim/lock`);
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toContain("text/plain");
    expect(r.headers.get("cache-control")).toBe("public, max-age=300");
    expect(await r.text()).toContain("tag chips-v1");
  });

  it("404s when the lock file is missing", async () => {
    const s2 = createStaticServer(dir, { lockFile: join(dir, "nope.lock") });
    await new Promise((res) => s2.listen(0, res));
    const r = await fetch(`http://127.0.0.1:${s2.address().port}/chips/anim/lock`);
    expect(r.status).toBe(404);
    await new Promise((res) => s2.close(res));
  });
});
