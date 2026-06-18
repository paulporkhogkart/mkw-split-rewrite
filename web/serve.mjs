// Dependency-free static server for the built website (web/dist). Serves files, with
// an SPA fallback to index.html for extension-less paths. No deps so the systemd unit
// just runs `node serve.mjs`. PORT defaults to 8788. Helpers are exported for tests;
// the server only listens when this file is run directly.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
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

export function createStaticServer(distDir) {
  const indexHtml = join(distDir, "index.html");
  return createServer(async (req, res) => {
    try {
      let file = resolveFile(req.url, distDir);
      const exists = await stat(file).then((s) => s.isFile()).catch(() => false);
      if (!exists) {
        if (extname(file)) { res.writeHead(404); res.end("not found"); return; }
        file = indexHtml;   // extension-less path -> SPA shell
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
