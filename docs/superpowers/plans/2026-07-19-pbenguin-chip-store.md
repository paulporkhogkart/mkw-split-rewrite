# pbenguin Chip Store (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rust-side chip cache with a `chips://` protocol handler (cache-or-fetch), a resumable
opt-in full-pack downloader, a settings "Chips" tab, and the Pi `/chips/anim/lock` route.

**Architecture:** New `src-tauri/src/chips/` module (store/net/pack/commands). The webview loads
`http://chips.localhost/manifest.json` + sheet URLs; the handler serves from
`<app-data>/chips/<tag>/chips/`, fetching misses one file at a time from thekartoff.com. The
full-pack downloader fills the same directory from the GitHub release pinned by the Pi-served
lock. Spec: `docs/superpowers/specs/2026-07-19-pbenguin-chip-cache-and-cards-design.md`.

**Tech Stack:** Rust (tauri v2, reqwest blocking, rusqlite via `wr::state`, sha2, tar, fs2,
dev-dep tiny_http), Svelte 4, Node test via vitest (web/).

## Global Constraints

- Engine (Python) untouched — all networking in Rust, UI in Svelte (spec "Shape").
- Contracts are FIXED: site manifest `https://thekartoff.com/chips/anim/manifest.json`
  (`base` field = `/chips/anim/<tag>/`); lock format = `tag` line, `base` line, then
  `sha256␣␣filename` lines (see `web/chips.lock`); shards `chips-<char>.tar` +
  `chips-manifest.json`; tags immutable, `chips-v[0-9]+` only.
- Eviction: on adopting a new current tag, delete ALL other tag dirs (partial AND complete) —
  no storage double-up (Paul 2026-07-19). Exception: never delete the tag an active pack
  download is working.
- Full pack ≈ 6.23 GB; label in UI: "Download full pack (6.3 GB)".
- `PBENGUIN_CHIPS_URL` env overrides the site base (default `https://thekartoff.com/chips/anim`)
  for rehearsal.
- Settings flags live in the existing `wr_service.db` via `wr::state::{get_flag,set_flag}`;
  keys are snake_case: `chips_pack_wanted`, `chips_pack_paused`.
- **Concurrent Velopack work is live in the main checkout** (`updater.rs`, `bridge.rs`,
  `App.svelte` update strip). Work in a fresh worktree branch off `main`
  (superpowers:using-git-worktrees); edits to `lib.rs`/`App.svelte`/`SettingsModal.svelte` must
  be purely additive; never touch `updater.rs`/`bridge.rs`.
- Rust tests: `cd src-tauri && cargo test` · web tests: `cd web && npx vitest run serve.test.js`.
- Windows product: custom scheme surfaces as `http://chips.localhost/…`; app-data dir =
  `app.path().app_data_dir()` (`%APPDATA%\mkw-tracker`).
- **STANDING RULE: NO `[dev-dependencies]` in `src-tauri/Cargo.toml`** (carried from the WR
  service + Velopack projects). Wherever a test block in this plan shows `tempfile::tempdir()`
  or `tiny_http`, substitute the std-only helpers `chips::testutil::TmpDir` /
  `chips::testutil::TestServer` defined in Task 2 — same behaviour, no new deps.
- **STANDING RULE: no em dashes in user-facing copy** (settings labels/notes use `·` or
  plain text).
- Subagents stage ONLY the files their task names (never `git add -A`); the main checkout
  has concurrent Velopack work, but THIS plan runs in its own worktree.

---

### Task 1: Pi lock route `GET /chips/anim/lock`

**Files:**
- Modify: `web/serve.mjs` (chips block, ~line 50)
- Test: `web/serve.test.js`

**Interfaces:**
- Produces: `GET /chips/anim/lock` → 200 `text/plain; charset=utf-8`, `cache-control:
  public, max-age=300`, body = the checkout's `web/chips.lock`; 404 when the file is missing.
  `createStaticServer(distDir, opts)` gains `opts.lockFile` (test injection; default =
  `chips.lock` beside serve.mjs). Works even when `MKW_CHIPS_DIR`/`opts.chipsDir` is unset —
  the lock comes from the repo checkout, not the data dir.

- [ ] **Step 1: Write the failing tests** (append to `web/serve.test.js`)

```js
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run serve.test.js`
Expected: FAIL — lock route returns 404 with chipsDir unset (falls into the `!chipsDir` guard).

- [ ] **Step 3: Implement the route** in `web/serve.mjs`

In `createStaticServer`, next to the `chipsDir` line:

```js
const lockFile = opts.lockFile ?? fileURLToPath(new URL("./chips.lock", import.meta.url));
```

Inside the `rawPath.startsWith(CHIPS_PREFIX)` block, BEFORE the `if (!chipsDir)` guard:

```js
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
```

(The existing `const rest = …` line moves up with this; delete the old duplicate.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npx vitest run serve.test.js`
Expected: PASS (all serve tests, old + new).

- [ ] **Step 5: Commit**

```bash
git add web/serve.mjs web/serve.test.js
git commit -m "feat(web): serve chips.lock at /chips/anim/lock for the pbenguin full-pack download"
```

---

### Task 2: Rust deps + `chips/store.rs` (layout, lock parse, tags, eviction)

**Files:**
- Modify: `src-tauri/Cargo.toml`
- Create: `src-tauri/src/chips/mod.rs`, `src-tauri/src/chips/store.rs`
- Modify: `src-tauri/src/lib.rs:1-4` (add `mod chips;`)

**Interfaces:**
- Produces:
  - `store::Lock { tag: String, base: String, files: Vec<(String, String)> }` (sha, name)
  - `store::parse_lock(&str) -> Result<Lock, String>`
  - `store::valid_tag(&str) -> bool`
  - `store::current_tag(dir: &Path) -> Option<String>` / `store::set_current_tag(dir, tag) -> Result<(), String>`
  - `store::resolve(dir: &Path, tag: &str, file: &str) -> Option<PathBuf>` (traversal-guarded, maps to `<dir>/<tag>/chips/<file>`)
  - `store::evict_others(dir: &Path, keep: &[&str])`
  - `store::write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()>` (temp `.part` + rename)

- [ ] **Step 1: Add dependencies** in `src-tauri/Cargo.toml` `[dependencies]`:

```toml
sha2 = "0.10"
tar = "0.4"
fs2 = "0.4"
```

NO `[dev-dependencies]` (standing rule). Instead create the std-only test helpers module
`src-tauri/src/chips/testutil.rs`, declared in `chips/mod.rs` as
`#[cfg(test)] pub mod testutil;`:

```rust
//! std-only test helpers (dev-dependencies are banned in this crate — standing rule).
//! TmpDir ~= tempfile::tempdir(); TestServer ~= a one-route tiny_http with Range support.

use std::io::{Read, Write};
use std::sync::atomic::{AtomicU32, Ordering};

static N: AtomicU32 = AtomicU32::new(0);

pub struct TmpDir(std::path::PathBuf);
impl TmpDir {
    pub fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "mkw-chips-test-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
        std::fs::create_dir_all(&p).unwrap();
        TmpDir(p)
    }
    pub fn path(&self) -> &std::path::Path { &self.0 }
}
impl Drop for TmpDir {
    fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
}

/// Minimal HTTP/1.1 server: `route(path, range_from) -> (status, body)`. Serves until
/// dropped. Connection: close per request; enough for blocking reqwest in tests.
pub struct TestServer {
    pub base: String,
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl TestServer {
    pub fn spawn(route: impl Fn(&str, Option<u64>) -> (u16, Vec<u8>) + Send + Sync + 'static) -> Self {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let s2 = stop.clone();
        let handle = std::thread::spawn(move || {
            while !s2.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((mut sock, _)) => {
                        sock.set_nonblocking(false).unwrap();
                        let mut buf = Vec::new();
                        let mut tmp = [0u8; 1024];
                        while !buf.windows(4).any(|w| w == b"\r\n\r\n") {
                            match sock.read(&mut tmp) {
                                Ok(0) => break,
                                Ok(n) => buf.extend_from_slice(&tmp[..n]),
                                Err(_) => break,
                            }
                        }
                        let text = String::from_utf8_lossy(&buf);
                        let path = text.lines().next()
                            .and_then(|l| l.split_whitespace().nth(1)).unwrap_or("/").to_string();
                        let range = text.lines()
                            .find(|l| l.to_ascii_lowercase().starts_with("range:"))
                            .and_then(|l| l.split('=').nth(1))
                            .and_then(|v| v.trim().trim_end_matches('-').parse::<u64>().ok());
                        let (status, body) = route(&path, range);
                        let reason = if status == 206 { "Partial Content" } else if status >= 400 { "Error" } else { "OK" };
                        let _ = sock.write_all(format!(
                            "HTTP/1.1 {status} {reason}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                            body.len()).as_bytes());
                        let _ = sock.write_all(&body);
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        TestServer { base, stop, handle: Some(handle) }
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(h) = self.handle.take() { let _ = h.join(); }
    }
}
```

Substitution rules for every test block in this plan: `tempfile::tempdir()` →
`let t = crate::chips::testutil::TmpDir::new();` (same `t.path()` shape);
`tiny_http`-based servers (incl. `spawn_pack_server*`) → `TestServer::spawn(route)` where
the route closure implements the same path/Range behaviour, and `srv.server_addr()`-style
address plumbing → `srv.base` (drop the manual thread + `.recv()` loops; assertions on the
requested path move into the route closure or are dropped where redundant).

- [ ] **Step 2: Create `src-tauri/src/chips/mod.rs`** (skeleton; grows in later tasks)

```rust
//! Chip sprite-sheet cache + full-pack download (spec 2026-07-19).
//! store = disk layout/lock/tags (pure). net = site fetch + manifest rewrite.
//! pack = full-pack downloader. commands = tauri commands + protocol glue.

pub mod store;
```

and `mod chips;` at the top of `src-tauri/src/lib.rs` (alongside `mod discord;` etc.).

- [ ] **Step 3: Write failing tests** at the bottom of `src-tauri/src/chips/store.rs`
(create the file with `use std::path::{Path, PathBuf};` and empty stubs that
`todo!()`, plus this test module):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const LOCK: &str = "tag chips-v1\nbase https://example.com/dl\n\
        1111111111111111111111111111111111111111111111111111111111111111  chips-manifest.json\n\
        2222222222222222222222222222222222222222222222222222222222222222  chips-mario.tar\n";

    #[test]
    fn parses_the_lock_shape() {
        let l = parse_lock(LOCK).unwrap();
        assert_eq!(l.tag, "chips-v1");
        assert_eq!(l.base, "https://example.com/dl");
        assert_eq!(l.files.len(), 2);
        assert_eq!(l.files[1].1, "chips-mario.tar");
    }

    #[test]
    fn rejects_locks_missing_tag_base_or_files() {
        assert!(parse_lock("").is_err());
        assert!(parse_lock("tag chips-v1\nbase https://x\n").is_err());
        assert!(parse_lock("base https://x\naaaa  f.tar\n").is_err());
    }

    #[test]
    fn tag_validation_is_strict() {
        assert!(valid_tag("chips-v1") && valid_tag("chips-v12"));
        for bad in ["chips-v", "chips-v1x", "v1", "..", "chips-v1/..", ""] {
            assert!(!valid_tag(bad), "{bad}");
        }
    }

    #[test]
    fn resolve_guards_traversal() {
        let d = Path::new("/data/chips");
        assert_eq!(resolve(d, "chips-v1", "a__idle.webp").unwrap(),
                   d.join("chips-v1").join("chips").join("a__idle.webp"));
        for bad in ["../x", "a/../x", "a\\x", "", "."] {
            assert!(resolve(d, "chips-v1", bad).is_none(), "{bad}");
        }
        assert!(resolve(d, "chips-v1..", "a.webp").is_none());
    }

    #[test]
    fn current_tag_roundtrip_and_eviction() {
        let t = tempfile::tempdir().unwrap();
        let d = t.path();
        assert_eq!(current_tag(d), None);
        set_current_tag(d, "chips-v2").unwrap();
        assert_eq!(current_tag(d).as_deref(), Some("chips-v2"));
        std::fs::create_dir_all(d.join("chips-v1/chips")).unwrap();
        std::fs::create_dir_all(d.join("chips-v2/chips")).unwrap();
        std::fs::create_dir_all(d.join("chips-v3/chips")).unwrap();
        evict_others(d, &["chips-v2", "chips-v3"]);
        assert!(!d.join("chips-v1").exists(), "old tag must be deleted");
        assert!(d.join("chips-v2").exists() && d.join("chips-v3").exists());
    }

    #[test]
    fn write_atomic_replaces_content() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("f.json");
        write_atomic(&p, b"one").unwrap();
        write_atomic(&p, b"two").unwrap();
        assert_eq!(std::fs::read(&p).unwrap(), b"two");
        assert!(!t.path().join("f.json.part").exists());
    }
}
```

- [ ] **Step 4: Run to verify failure**

Run: `cd src-tauri && cargo test chips::store`
Expected: FAIL (todo!/unimplemented panics or compile errors on stubs).

- [ ] **Step 5: Implement `store.rs`**

```rust
//! Disk layout, lock parsing, tag bookkeeping. Pure fs + string logic — no network,
//! no tauri types, so all of it unit-tests.

use std::path::{Path, PathBuf};

pub struct Lock {
    pub tag: String,
    pub base: String,
    /// (sha256-hex, filename) in lock order.
    pub files: Vec<(String, String)>,
}

/// Same shape deploy/fetch_chips.sh reads: `tag X`, `base URL`, then `sha  name` lines.
pub fn parse_lock(text: &str) -> Result<Lock, String> {
    let (mut tag, mut base, mut files) = (None, None, Vec::new());
    for line in text.lines() {
        let mut it = line.split_whitespace();
        match (it.next(), it.next()) {
            (Some("tag"), Some(v)) if tag.is_none() => tag = Some(v.to_string()),
            (Some("base"), Some(v)) if base.is_none() => base = Some(v.to_string()),
            (Some(sha), Some(name)) if sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_hexdigit()) =>
                files.push((sha.to_string(), name.to_string())),
            _ => {}
        }
    }
    match (tag, base) {
        (Some(tag), Some(base)) if !files.is_empty() && valid_tag(&tag) => Ok(Lock { tag, base, files }),
        _ => Err("chips: bad lock".into()),
    }
}

/// `chips-v<digits>` only — doubles as the traversal guard for the URL tag segment.
pub fn valid_tag(tag: &str) -> bool {
    tag.strip_prefix("chips-v")
        .is_some_and(|r| !r.is_empty() && r.bytes().all(|b| b.is_ascii_digit()))
}

pub fn current_tag(dir: &Path) -> Option<String> {
    let t = std::fs::read_to_string(dir.join("current")).ok()?;
    let t = t.trim().to_string();
    valid_tag(&t).then_some(t)
}

pub fn set_current_tag(dir: &Path, tag: &str) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    write_atomic(&dir.join("current"), tag.as_bytes()).map_err(|e| e.to_string())
}

/// `<dir>/<tag>/chips/<file>`, or None on anything traversal-shaped.
pub fn resolve(dir: &Path, tag: &str, file: &str) -> Option<PathBuf> {
    if !valid_tag(tag) || file.is_empty() || file.contains('\\') { return None; }
    if file.split('/').any(|s| s.is_empty() || s == "." || s == "..") { return None; }
    Some(dir.join(tag).join("chips").join(file))
}

/// Delete every chips-v* dir not in `keep` (no storage double-up — spec Eviction).
/// `keep` = current tag + the tag an active pack download is filling, if any.
pub fn evict_others(dir: &Path, keep: &[&str]) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let name = e.file_name();
        let Some(n) = name.to_str() else { continue };
        if valid_tag(n) && !keep.contains(&n) {
            let _ = std::fs::remove_dir_all(e.path());
        }
    }
}

/// Temp `.part` + rename — a killed process never leaves a truncated file at `path`.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    if let Some(p) = path.parent() { std::fs::create_dir_all(p)?; }
    let tmp = path.with_extension(
        format!("{}part", path.extension().map(|e| format!("{}.", e.to_string_lossy())).unwrap_or_default()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src-tauri && cargo test chips::store`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/chips src-tauri/src/lib.rs
git commit -m "feat(chips): store module — lock parse, tag layout, eviction, atomic writes"
```

---

### Task 3: `chips/net.rs` (site base, manifest rewrite/refresh, single-file fetch)

**Files:**
- Create: `src-tauri/src/chips/net.rs`
- Modify: `src-tauri/src/chips/mod.rs` (add `pub mod net;`)

**Interfaces:**
- Consumes: `store::{valid_tag, current_tag, set_current_tag, resolve, evict_others, write_atomic}`
- Produces:
  - `net::site_base() -> String` — `PBENGUIN_CHIPS_URL` env else `https://thekartoff.com/chips/anim`
  - `net::LOCAL_BASE: &str = "http://chips.localhost/"`
  - `net::rewrite_manifest(body: &str) -> Result<(String, String), String>` — (tag, rewritten JSON with `base` = `http://chips.localhost/<tag>/`)
  - `net::refresh_manifest(dir: &Path, active_pack_tag: Option<&str>) -> Result<String, String>` — fetch + persist + set current + evict; offline falls back to the cached copy; returns rewritten JSON
  - `net::fetch_file(dir: &Path, tag: &str, file: &str) -> Result<Vec<u8>, String>` — one-file miss fetch, cached via `write_atomic` (cache-write failure degrades to pass-through)

- [ ] **Step 1: Write failing tests** (in `net.rs` `#[cfg(test)]`; the HTTP ones run a
`tiny_http` server on an ephemeral port and point `PBENGUIN_CHIPS_URL` at it — set/remove the
env var inside each test, and mark those tests `#[serial]`-free by using a process-wide mutex:)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    static ENV: Mutex<()> = Mutex::new(());   // env-var tests must not interleave

    #[test]
    fn rewrites_base_to_local_and_extracts_tag() {
        let body = r#"{"version":1,"fps":60,"base":"/chips/anim/chips-v1/","combos":{}}"#;
        let (tag, out) = rewrite_manifest(body).unwrap();
        assert_eq!(tag, "chips-v1");
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["base"], "http://chips.localhost/chips-v1/");
    }

    #[test]
    fn rejects_manifest_with_bad_base() {
        assert!(rewrite_manifest(r#"{"combos":{}}"#).is_err());
        assert!(rewrite_manifest(r#"{"base":"/chips/anim/../x/"}"#).is_err());
    }

    fn serve_one(body: Vec<u8>, status: u16) -> (tiny_http::Server, String) {
        let srv = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let addr = format!("http://{}", srv.server_addr());
        (srv, addr)
    }

    #[test]
    fn refresh_fetches_persists_and_evicts() {
        let _g = ENV.lock().unwrap();
        let t = tempfile::tempdir().unwrap();
        let dir = t.path().to_path_buf();
        std::fs::create_dir_all(dir.join("chips-v0/chips")).unwrap(); // stale tag to evict
        let srv = tiny_http::Server::http("127.0.0.1:0").unwrap();
        std::env::set_var("PBENGUIN_CHIPS_URL", format!("http://{}", srv.server_addr()));
        let h = std::thread::spawn(move || {
            let rq = srv.recv().unwrap();
            assert!(rq.url().ends_with("/manifest.json"));
            rq.respond(tiny_http::Response::from_string(
                r#"{"base":"/chips/anim/chips-v1/","combos":{}}"#)).unwrap();
        });
        let out = refresh_manifest(&dir, None).unwrap();
        h.join().unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(out.contains("chips.localhost/chips-v1/"));
        assert_eq!(store::current_tag(&dir).as_deref(), Some("chips-v1"));
        assert!(dir.join("chips-v1/chips/manifest.json").exists(), "raw manifest persisted");
        assert!(!dir.join("chips-v0").exists(), "stale tag evicted on adoption");
    }

    #[test]
    fn refresh_offline_serves_cached_copy() {
        let _g = ENV.lock().unwrap();
        let t = tempfile::tempdir().unwrap();
        let dir = t.path().to_path_buf();
        store::set_current_tag(&dir, "chips-v1").unwrap();
        store::write_atomic(&dir.join("chips-v1/chips/manifest.json"),
            br#"{"base":"/chips/anim/chips-v1/","combos":{}}"#).unwrap();
        std::env::set_var("PBENGUIN_CHIPS_URL", "http://127.0.0.1:9"); // nothing listens
        let out = refresh_manifest(&dir, None).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(out.contains("chips.localhost/chips-v1/"));
    }

    #[test]
    fn fetch_file_caches_on_disk() {
        let _g = ENV.lock().unwrap();
        let t = tempfile::tempdir().unwrap();
        let dir = t.path().to_path_buf();
        let srv = tiny_http::Server::http("127.0.0.1:0").unwrap();
        std::env::set_var("PBENGUIN_CHIPS_URL", format!("http://{}", srv.server_addr()));
        let h = std::thread::spawn(move || {
            let rq = srv.recv().unwrap();
            assert_eq!(rq.url(), "/chips-v1/a__idle.webp");
            rq.respond(tiny_http::Response::from_data(vec![7u8; 32])).unwrap();
        });
        let bytes = fetch_file(&dir, "chips-v1", "a__idle.webp").unwrap();
        h.join().unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert_eq!(bytes.len(), 32);
        assert_eq!(std::fs::read(dir.join("chips-v1/chips/a__idle.webp")).unwrap(), bytes);
    }
}
```

(Delete the unused `serve_one` helper if it stays unused after implementation — it's shown
here as scaffolding you may fold into the two HTTP tests.)

- [ ] **Step 2: Run to verify failure** — `cd src-tauri && cargo test chips::net` → FAIL.

- [ ] **Step 3: Implement `net.rs`**

```rust
//! Site-facing fetches: manifest refresh (with offline fallback + tag adoption/eviction)
//! and single-file miss fetch. Blocking reqwest — callers run on worker threads.

use super::store;
use std::path::Path;

pub const LOCAL_BASE: &str = "http://chips.localhost/";

pub fn site_base() -> String {
    std::env::var("PBENGUIN_CHIPS_URL")
        .ok().filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| "https://thekartoff.com/chips/anim".into())
}

fn get(url: &str) -> Result<reqwest::blocking::Response, String> {
    reqwest::blocking::Client::new().get(url)
        .timeout(std::time::Duration::from_secs(30))
        .send().and_then(|r| r.error_for_status())
        .map_err(|e| format!("chips: GET {url}: {e}"))
}

/// (tag, manifest-with-local-base). The site's injected base carries the tag.
pub fn rewrite_manifest(body: &str) -> Result<(String, String), String> {
    let mut v: serde_json::Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
    let base = v.get("base").and_then(|b| b.as_str()).ok_or("chips: manifest missing base")?;
    let tag = base.trim_end_matches('/').rsplit('/').next().unwrap_or_default().to_string();
    if !store::valid_tag(&tag) { return Err(format!("chips: bad tag in manifest base {base:?}")); }
    v["base"] = serde_json::Value::String(format!("{LOCAL_BASE}{tag}/"));
    Ok((tag, v.to_string()))
}

/// Fetch the site manifest; persist the RAW copy, adopt its tag as current, evict other
/// tags (spec Eviction — sparing an active pack download's tag). Offline → last cached.
pub fn refresh_manifest(dir: &Path, active_pack_tag: Option<&str>) -> Result<String, String> {
    match get(&format!("{}/manifest.json", site_base())).and_then(|r| r.text().map_err(|e| e.to_string())) {
        Ok(body) => {
            let (tag, rewritten) = rewrite_manifest(&body)?;
            store::write_atomic(&dir.join(&tag).join("chips").join("manifest.json"), body.as_bytes())
                .map_err(|e| e.to_string())?;
            let prev = store::current_tag(dir);
            store::set_current_tag(dir, &tag)?;
            if prev.as_deref() != Some(tag.as_str()) {
                let mut keep = vec![tag.as_str()];
                if let Some(a) = active_pack_tag { keep.push(a); }
                store::evict_others(dir, &keep);
            }
            Ok(rewritten)
        }
        Err(e) => {
            let tag = store::current_tag(dir).ok_or(format!("chips: offline, no cache: {e}"))?;
            let body = std::fs::read_to_string(dir.join(&tag).join("chips").join("manifest.json"))
                .map_err(|e2| format!("chips: offline, cache unreadable: {e2}"))?;
            Ok(rewrite_manifest(&body)?.1)
        }
    }
}

/// One-file on-demand fetch. Cache-write failure is non-fatal (pass the bytes through).
pub fn fetch_file(dir: &Path, tag: &str, file: &str) -> Result<Vec<u8>, String> {
    let path = store::resolve(dir, tag, file).ok_or("chips: bad path")?;
    let bytes = get(&format!("{}/{}/{}", site_base(), tag, file))?
        .bytes().map_err(|e| e.to_string())?.to_vec();
    if let Err(e) = store::write_atomic(&path, &bytes) {
        log::warn!("[chips] cache write {path:?} failed: {e} — serving uncached");
    }
    Ok(bytes)
}
```

- [ ] **Step 4: Run tests** — `cargo test chips::net` → PASS. Also run the whole suite once
(`cargo test`) to catch cross-module breakage.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/chips
git commit -m "feat(chips): net module — manifest refresh/rewrite, on-demand file fetch"
```

---

### Task 4: `chips://` protocol handler + registration

**Files:**
- Create: `src-tauri/src/chips/protocol.rs`
- Modify: `src-tauri/src/chips/mod.rs` (`pub mod protocol;` + `chips_root` helper)
- Modify: `src-tauri/src/lib.rs` (builder: `.register_asynchronous_uri_scheme_protocol("chips", …)`)
- Verify: `src-tauri/tauri.conf.json` CSP

**Interfaces:**
- Consumes: `net::{refresh_manifest, fetch_file}`, `store::{resolve, current_tag}`
- Produces:
  - `protocol::serve(dir: &Path, path: &str, active_pack_tag: Option<&str>) -> (u16, &'static str, Vec<u8>)`
    — status, content-type, body. Pure-ish (network only via net); testable for the cache-hit
    and bad-path branches without any server.
  - Frontend contract: `http://chips.localhost/manifest.json` (rewritten manifest, `no-cache`)
    and `http://chips.localhost/<tag>/<file>` (immutable) — Plan B consumes these.
  - Manifest refresh throttle: at most one network refresh per 300 s (in-memory `Instant`).

- [ ] **Step 1: Write failing tests** (`protocol.rs` `#[cfg(test)]`):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serves_cached_file_without_network() {
        let t = tempfile::tempdir().unwrap();
        let dir = t.path();
        crate::chips::store::write_atomic(
            &dir.join("chips-v1/chips/a__idle.webp"), b"WEBPDATA").unwrap();
        let (status, ct, body) = serve(dir, "/chips-v1/a__idle.webp", None);
        assert_eq!(status, 200);
        assert_eq!(ct, "image/webp");
        assert_eq!(body, b"WEBPDATA");
    }

    #[test]
    fn rejects_traversal_paths() {
        let t = tempfile::tempdir().unwrap();
        for p in ["/chips-v1/../secret", "/..%2Fx", "/chips-v1/a\\b", "/nope"] {
            let (status, _, _) = serve(t.path(), p, None);
            assert_eq!(status, 404, "{p}");
        }
    }

    #[test]
    fn content_types_cover_the_pack() {
        assert_eq!(ctype("x.webp"), "image/webp");
        assert_eq!(ctype("x__sil_k0.png"), "image/png");
        assert_eq!(ctype("manifest.json"), "application/json");
        assert_eq!(ctype("other.bin"), "application/octet-stream");
    }
}
```

- [ ] **Step 2: Run to verify failure** — `cargo test chips::protocol` → FAIL.

- [ ] **Step 3: Implement `protocol.rs`**

```rust
//! chips:// scheme (http://chips.localhost/ in WebView2). Cache-first; misses go through
//! net. The manifest is refreshed from the site at most every 5 min (site max-age).

use super::{net, store};
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant};

const MANIFEST_TTL: Duration = Duration::from_secs(300);
static MANIFEST_CACHE: Mutex<Option<(Instant, String)>> = Mutex::new(None);

pub fn ctype(name: &str) -> &'static str {
    if name.ends_with(".webp") { "image/webp" }
    else if name.ends_with(".png") { "image/png" }
    else if name.ends_with(".json") { "application/json" }
    else { "application/octet-stream" }
}

fn manifest(dir: &Path, active_pack_tag: Option<&str>) -> Result<String, String> {
    let mut g = MANIFEST_CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((at, body)) = g.as_ref() {
        if at.elapsed() < MANIFEST_TTL { return Ok(body.clone()); }
    }
    match net::refresh_manifest(dir, active_pack_tag) {
        Ok(body) => { *g = Some((Instant::now(), body.clone())); Ok(body) }
        Err(e) => g.as_ref().map(|(_, b)| b.clone()).ok_or(e), // stale beats nothing
    }
}

pub fn serve(dir: &Path, path: &str, active_pack_tag: Option<&str>) -> (u16, &'static str, Vec<u8>) {
    let path = path.trim_start_matches('/');
    if path == "manifest.json" {
        return match manifest(dir, active_pack_tag) {
            Ok(b) => (200, "application/json", b.into_bytes()),
            Err(e) => { log::warn!("[chips] manifest: {e}"); (404, "text/plain", e.into_bytes()) }
        };
    }
    let Some((tag, file)) = path.split_once('/') else { return (404, "text/plain", b"not found".to_vec()) };
    let Some(p) = store::resolve(dir, tag, file) else { return (404, "text/plain", b"not found".to_vec()) };
    match std::fs::read(&p) {
        Ok(b) => (200, ctype(file), b),
        Err(_) => match net::fetch_file(dir, tag, file) {
            Ok(b) => (200, ctype(file), b),
            Err(e) => { log::debug!("[chips] miss {path}: {e}"); (404, "text/plain", e.into_bytes()) }
        },
    }
}
```

In `chips/mod.rs` add:

```rust
pub mod net;
pub mod protocol;

/// `<app-data>/chips`. Panics never: app_data_dir is infallible post-setup on Windows.
pub fn chips_root<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> std::path::PathBuf {
    use tauri::Manager;
    app.path().app_data_dir().expect("app_data_dir").join("chips")
}
```

- [ ] **Step 4: Register the scheme** in `lib.rs`, on the builder chain (next to the
plugins, BEFORE `.setup(...)`):

```rust
.register_asynchronous_uri_scheme_protocol("chips", |ctx, request, responder| {
    let app = ctx.app_handle().clone();
    let path = request.uri().path().to_string();
    // Blocking fs + reqwest work — never on the event loop thread.
    tauri::async_runtime::spawn_blocking(move || {
        let active = chips::active_pack_tag();
        let (status, ct, body) = chips::protocol::serve(&chips::chips_root(&app), &path, active.as_deref());
        let cache = if path.ends_with("manifest.json") { "no-cache" } else { "public, max-age=31536000, immutable" };
        responder.respond(
            tauri::http::Response::builder()
                .status(status)
                .header("content-type", ct)
                .header("cache-control", cache)
                .body(body)
                .unwrap(),
        );
    });
})
```

`chips::active_pack_tag()` for now is a stub in `mod.rs` (real value arrives in Task 6):

```rust
/// Tag an in-flight full-pack download is filling (eviction must spare it). Task 6 wires it.
pub fn active_pack_tag() -> Option<String> {
    ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()).clone()
}
pub static ACTIVE_PACK_TAG: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
```

- [ ] **Step 5: CSP check.** Open `src-tauri/tauri.conf.json` → `app.security.csp`. If it is
`null`/absent, nothing to do. If a CSP string exists, add `http://chips.localhost` to
`img-src` and `connect-src`. Record which case applied in the commit message.

- [ ] **Step 6: Run** — `cargo test chips::` (all chips tests PASS) and `cargo build`
(compiles with the registration).

- [ ] **Step 7: Manual smoke** — `npm run tauri dev`, open devtools console:

```js
fetch("http://chips.localhost/manifest.json").then(r => r.json()).then(m => console.log(m.base, Object.keys(m.combos).length))
```

Expected (online): `http://chips.localhost/chips-v1/ 6273`. Then fetch one sheet URL and
confirm a second fetch is instant (file present under `%APPDATA%\mkw-tracker\chips\chips-v1\chips\`).
NOTE: this needs the chips pack live on thekartoff.com (deployed). If the deploy hasn't
happened yet, run the Task 9 fake-pack rehearsal instead and smoke against `PBENGUIN_CHIPS_URL`.

- [ ] **Step 8: Commit**

```bash
git add src-tauri/src/chips src-tauri/src/lib.rs src-tauri/tauri.conf.json
git commit -m "feat(chips): chips:// protocol — cache-first serving with on-demand miss fetch"
```

---

### Task 5: `chips/pack.rs` — state file, reconcile, sha, untar

**Files:**
- Create: `src-tauri/src/chips/pack.rs`
- Modify: `src-tauri/src/chips/mod.rs` (`pub mod pack;`)

**Interfaces:**
- Consumes: `store::Lock`
- Produces:
  - `pack::Status` enum: `Pending | Downloaded | Done` (serde, lowercase)
  - `pack::PackState { tag: String, base: String, shards: Vec<Shard> }`,
    `pack::Shard { name: String, sha: String, status: Status }`
  - `pack::load_state(tag_dir: &Path) -> Option<PackState>` / `pack::save_state(tag_dir, &PackState)`
    (file `.pack-state.json` via `store::write_atomic`)
  - `pack::reconcile(lock: &Lock, prev: Option<PackState>) -> PackState` — new tag ⇒ all
    Pending; same tag ⇒ keep `Done` only where the sha matches (changed shards redo)
  - `pack::sha256_file(path: &Path) -> Result<String, String>` (streamed, lowercase hex)
  - `pack::untar_into(tar_path: &Path, dest: &Path) -> Result<(), String>` — rejects entries
    with traversal paths; overwrite-idempotent

- [ ] **Step 1: Write failing tests** (`pack.rs` `#[cfg(test)]`):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::chips::store::Lock;

    fn lock(tag: &str, files: &[(&str, &str)]) -> Lock {
        Lock { tag: tag.into(), base: "https://x/dl".into(),
               files: files.iter().map(|(s, n)| (s.to_string(), n.to_string())).collect() }
    }

    #[test]
    fn reconcile_fresh_and_tag_change() {
        let l = lock("chips-v1", &[("a".repeat(64).leak(), "one.tar")]);
        let s = reconcile(&l, None);
        assert!(matches!(s.shards[0].status, Status::Pending));
        let prev = PackState { tag: "chips-v0".into(), base: s.base.clone(), shards: s.shards.clone() };
        let s2 = reconcile(&l, Some(prev));
        assert!(matches!(s2.shards[0].status, Status::Pending), "tag change restarts");
        assert_eq!(s2.tag, "chips-v1");
    }

    #[test]
    fn reconcile_same_tag_keeps_done_only_on_matching_sha() {
        let sha_a = "a".repeat(64); let sha_b = "b".repeat(64);
        let l = lock("chips-v1", &[(&sha_a, "one.tar"), (&sha_b, "two.tar")]);
        let mut prev = reconcile(&l, None);
        prev.shards[0].status = Status::Done;
        prev.shards[1].status = Status::Done;
        prev.shards[1].sha = "c".repeat(64);       // sha changed upstream
        let s = reconcile(&l, Some(prev));
        assert!(matches!(s.shards[0].status, Status::Done));
        assert!(matches!(s.shards[1].status, Status::Pending));
    }

    #[test]
    fn state_roundtrips_via_disk() {
        let t = tempfile::tempdir().unwrap();
        let l = lock("chips-v1", &[("d".repeat(64).leak(), "one.tar")]);
        let s = reconcile(&l, None);
        save_state(t.path(), &s).unwrap();
        let r = load_state(t.path()).unwrap();
        assert_eq!(r.tag, "chips-v1");
        assert_eq!(r.shards.len(), 1);
    }

    #[test]
    fn sha256_streams_correctly() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("f");
        std::fs::write(&p, b"abc").unwrap();
        assert_eq!(sha256_file(&p).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn untar_extracts_and_rejects_traversal() {
        let t = tempfile::tempdir().unwrap();
        let tarp = t.path().join("x.tar");
        {   // build a tar with one good entry
            let f = std::fs::File::create(&tarp).unwrap();
            let mut b = tar::Builder::new(f);
            let data = b"hello"; let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64); h.set_mode(0o644); h.set_cksum();
            b.append_data(&mut h, "a__idle.webp", &data[..]).unwrap();
            b.finish().unwrap();
        }
        let dest = t.path().join("out");
        untar_into(&tarp, &dest).unwrap();
        assert_eq!(std::fs::read(dest.join("a__idle.webp")).unwrap(), b"hello");
        untar_into(&tarp, &dest).unwrap(); // overwrite-idempotent (resume re-untars)
    }
}
```

- [ ] **Step 2: Run to verify failure** — `cargo test chips::pack` → FAIL.

- [ ] **Step 3: Implement**

```rust
//! Full-pack download bookkeeping: .pack-state.json + sha + untar. The runner (Task 6)
//! drives these; everything here is synchronous and unit-testable.

use super::store::{self, Lock};
use serde::{Deserialize, Serialize};
use std::io::Read;
use std::path::Path;

#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum Status { Pending, Downloaded, Done }

#[derive(Clone, Serialize, Deserialize)]
pub struct Shard { pub name: String, pub sha: String, pub status: Status }

#[derive(Clone, Serialize, Deserialize)]
pub struct PackState { pub tag: String, pub base: String, pub shards: Vec<Shard> }

const STATE_FILE: &str = ".pack-state.json";

pub fn load_state(tag_dir: &Path) -> Option<PackState> {
    serde_json::from_str(&std::fs::read_to_string(tag_dir.join(STATE_FILE)).ok()?).ok()
}

pub fn save_state(tag_dir: &Path, s: &PackState) -> Result<(), String> {
    store::write_atomic(&tag_dir.join(STATE_FILE),
        serde_json::to_string_pretty(s).map_err(|e| e.to_string())?.as_bytes())
        .map_err(|e| e.to_string())
}

/// New tag ⇒ everything Pending. Same tag ⇒ a shard stays Done only if its sha still
/// matches the lock (spec: "same tag but changed shas → only changed shards redo").
pub fn reconcile(lock: &Lock, prev: Option<PackState>) -> PackState {
    let prev = prev.filter(|p| p.tag == lock.tag);
    let shards = lock.files.iter().map(|(sha, name)| {
        let status = prev.as_ref()
            .and_then(|p| p.shards.iter().find(|s| &s.name == name))
            .filter(|s| &s.sha == sha && s.status == Status::Done)
            .map(|_| Status::Done).unwrap_or(Status::Pending);
        Shard { name: name.clone(), sha: sha.clone(), status }
    }).collect();
    PackState { tag: lock.tag.clone(), base: lock.base.clone(), shards }
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut h = Sha256::new();
    let mut buf = [0u8; 65536];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 { break; }
        h.update(&buf[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}

/// Extract a shard into `<tag>/chips/`. Overwrites (a killed run re-untars its shard).
pub fn untar_into(tar_path: &Path, dest: &Path) -> Result<(), String> {
    std::fs::create_dir_all(dest).map_err(|e| e.to_string())?;
    let f = std::fs::File::open(tar_path).map_err(|e| e.to_string())?;
    let mut ar = tar::Archive::new(f);
    ar.set_overwrite(true);
    // tar-rs `unpack` already refuses paths escaping dest; keep it (no per-entry loop).
    ar.unpack(dest).map_err(|e| format!("untar {tar_path:?}: {e}"))
}
```

- [ ] **Step 4: Run** — `cargo test chips::pack` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/chips
git commit -m "feat(chips): pack state — reconcile, sha256, sanitized untar"
```

---

### Task 6: pack runner (Range-resumable download loop)

**Files:**
- Modify: `src-tauri/src/chips/pack.rs` (add runner section)

**Interfaces:**
- Consumes: Task 5 items, `net::site_base`, `store::*`, `chips::ACTIVE_PACK_TAG`
- Produces:
  - `pack::Ctl` = `Arc<AtomicU8>` values `RUN=0 / PAUSE=1 / CANCEL=2` (consts `CTL_RUN` etc.)
  - `pack::Outcome` enum: `Complete | Paused | Cancelled`
  - `pack::Progress { tag: String, done: usize, total: usize, shard: String, shard_bytes: u64, state: String }` (serde Serialize)
  - `pack::run_pack(dir: &Path, ctl: &AtomicU8, emit: &(dyn Fn(&Progress) + Send + Sync)) -> Result<Outcome, String>`
    — fetches `<site_base>/lock`, reconciles, per shard: download (Range resume) → sha →
    untar/copy → delete tar → save state; writes `.complete` + sets `current` (only when no
    current tag exists yet) on completion. Emits progress on every state change and every
    ~1 MB of download. Free-disk pre-check: 8 GiB via `fs2::available_space` when any shard
    is Pending (skip the check when everything is already Downloaded/Done).
  - Shard staging: partial downloads live at `<tag_dir>/.stage/<name>`; `chips-manifest.json`
    is copied (not untarred) to `<tag_dir>/chips/manifest.json` after verify.

- [ ] **Step 1: Write failing tests.** Test HTTP server: tiny_http with manual Range support
(serves a fixed byte buffer; on `Range: bytes=N-` responds 206 with the slice). Include this
helper in the test module:

```rust
#[cfg(test)]
mod runner_tests {
    use super::*;
    use std::sync::atomic::{AtomicU8, Ordering};
    use std::sync::{Arc, Mutex};

    /// Range-aware one-endpoint server: GET /lock -> lock text; GET /<name> -> data slice.
    fn spawn_pack_server(lock_text: String, files: Vec<(String, Vec<u8>)>) -> (String, std::thread::JoinHandle<()>) {
        let srv = tiny_http::Server::http("127.0.0.1:0").unwrap();
        let addr = format!("http://{}", srv.server_addr());
        let h = std::thread::spawn(move || {
            for rq in srv.incoming_requests() {
                let url = rq.url().trim_start_matches('/').to_string();
                if url == "lock" { let _ = rq.respond(tiny_http::Response::from_string(lock_text.clone())); continue; }
                let Some((_, data)) = files.iter().find(|(n, _)| *n == url) else {
                    let _ = rq.respond(tiny_http::Response::empty(404)); continue;
                };
                let from = rq.headers().iter()
                    .find(|h| h.field.equiv("Range"))
                    .and_then(|h| h.value.as_str().strip_prefix("bytes=")
                        .and_then(|v| v.trim_end_matches('-').parse::<usize>().ok()))
                    .unwrap_or(0);
                let slice = data[from.min(data.len())..].to_vec();
                let status = if from > 0 { 206 } else { 200 };
                let _ = rq.respond(tiny_http::Response::from_data(slice).with_status_code(status));
            }
        });
        (addr, h)
    }

    /// One-shard pack: tar containing a__idle.webp; lock with its real sha.
    fn fixture() -> (String, Vec<u8>, String) {
        let mut tarbuf = Vec::new();
        {
            let mut b = tar::Builder::new(&mut tarbuf);
            let data = vec![9u8; 300_000]; // big enough to pause mid-flight
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64); h.set_mode(0o644); h.set_cksum();
            b.append_data(&mut h, "a__idle.webp", &data[..]).unwrap();
            b.finish().unwrap();
        }
        use sha2::{Digest, Sha256};
        let sha = format!("{:x}", Sha256::digest(&tarbuf));
        (sha.clone(), tarbuf, sha)
    }

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static ENV: Mutex<()> = Mutex::new(());
        ENV.lock().unwrap_or_else(|e| e.into_inner())
    }

    #[test]
    fn downloads_verifies_untars_and_completes() {
        let _g = env_lock();
        let (sha, tarbuf, _) = fixture();
        let lock_text = format!("tag chips-v1\nbase {{B}}\n{sha}  chips-a.tar\n");
        let (addr, _h) = spawn_pack_server(lock_text.replace("{B}", "SELF"), vec![("chips-a.tar".into(), tarbuf)]);
        // base must point at the same server: rewrite {B} with the real addr
        // (spawn again with the final text — simplest: build addr-independent by
        //  serving base==addr; see implementation note below)
        let (addr, _h) = { let (sha2, tarbuf2, _) = fixture();
            let (a, h) = spawn_pack_server_with_self_base(sha2, tarbuf2); (a, h) };
        std::env::set_var("PBENGUIN_CHIPS_URL", &addr);
        let t = tempfile::tempdir().unwrap();
        let ctl = AtomicU8::new(CTL_RUN);
        let out = run_pack(t.path(), &ctl, &|_p| {}).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(matches!(out, Outcome::Complete));
        let td = t.path().join("chips-v1");
        assert!(td.join(".complete").exists());
        assert!(td.join("chips/a__idle.webp").exists());
        assert!(!td.join(".stage/chips-a.tar").exists(), "tar deleted after untar");
    }

    #[test]
    fn pause_persists_partial_and_resume_completes_via_range() {
        let _g = env_lock();
        let (addr, _h) = { let (sha, tarbuf, _) = fixture(); spawn_pack_server_with_self_base(sha, tarbuf) };
        std::env::set_var("PBENGUIN_CHIPS_URL", &addr);
        let t = tempfile::tempdir().unwrap();
        // pause after the first progress event that reports shard bytes
        let ctl = Arc::new(AtomicU8::new(CTL_RUN));
        let c2 = ctl.clone();
        let out = run_pack(t.path(), &ctl, &move |p| {
            if p.state == "downloading" && p.shard_bytes > 0 { c2.store(CTL_PAUSE, Ordering::SeqCst); }
        }).unwrap();
        assert!(matches!(out, Outcome::Paused));
        let partial = t.path().join("chips-v1/.stage/chips-a.tar");
        assert!(partial.exists(), "partial shard survives pause");
        let before = std::fs::metadata(&partial).unwrap().len();
        assert!(before > 0);
        // resume: same code path, finishes from the partial
        let ctl2 = AtomicU8::new(CTL_RUN);
        let out2 = run_pack(t.path(), &ctl2, &|_p| {}).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(matches!(out2, Outcome::Complete));
        assert!(t.path().join("chips-v1/.complete").exists());
    }

    #[test]
    fn corrupt_shard_is_redownloaded_not_fatal() {
        let _g = env_lock();
        let (addr, _h) = { let (sha, tarbuf, _) = fixture(); spawn_pack_server_with_self_base(sha, tarbuf) };
        std::env::set_var("PBENGUIN_CHIPS_URL", &addr);
        let t = tempfile::tempdir().unwrap();
        // poison a pre-existing partial so the first sha check fails
        let stage = t.path().join("chips-v1/.stage");
        std::fs::create_dir_all(&stage).unwrap();
        std::fs::write(stage.join("chips-a.tar"), b"garbage-longer-than-real? no: shorter").unwrap();
        let ctl = AtomicU8::new(CTL_RUN);
        let out = run_pack(t.path(), &ctl, &|_p| {}).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(matches!(out, Outcome::Complete), "bad partial → refetch from zero, still completes");
    }
}
```

Implementation note for the fixture helper the tests call:

```rust
#[cfg(test)]
fn spawn_pack_server_with_self_base(sha: String, tarbuf: Vec<u8>) -> (String, std::thread::JoinHandle<()>) {
    // Two-step: bind first so the addr is known, THEN build the lock text with base=addr.
    let srv = tiny_http::Server::http("127.0.0.1:0").unwrap();
    let addr = format!("http://{}", srv.server_addr());
    let lock_text = format!("tag chips-v1\nbase {addr}\n{sha}  chips-a.tar\n");
    let h = std::thread::spawn(move || { /* same request loop as spawn_pack_server */ });
    (addr, h)
}
```

(Fold the request loop into one shared helper — the split above exists only because the base
URL must equal the bound address.)

- [ ] **Step 2: Run to verify failure** — `cargo test chips::pack::runner_tests` → FAIL.

- [ ] **Step 3: Implement the runner** (append to `pack.rs`):

```rust
use std::sync::atomic::{AtomicU8, Ordering};

pub const CTL_RUN: u8 = 0;
pub const CTL_PAUSE: u8 = 1;
pub const CTL_CANCEL: u8 = 2;

pub enum Outcome { Complete, Paused, Cancelled }

#[derive(Clone, serde::Serialize)]
pub struct Progress {
    pub tag: String, pub done: usize, pub total: usize,
    pub shard: String, pub shard_bytes: u64, pub state: String,
}

fn ctl_state(ctl: &AtomicU8) -> u8 { ctl.load(Ordering::SeqCst) }

/// Download `url` to `dest`, resuming from an existing partial via Range. Returns
/// Some(outcome) when interrupted, None when byte-complete.
fn download(url: &str, dest: &Path, ctl: &AtomicU8, mut tick: impl FnMut(u64)) -> Result<Option<Outcome>, String> {
    use std::io::{Seek, SeekFrom, Write};
    if let Some(p) = dest.parent() { std::fs::create_dir_all(p).map_err(|e| e.to_string())?; }
    let mut have = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
    let client = reqwest::blocking::Client::new();
    let mut req = client.get(url).timeout(std::time::Duration::from_secs(24 * 3600));
    if have > 0 { req = req.header("Range", format!("bytes={have}-")); }
    let mut resp = req.send().and_then(|r| {
        // 416 = our partial is already the full file (server has nothing past `have`)
        if r.status().as_u16() == 416 { Ok(r) } else { r.error_for_status() }
    }).map_err(|e| format!("chips: GET {url}: {e}"))?;
    match resp.status().as_u16() {
        416 => return Ok(None),
        206 => {}
        200 => have = 0,           // server ignored Range: restart from zero
        s => return Err(format!("chips: GET {url}: HTTP {s}")),
    }
    let mut f = std::fs::OpenOptions::new().create(true).write(true).open(dest).map_err(|e| e.to_string())?;
    f.set_len(have).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(have)).map_err(|e| e.to_string())?;
    let mut buf = [0u8; 65536];
    let mut since_tick = 0u64;
    loop {
        match ctl_state(ctl) {
            CTL_PAUSE => return Ok(Some(Outcome::Paused)),
            CTL_CANCEL => return Ok(Some(Outcome::Cancelled)),
            _ => {}
        }
        let n = std::io::Read::read(&mut resp, &mut buf).map_err(|e| e.to_string())?;
        if n == 0 { break; }
        f.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        have += n as u64; since_tick += n as u64;
        if since_tick >= 1_000_000 { since_tick = 0; tick(have); }
    }
    tick(have);
    Ok(None)
}

pub fn run_pack(dir: &Path, ctl: &AtomicU8, emit: &(dyn Fn(&Progress) + Send + Sync)) -> Result<Outcome, String> {
    let lock_text = super::net::get_text(&format!("{}/lock", super::net::site_base()))?;
    let lock = store::parse_lock(&lock_text)?;
    let tag_dir = dir.join(&lock.tag);
    std::fs::create_dir_all(tag_dir.join("chips")).map_err(|e| e.to_string())?;
    // Anything that isn't this lock's tag is stale staging — but never touch the
    // current on-demand tag; refresh_manifest owns that eviction.
    *super::ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()) = Some(lock.tag.clone());
    let mut state = reconcile(&lock, load_state(&tag_dir));
    save_state(&tag_dir, &state)?;
    let total = state.shards.len();
    let pending = state.shards.iter().filter(|s| s.status != Status::Done).count();
    if pending > 0 {
        let free = fs2::available_space(dir).unwrap_or(u64::MAX);
        if free < 8 * 1024 * 1024 * 1024 && pending == total {
            return Err(format!("chips: need ~8 GB free, have {} GB", free / (1024 * 1024 * 1024)));
        }
    }
    let prog = |state_str: &str, i: usize, shard: &str, bytes: u64| Progress {
        tag: lock.tag.clone(), done: i, total, shard: shard.into(), shard_bytes: bytes, state: state_str.into(),
    };
    for i in 0..state.shards.len() {
        let (name, sha) = (state.shards[i].name.clone(), state.shards[i].sha.clone());
        if state.shards[i].status == Status::Done { continue; }
        let staged = tag_dir.join(".stage").join(&name);
        if state.shards[i].status != Status::Downloaded {
            emit(&prog("downloading", i, &name, 0));
            let url = format!("{}/{}", lock.base, name);
            if let Some(out) = download(&url, &staged, ctl, |b| emit(&prog("downloading", i, &name, b)))? {
                save_state(&tag_dir, &state)?;
                emit(&prog(if matches!(out, Outcome::Paused) { "paused" } else { "cancelled" }, i, &name, 0));
                return Ok(out);
            }
            emit(&prog("verifying", i, &name, 0));
            if sha256_file(&staged)? != sha {
                // bad partial or corrupt transfer: refetch once from zero
                std::fs::remove_file(&staged).map_err(|e| e.to_string())?;
                emit(&prog("downloading", i, &name, 0));
                let url = format!("{}/{}", lock.base, name);
                if let Some(out) = download(&url, &staged, ctl, |b| emit(&prog("downloading", i, &name, b)))? {
                    save_state(&tag_dir, &state)?;
                    return Ok(out);
                }
                emit(&prog("verifying", i, &name, 0));
                if sha256_file(&staged)? != sha {
                    return Err(format!("chips: {name} sha mismatch after retry"));
                }
            }
            state.shards[i].status = Status::Downloaded;
            save_state(&tag_dir, &state)?;
        }
        emit(&prog("unpacking", i, &name, 0));
        if name.ends_with(".tar") {
            untar_into(&staged, &tag_dir.join("chips"))?;
        } else if name == "chips-manifest.json" {
            std::fs::copy(&staged, tag_dir.join("chips").join("manifest.json")).map_err(|e| e.to_string())?;
        }
        let _ = std::fs::remove_file(&staged);
        state.shards[i].status = Status::Done;
        save_state(&tag_dir, &state)?;
        emit(&prog("downloading", i + 1, "", 0));
    }
    let _ = std::fs::remove_dir(tag_dir.join(".stage"));
    std::fs::write(tag_dir.join(".complete"), b"").map_err(|e| e.to_string())?;
    if store::current_tag(dir).is_none() { store::set_current_tag(dir, &lock.tag)?; }
    *super::ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()) = None;
    emit(&prog("done", total, "", 0));
    Ok(Outcome::Complete)
}
```

Also add to `net.rs` (used above):

```rust
pub fn get_text(url: &str) -> Result<String, String> {
    get(url)?.text().map_err(|e| e.to_string())
}
```

(Make `get` `pub(crate)` or route through `get_text`.) On early returns
(pause/cancel/error), clear `ACTIVE_PACK_TAG` too — wrap the body so every exit path resets
it (a small `struct ActiveTagGuard` with `Drop` is the clean way; implement it).

- [ ] **Step 4: Run** — `cargo test chips::pack` → PASS (state + runner tests).

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/chips
git commit -m "feat(chips): resumable full-pack runner — Range resume, per-shard verify+untar"
```

---

### Task 7: commands, managed state, boot resume

**Files:**
- Create: `src-tauri/src/chips/commands.rs`
- Modify: `src-tauri/src/chips/mod.rs` (`pub mod commands;`)
- Modify: `src-tauri/src/lib.rs` (manage state; extend `invoke_handler`; setup boot-resume)
- Modify: `src-tauri/src/wr/state.rs` (two key consts)

**Interfaces:**
- Consumes: `pack::{run_pack, CTL_*, Outcome, Progress}`, `wr::state::{get_flag, set_flag}`,
  `wr::settings_db`
- Produces (all `#[tauri::command]`, registered in `invoke_handler`):
  - `chips_get_status(app) -> serde_json::Value`:
    `{ "currentTag": string|null, "cachedFiles": number, "cachedBytes": number,
       "packComplete": bool, "packTag": string|null, "packWanted": bool, "packPaused": bool,
       "updateAvailable": bool }` (`updateAvailable` = a `.complete` pack exists whose tag ≠
    current tag). camelCase — Tauri frontend convention.
  - `chips_start_pack(app)` — idempotent; spawns the runner thread, emits `chips-progress`
    events (payload = `Progress`), sets `chips_pack_wanted=1`, clears `chips_pack_paused`
  - `chips_pause_pack(app)` — sets ctl PAUSE + `chips_pack_paused=1`
  - `chips_cancel_pack(app)` — sets ctl CANCEL, deletes the pack tag's `.stage/` +
    `.pack-state.json`, clears both flags
  - `chips_delete_cache(app)` — cancels any run, deletes `<app-data>/chips` entirely
  - `chips::boot_resume(app)` — called from `setup()`: if `chips_pack_wanted && !chips_pack_paused`
    and no `.complete` under the current-lock tag, start the runner quietly
- Settings consts (in `wr/state.rs` beside the others):
  `pub const SETTING_CHIPS_PACK_WANTED: &str = "chips_pack_wanted";`
  `pub const SETTING_CHIPS_PACK_PAUSED: &str = "chips_pack_paused";`

- [ ] **Step 1: Implement `commands.rs`** (thin glue over tested modules — no new unit
tests here; the testable logic all lives in Tasks 2/5/6. `cached_stats` is the one pure
helper — test it):

```rust
//! Tauri commands + runner thread management. Logic lives in store/net/pack (tested);
//! this file is glue: settings flags, thread spawn, event emit.

use super::{chips_root, pack, store, ACTIVE_PACK_TAG};
use crate::wr;
use std::path::Path;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};

pub struct ChipsJob { pub ctl: Arc<AtomicU8> }
pub struct ChipsState(pub Mutex<Option<ChipsJob>>);

/// (file count, byte total) under every tag dir. Walks — called on settings open only.
pub fn cached_stats(dir: &Path) -> (u64, u64) {
    fn walk(p: &Path, acc: &mut (u64, u64)) {
        let Ok(rd) = std::fs::read_dir(p) else { return };
        for e in rd.flatten() {
            let Ok(md) = e.metadata() else { continue };
            if md.is_dir() { walk(&e.path(), acc); }
            else { acc.0 += 1; acc.1 += md.len(); }
        }
    }
    let mut acc = (0, 0);
    walk(dir, &mut acc);
    acc
}

fn complete_pack_tag(dir: &Path) -> Option<String> {
    let rd = std::fs::read_dir(dir).ok()?;
    rd.flatten().find_map(|e| {
        let n = e.file_name().to_str()?.to_string();
        (store::valid_tag(&n) && e.path().join(".complete").exists()).then_some(n)
    })
}

#[tauri::command]
pub fn chips_get_status(app: tauri::AppHandle) -> serde_json::Value {
    let dir = chips_root(&app);
    let (files, bytes) = cached_stats(&dir);
    let current = store::current_tag(&dir);
    let pack_tag = complete_pack_tag(&dir);
    let (wanted, paused) = wr::settings_db(&app).map(|c| (
        wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED),
        wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED),
    )).unwrap_or((false, false));
    serde_json::json!({
        "currentTag": current, "cachedFiles": files, "cachedBytes": bytes,
        "packComplete": pack_tag.is_some(), "packTag": pack_tag,
        "packWanted": wanted, "packPaused": paused,
        "updateAvailable": matches!((&pack_tag, &current), (Some(p), Some(c)) if p != c),
    })
}

fn spawn_runner(app: tauri::AppHandle, state: &ChipsState) {
    let mut guard = state.0.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(j) = guard.as_ref() {
        if j.ctl.load(Ordering::SeqCst) == pack::CTL_RUN { return; } // already running
    }
    let ctl = Arc::new(AtomicU8::new(pack::CTL_RUN));
    *guard = Some(ChipsJob { ctl: ctl.clone() });
    let dir = chips_root(&app);
    std::thread::spawn(move || {
        use tauri::Emitter;
        let emit_app = app.clone();
        let emit = move |p: &pack::Progress| { let _ = emit_app.emit("chips-progress", p); };
        match pack::run_pack(&dir, &ctl, &emit) {
            Ok(pack::Outcome::Complete) => log::info!("[chips] pack complete"),
            Ok(_) => log::info!("[chips] pack interrupted (pause/cancel)"),
            Err(e) => {
                log::error!("[chips] pack failed: {e}");
                let _ = app.emit("chips-progress", serde_json::json!({
                    "tag": "", "done": 0, "total": 0, "shard": "", "shard_bytes": 0,
                    "state": "error", "error": e,
                }));
            }
        }
    });
}

#[tauri::command]
pub fn chips_start_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED, true);
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, false);
    }
    spawn_runner(app, &state);
}

#[tauri::command]
pub fn chips_pause_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Some(j) = state.0.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        j.ctl.store(pack::CTL_PAUSE, Ordering::SeqCst);
    }
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, true);
    }
}

#[tauri::command]
pub fn chips_cancel_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Some(j) = state.0.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        j.ctl.store(pack::CTL_CANCEL, Ordering::SeqCst);
    }
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED, false);
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, false);
    }
    let dir = chips_root(&app);
    // staging of whichever tag the runner was on; sweep every tag's leftovers
    if let Ok(rd) = std::fs::read_dir(&dir) {
        for e in rd.flatten() {
            if e.file_name().to_str().map(store::valid_tag).unwrap_or(false) {
                let _ = std::fs::remove_dir_all(e.path().join(".stage"));
                let _ = std::fs::remove_file(e.path().join(".pack-state.json"));
            }
        }
    }
}

#[tauri::command]
pub fn chips_delete_cache(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    chips_cancel_pack(app.clone(), state);
    let _ = std::fs::remove_dir_all(chips_root(&app));
}

/// setup() hook: resume a wanted, unpaused, incomplete pack quietly. A complete pack on
/// the CURRENT tag means there is nothing to do; a complete pack on a stale tag does NOT
/// auto-redownload (spec: update is an explicit button) — so bail whenever any complete
/// pack exists.
pub fn boot_resume(app: &tauri::AppHandle) {
    use tauri::Manager;
    let Ok(c) = wr::settings_db(app) else { return };
    if !wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED) { return; }
    if wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED) { return; }
    if complete_pack_tag(&chips_root(app)).is_some() { return; }
    let state = app.state::<ChipsState>();
    spawn_runner(app.clone(), &state);
}
```

Add a `cached_stats` unit test (temp dir with two nested files → `(2, total)`).

- [ ] **Step 2: Wire into `lib.rs`** — all additive:
  - `app.manage(chips::commands::ChipsState(Mutex::new(None)));` in `setup()` (next to the
    other `app.manage` calls), then `chips::commands::boot_resume(app.handle());` after the
    tray setup.
  - Append to `invoke_handler`: `chips::commands::chips_get_status, chips::commands::chips_start_pack, chips::commands::chips_pause_pack, chips::commands::chips_cancel_pack, chips::commands::chips_delete_cache`.
  - Add the two consts to `wr/state.rs`.

- [ ] **Step 3: Run** — `cargo test` (whole suite) + `cargo build`. Expected: PASS/compiles.

- [ ] **Step 4: Commit**

```bash
git add src-tauri/src
git commit -m "feat(chips): pack commands, chips-progress events, boot resume"
```

---

### Task 8: Settings "Chips" tab

**Files:**
- Create: `src/components/ChipsSettings.svelte`, `src/lib/chipsSettings.js`
- Test: `src/lib/chipsSettings.test.js`
- Modify: `src/App.svelte:543-549` (`RERUN_STEPS` + `STEP_LABELS`)
- Modify: `src/components/SettingsModal.svelte` (import + `{:else if wizardStep === "chips"}`)

**Interfaces:**
- Consumes: `chips_get_status` / `chips_start_pack` / `chips_pause_pack` / `chips_cancel_pack`
  / `chips_delete_cache` commands; `chips-progress` events (Task 7 payload shape, note
  `shard_bytes` stays snake_case inside the Progress payload)
- Produces: `chipsSettings.js` pure helpers:
  - `fmtBytes(n) -> "0 B" | "512 KB" | "1.2 MB" | "6.23 GB"` (≥100 shows 0 decimals, ≥10 one, else two)
  - `packLabel(status) -> string` — `"Download full pack (6.3 GB)"` | `"Downloading · shard
    12/51"` | `"Paused · shard 12/51"` | `"Installed (chips-v1)"` | `"Pack update available
    (6.3 GB)"` (from `{packComplete, packWanted, packPaused, updateAvailable}` + last progress)
  - `progressFrac(progress) -> number` — `done/total` clamped 0..1 (`null` when total is 0)

- [ ] **Step 1: Write failing tests** (`src/lib/chipsSettings.test.js`, vitest style as the
sibling `*.test.js` files):

```js
import { describe, it, expect } from "vitest";
import { fmtBytes, packLabel, progressFrac } from "./chipsSettings.js";

describe("fmtBytes", () => {
  it("scales units", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(6_230_000_000)).toBe("6.23 GB");
    expect(fmtBytes(512 * 1024)).toBe("512 KB");
  });
});

describe("packLabel", () => {
  const base = { packComplete: false, packWanted: false, packPaused: false, updateAvailable: false };
  it("idle offer", () => expect(packLabel(base, null)).toBe("Download full pack (6.3 GB)"));
  it("downloading with progress", () =>
    expect(packLabel({ ...base, packWanted: true }, { done: 11, total: 51, state: "downloading" }))
      .toBe("Downloading · shard 12/51"));
  it("paused", () =>
    expect(packLabel({ ...base, packWanted: true, packPaused: true }, { done: 11, total: 51 }))
      .toBe("Paused · shard 12/51"));
  it("installed", () =>
    expect(packLabel({ ...base, packComplete: true, packTag: "chips-v1" }, null)).toBe("Installed (chips-v1)"));
  it("update available", () =>
    expect(packLabel({ ...base, packComplete: true, updateAvailable: true }, null))
      .toBe("Pack update available (6.3 GB)"));
});

describe("progressFrac", () => {
  it("fractions and clamps", () => {
    expect(progressFrac({ done: 17, total: 51 })).toBeCloseTo(1 / 3);
    expect(progressFrac({ done: 0, total: 0 })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/lib/chipsSettings.test.js` → FAIL.

- [ ] **Step 3: Implement `src/lib/chipsSettings.js`**

```js
// Pure display helpers for the Chips settings tab (ChipsSettings.svelte owns IPC/DOM).

export function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  const d = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${Number(v.toFixed(d))} ${units[i]}`;
}

const OFFER = "Download full pack (6.3 GB)";

export function packLabel(status, progress) {
  const shard = progress && progress.total ? ` · shard ${Math.min(progress.done + 1, progress.total)}/${progress.total}` : "";
  if (status.packPaused && status.packWanted) return `Paused${shard}`;
  if (status.packWanted && !status.packComplete) return `Downloading${shard}`;
  if (status.packComplete && status.updateAvailable) return `Pack update available (6.3 GB)`;
  if (status.packComplete) return `Installed (${status.packTag})`;
  return OFFER;
}

export function progressFrac(progress) {
  if (!progress || !progress.total) return null;
  return Math.min(1, Math.max(0, progress.done / progress.total));
}
```

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Build `ChipsSettings.svelte`** (follow the tab idioms in
`SettingsModal.svelte` — `.discord-section` boxes, `.btn-primary`/`.btn-sm`, `.discord-note`):

```svelte
<script>
  // Chips tab: on-demand cache stats + opt-in full-pack download (spec 2026-07-19).
  import { onMount, onDestroy } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { fmtBytes, packLabel, progressFrac } from "../lib/chipsSettings.js";

  let status = null, progress = null, unlisten = null, err = "";

  async function refresh() {
    try { status = await invoke("chips_get_status"); } catch (e) { err = String(e); }
  }
  onMount(async () => {
    refresh();
    unlisten = await listen("chips-progress", (ev) => {
      progress = ev.payload;
      if (progress.state === "error") err = progress.error || "download failed";
      if (progress.state === "done" || progress.state === "error") refresh();
    });
  });
  onDestroy(() => unlisten && unlisten());

  const start  = () => { err = ""; invoke("chips_start_pack").then(refresh); };
  const pause  = () => invoke("chips_pause_pack").then(refresh);
  const cancel = () => invoke("chips_cancel_pack").then(() => { progress = null; refresh(); });
  const nuke   = () => invoke("chips_delete_cache").then(() => { progress = null; refresh(); });

  $: downloading = status?.packWanted && !status?.packComplete && !status?.packPaused;
  $: frac = progressFrac(progress);
</script>

<div class="step-centred">
  <h2>Chips</h2>
  <p>Animated character/kart chips on the player cards. By default they download on demand
     and stay cached, so anything seen once is instant. The full pack makes every chip
     instant, even offline.</p>

  <div class="discord-section">
    <h3 class="discord-heading">Cache</h3>
    <div class="kvrow"><span>Cached</span>
      <span>{status ? `${status.cachedFiles} files · ${fmtBytes(status.cachedBytes)}` : "…"}</span></div>
    <div class="kvrow"><span>Pack version</span><span>{status?.currentTag ?? "not fetched yet"}</span></div>
    <button class="btn-sm" on:click={nuke}>Delete chip cache</button>
    <p class="discord-note">Also covered by app-data deletion. Chips re-download on demand.</p>
  </div>

  <div class="discord-section">
    <h3 class="discord-heading">Full pack</h3>
    <div class="kvrow"><span>{status ? packLabel(status, progress) : "…"}</span></div>
    {#if frac != null && downloading}
      <div class="bar"><div class="fill" style="width:{frac * 100}%"></div></div>
      <div class="discord-note">{progress.shard} · {fmtBytes(progress.shard_bytes)}</div>
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
    <div class="btns">
      {#if downloading}
        <button class="btn-sm" on:click={pause}>Pause</button>
        <button class="btn-sm" on:click={cancel}>Cancel</button>
      {:else if status?.packPaused}
        <button class="btn-primary" on:click={start}>Resume</button>
        <button class="btn-sm" on:click={cancel}>Cancel</button>
      {:else}
        <button class="btn-primary" on:click={start}>
          {status?.updateAvailable ? "Update pack" : "Download full pack (6.3 GB)"}</button>
      {/if}
    </div>
    <p class="discord-note">Resumes where it left off after pause or app restart.
       Nothing re-downloads.</p>
  </div>
</div>

<style>
  .kvrow { display: flex; justify-content: space-between; font-size: .72rem; color: var(--tx); }
  .bar { height: 6px; background: var(--panel); border: 1px solid var(--bd); border-radius: 3px; overflow: hidden; }
  .fill { height: 100%; background: var(--accent); transition: width .3s; }
  .btns { display: flex; gap: .5rem; margin-top: .2rem; }
  .err { font-size: .68rem; color: var(--bad, #e5484d); }
  /* .step-centred/.discord-* come from the parent modal's scope — duplicate the handful
     used here into this component's scope (Svelte styles don't cascade): */
  .step-centred { max-width: 560px; margin: 0 auto; padding: .5rem 0; display: flex; flex-direction: column; gap: .75rem; }
  .step-centred h2 { color: var(--tx); font-size: .95rem; font-weight: 600; }
  .step-centred p { font-size: .76rem; color: var(--tx-mut); line-height: 1.6; }
  .discord-section { display: flex; flex-direction: column; gap: .35rem; padding: .55rem .7rem;
    border-radius: var(--r); background: var(--panel-2); border: 1px solid var(--bd); }
  .discord-heading { font-size: .63rem; color: var(--tx-mut); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; margin: 0; }
  .discord-note { font-size: .66rem; color: var(--tx-dim); margin: .1rem 0 0; line-height: 1.5; }
  .btn-primary { background: var(--accent-bg); color: var(--tx); border: 1px solid var(--accent); border-radius: var(--r);
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem; cursor: pointer; }
  .btn-sm { background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem; cursor: pointer; }
</style>
```

- [ ] **Step 6: Wire the tab.** `src/App.svelte`: `RERUN_STEPS` →
`["language", "camera", "chips", "discord", "sync", "trails", "screenshots"]`; `STEP_LABELS`
add `chips: "Chips"`. `SettingsModal.svelte`: `import ChipsSettings from "./ChipsSettings.svelte";`
and before the final `{/if}`:

```svelte
{:else if wizardStep === "chips"}
  <ChipsSettings />
```

- [ ] **Step 7: Run** — `npx vitest run` (frontend suite) + `npm run tauri dev` → open
Settings → Chips tab renders, status populates (or shows the not-fetched-yet copy offline).

- [ ] **Step 8: Commit**

```bash
git add src/components/ChipsSettings.svelte src/lib/chipsSettings.js src/lib/chipsSettings.test.js src/App.svelte src/components/SettingsModal.svelte
git commit -m "feat(chips): settings Chips tab — cache stats, full-pack download UI"
```

---

### Task 9: Fake-pack rehearsal + docs

**Files:**
- Create: `scripts/make_fake_chip_pack.py`
- Modify: `CLAUDE.md` (Key Data Files note), `docs/config-reference.md` if it lists settings
  keys (add the two chips flags)

**Interfaces:**
- Produces: `python scripts/make_fake_chip_pack.py <outdir>` builds: `manifest.json` (2 tiny
  combos, base `/chips/anim/chips-v1/`), `chips-v1/` content, two `chips-*.tar` shards +
  `chips-manifest.json` + a `lock` file with real sha256s — laid out so
  `python -m http.server` in `<outdir>` serves `/manifest.json`, `/lock`, `/chips-a.tar`, …
  and `PBENGUIN_CHIPS_URL=http://127.0.0.1:8000` exercises BOTH paths end-to-end.

- [ ] **Step 1: Write the script** (stdlib only: `tarfile`, `hashlib`, `json`, tiny webp
bytes can be any placeholder file content — the store doesn't decode):

```python
"""Build a miniature fake chip pack + lock for pbenguin rehearsal.

Usage:  python scripts/make_fake_chip_pack.py temp/fakepack
        cd temp/fakepack && python -m http.server 8000
        PBENGUIN_CHIPS_URL=http://127.0.0.1:8000 npm run tauri dev
"""
import hashlib, json, os, sys, tarfile

out = sys.argv[1]
os.makedirs(out, exist_ok=True)

combos = {
    "mario__base__standard_kart": {"kart": True, "idle_resume": 3,
        "anims": {"idle": {"frames": 8, "cols": 3, "rows": 3},
                  "spawn": {"frames": 4, "cols": 2, "rows": 2},
                  "flourish": {"frames": 4, "cols": 2, "rows": 2}}},
    "luigi__base": {"kart": False, "idle_resume": 0,
        "anims": {"idle": {"frames": 8, "cols": 3, "rows": 3},
                  "flourish": {"frames": 4, "cols": 2, "rows": 2}}},
}
manifest = {"version": 1, "scale": 0.2, "fps": 60, "fw": 205, "fh": 216,
            "base": "/chips/anim/chips-v1/", "combos": combos}

def tar_of(name, files):
    path = os.path.join(out, name)
    with tarfile.open(path, "w") as t:
        for fname, data in files:
            tmp = os.path.join(out, ".tmp")
            with open(tmp, "wb") as f: f.write(data)
            t.add(tmp, arcname=fname)
    os.remove(os.path.join(out, ".tmp"))
    return path

def fake_files(combo):
    files = []
    for anim in combos[combo]["anims"]:
        files.append((f"{combo}__{anim}.webp", os.urandom(2048)))
        for k in range(4):
            files.append((f"{combo}__{anim}__sil_k{k}.png", os.urandom(256)))
    return files

shards = [tar_of("chips-mario.tar", fake_files("mario__base__standard_kart")),
          tar_of("chips-luigi.tar", fake_files("luigi__base"))]
mpath = os.path.join(out, "chips-manifest.json")
with open(mpath, "w") as f: json.dump(manifest, f)

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()

lock = ["tag chips-v1", "base http://127.0.0.1:8000"]
for p in [mpath] + shards:
    lock.append(f"{sha(p)}  {os.path.basename(p)}")
with open(os.path.join(out, "lock"), "w") as f: f.write("\n".join(lock) + "\n")

# on-demand path needs /manifest.json + /chips-v1/<file> — mirror the site layout
with open(os.path.join(out, "manifest.json"), "w") as f: json.dump(manifest, f)
os.makedirs(os.path.join(out, "chips-v1"), exist_ok=True)
for combo in combos:
    for fname, _ in fake_files(combo):
        pass  # sheet bytes differ per call; re-extract from the shard instead:
for shard in shards:
    with tarfile.open(shard) as t: t.extractall(os.path.join(out, "chips-v1"))
print(f"fake pack in {out} — serve with: cd {out} && python -m http.server 8000")
```

NOTE the ordering bug hiding in a naive version: `fake_files` uses `os.urandom`, so the tar
content and any separately-written loose files WOULD differ. The script above therefore
extracts the `chips-v1/` on-demand tree FROM the shards, guaranteeing the on-demand bytes,
the shard bytes, and the lock shas all agree. Keep that property.

- [ ] **Step 2: Rehearse** (manual, dev machine):

```
python scripts/make_fake_chip_pack.py temp/fakepack
cd temp/fakepack && python -m http.server 8000
# separate shell:
PBENGUIN_CHIPS_URL=http://127.0.0.1:8000 npm run tauri dev
```

Checklist (all against the fake server):
1. Devtools: `fetch("http://chips.localhost/manifest.json")` → base rewritten, combos listed.
2. Fetch a sheet URL twice → file appears under `%APPDATA%\mkw-tracker\chips\chips-v1\chips\`.
3. Settings → Chips → Download full pack → completes; `.complete` present; status "Installed".
4. Delete cache → start download → Pause mid-flight → kill the app → relaunch → boot-resume
   continues from the partial (watch `.stage/` size, confirm it grows, not resets).
5. Cancel → `.stage/`/`.pack-state.json` gone, flags cleared.

- [ ] **Step 3: Docs.** CLAUDE.md "Key Data Files": add one row
`| %APPDATA%\mkw-tracker\chips\ | pbenguin chip cache (on-demand + full pack; spec 2026-07-19) |`.
Root CLAUDE.md chip-pack paragraph: append "pbenguin caches chips per-tag under its app data
dir via the `chips://` protocol; full-pack download in Settings → Chips."

- [ ] **Step 4: Commit**

```bash
git add scripts/make_fake_chip_pack.py CLAUDE.md docs/config-reference.md
git commit -m "feat(chips): fake-pack rehearsal script + docs"
```

---

## Final verification (whole plan)

- [ ] `cd src-tauri && cargo test` — all green.
- [ ] `npx vitest run` (repo root, frontend) and `cd web && npx vitest run` — all green.
- [ ] Full rehearsal checklist from Task 9 passes.
- [ ] `git log --oneline main..HEAD` shows one commit per task, no stray files
  (`git status` clean; nothing under `temp/` committed).
