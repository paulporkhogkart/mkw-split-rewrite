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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serves_cached_file_without_network() {
        let t = crate::chips::testutil::TmpDir::new();
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
        let t = crate::chips::testutil::TmpDir::new();
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
