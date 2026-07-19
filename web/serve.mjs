// Dependency-free static server for the built website (web/dist). Serves files, with
// an SPA fallback to index.html for extension-less paths. No deps so the systemd unit
// just runs `node serve.mjs`. PORT defaults to 8788. Helpers are exported for tests;
// the server only listens when this file is run directly.
import { createServer } from "node:http";
import { readFile, stat, readlink } from "node:fs/promises";
import { join, normalize, extname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
  ".map": "application/json", ".txt": "text/plain; charset=utf-8",
};

export function contentType(p) { return TYPES[extname(p).toLowerCase()] || "application/octet-stream"; }

/** Map a request path to an absolute file under `distDir`, guarding against traversal.
 *  A trailing slash maps to index.html; leading `..`/slashes are stripped so the result
 *  always stays within distDir (a non-existent file is the caller's 404/SPA decision). */
export function resolveFile(urlPath, distDir) {
  let p = decodeURIComponent((urlPath || "/").split("?")[0]);
  if (p.endsWith("/")) p += "index.html";
  const rel = normalize(p).replace(/^(\.\.[/\\])+/, "").replace(/^[/\\]+/, "");
  return join(distDir, rel);
}

const CHIPS_PREFIX = "/chips/anim/";

/** Resolve the current chips tag: `current` may be a symlink to the tag dir or a
 *  text file containing the tag name (Windows dev uses the text-file form). Returns
 *  null when unset/invalid. */
export async function currentChipsTag(chipsDir) {
  const p = join(chipsDir, "current");
  try { return (await readlink(p)).replace(/[/\\]+$/, "").split(/[/\\]/).pop(); }
  catch { /* not a symlink */ }
  try { return (await readFile(p, "utf8")).trim() || null; }
  catch { return null; }
}

export function createStaticServer(distDir, opts = {}) {
  const chipsDir = opts.chipsDir ?? process.env.MKW_CHIPS_DIR;
  const lockFile = opts.lockFile ?? fileURLToPath(new URL("./chips.lock", import.meta.url));
  const indexHtml = join(distDir, "index.html");
  return createServer(async (req, res) => {
    try {
      const rawPath = decodeURIComponent((req.url || "/").split("?")[0]);
      if (rawPath.startsWith(CHIPS_PREFIX)) {
        const rest = rawPath.slice(CHIPS_PREFIX.length);
        // The lock pins the full-pack download (pbenguin): serve the checkout's committed
        // chips.lock so the pack a client downloads always matches the manifest this Pi serves.
        if (rest === "lock") {
          const body = await readFile(lockFile).catch(() => null);
          if (!body) { res.writeHead(404); res.end("not found"); return; }
          res.writeHead(200, { "content-type": TYPES[".txt"], "cache-control": "public, max-age=300" });
          res.end(body);
          return;
        }
        if (!chipsDir) { res.writeHead(404); res.end("not found"); return; }
        if (rest === "manifest.json") {
          const tag = await currentChipsTag(chipsDir);
          const file = tag && resolveFile(`/${tag}/chips/manifest.json`, chipsDir);
          const body = file && await readFile(file).catch(() => null);
          if (!body) { res.writeHead(404); res.end("not found"); return; }
          const j = JSON.parse(body);
          j.base = `${CHIPS_PREFIX}${tag}/`;
          res.writeHead(200, {
            "content-type": TYPES[".json"],
            "cache-control": "public, max-age=300",
            "x-chips-tag": tag,
          });
          res.end(JSON.stringify(j));
          return;
        }
        // <tag>/<file...> — tag is user-supplied (it's in the URL), so guard it against
        // traversal the same way resolveFile guards everything else; reject before it
        // ever reaches `join(chipsDir, tag, "chips")`.
        const [tag, ...restParts] = rest.split("/");
        if (!tag || tag.includes("..") || tag.includes("\\")) { res.writeHead(404); res.end("not found"); return; }
        const file = resolveFile(`/${restParts.join("/")}`, join(chipsDir, tag, "chips"));
        const ok = await stat(file).then((s) => s.isFile()).catch(() => false);
        if (!ok) { res.writeHead(404); res.end("not found"); return; }
        res.writeHead(200, {
          "content-type": contentType(file),
          "cache-control": "public, max-age=31536000, immutable",
        });
        res.end(await readFile(file));
        return;
      }
      let file = resolveFile(req.url, distDir);
      const exists = await stat(file).then((s) => s.isFile()).catch(() => false);
      if (!exists) {
        // A concrete sub-resource extension (.js/.png/...) 404s; document-ish paths fall
        // through to the SPA shell — both extension-less client routes (/turf) and a missing
        // .html like a trailing-slash route (/turf/ -> /turf/index.html).
        const ext = extname(file).toLowerCase();
        if (ext && ext !== ".html") { res.writeHead(404); res.end("not found"); return; }
        file = indexHtml;   // client route -> SPA shell
      }
      const body = await readFile(file);
      res.writeHead(200, {
        "content-type": contentType(file),
        "cache-control": file === indexHtml ? "no-cache" : "public, max-age=3600",
      });
      res.end(body);
    } catch {
      res.writeHead(500); res.end("server error");
    }
  });
}

// Listen only when run directly (not when imported by tests).
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const distDir = fileURLToPath(new URL("./dist", import.meta.url));
  const port = Number(process.env.PORT ?? 8788);
  createStaticServer(distDir).listen(port, () => console.log(`[web] serving ${distDir} on http://127.0.0.1:${port}`));
}
