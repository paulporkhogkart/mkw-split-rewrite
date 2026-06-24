"""Local server for tools/chip-cropper.html.

Serves the SDR captures and the crop tool, and persists the crop spec. Pure helpers
(list_captures / load_crops / save_crops) are unit-tested; the HTTP shell is manual.

Run: python scripts/chip_cropper_server.py [--lang en_uk] [--captures captures_sdr]
     [--crops tools/chips.crops.json] [--port 8777]
Then open http://localhost:8777/
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

CATEGORIES = ("combos", "karts", "courses")


def safe_capture_path(captures_root: str, rel: str) -> Optional[str]:
    """Resolve a /captures/<rel> request to an absolute path inside captures_root,
    or None if it would escape (path traversal guard)."""
    root = os.path.abspath(captures_root)
    target = os.path.abspath(os.path.join(root, *rel.split("/")))
    if target == root or target.startswith(root + os.sep):
        return target
    return None


def list_captures(captures_root: str, lang: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for category in CATEGORIES:
        directory = os.path.join(captures_root, lang, category)
        if not os.path.isdir(directory):
            continue
        for fn in sorted(os.listdir(directory)):
            if fn.lower().endswith(".png"):
                items.append({"category": category, "name": fn[:-4],
                              "url": f"/captures/{lang}/{category}/{fn}"})
    return items


def load_crops(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"meta": {"crop_aspect": 1.0, "chip_px": 96},
            "defaults": {"character": {}, "course": None},
            "combos": {}, "karts": {}, "courses": {}}


def save_crops(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def serve(captures_root: str, lang: str, crops_path: str, html_path: str, port: int = 8777):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(html_path, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if self.path == "/api/captures":
                return self._send(200, json.dumps(list_captures(captures_root, lang)).encode())
            if self.path == "/api/crops":
                return self._send(200, json.dumps(load_crops(crops_path)).encode())
            if self.path.startswith("/captures/"):
                rel = self.path[len("/captures/"):].split("?", 1)[0]
                fpath = safe_capture_path(captures_root, rel)
                if fpath is not None and os.path.isfile(fpath):
                    with open(fpath, "rb") as fh:
                        return self._send(200, fh.read(), "image/png")
                return self._send(404, b"not found", "text/plain")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/crops":
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                save_crops(crops_path, data)
                return self._send(200, b'{"ok":true}')
            return self._send(404, b"not found", "text/plain")

        def log_message(self, *a):   # quiet
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[cropper] http://localhost:{port}/  (lang={lang}, crops={crops_path})")
    httpd.serve_forever()


def main():
    p = argparse.ArgumentParser(description="Local server for the chip crop tool.")
    p.add_argument("--lang", default="en_uk")
    p.add_argument("--captures", default="captures_sdr")
    p.add_argument("--crops", default=os.path.join("tools", "chips.crops.json"))
    p.add_argument("--html", default=os.path.join("tools", "chip-cropper.html"))
    p.add_argument("--port", type=int, default=8777)
    a = p.parse_args()
    serve(a.captures, a.lang, a.crops, a.html, a.port)


if __name__ == "__main__":
    main()
