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

pub fn get_text(url: &str) -> Result<String, String> {
    get(url)?.text().map_err(|e| e.to_string())
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chips::testutil::{env_lock, TestServer, TmpDir};

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

    #[test]
    fn refresh_fetches_persists_and_evicts() {
        let _g = env_lock();
        let t = TmpDir::new();
        let dir = t.path().to_path_buf();
        std::fs::create_dir_all(dir.join("chips-v0/chips")).unwrap(); // stale tag to evict
        let srv = TestServer::spawn(|path, _range| {
            assert!(path.ends_with("/manifest.json"), "unexpected path {path}");
            (200, br#"{"base":"/chips/anim/chips-v1/","combos":{}}"#.to_vec())
        });
        std::env::set_var("PBENGUIN_CHIPS_URL", &srv.base);
        let out = refresh_manifest(&dir, None).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(out.contains("chips.localhost/chips-v1/"));
        assert_eq!(store::current_tag(&dir).as_deref(), Some("chips-v1"));
        assert!(dir.join("chips-v1/chips/manifest.json").exists(), "raw manifest persisted");
        assert!(!dir.join("chips-v0").exists(), "stale tag evicted on adoption");
    }

    #[test]
    fn refresh_offline_serves_cached_copy() {
        let _g = env_lock();
        let t = TmpDir::new();
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
        let _g = env_lock();
        let t = TmpDir::new();
        let dir = t.path().to_path_buf();
        let srv = TestServer::spawn(|path, _range| {
            assert_eq!(path, "/chips-v1/a__idle.webp");
            (200, vec![7u8; 32])
        });
        std::env::set_var("PBENGUIN_CHIPS_URL", &srv.base);
        let bytes = fetch_file(&dir, "chips-v1", "a__idle.webp").unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert_eq!(bytes.len(), 32);
        assert_eq!(std::fs::read(dir.join("chips-v1/chips/a__idle.webp")).unwrap(), bytes);
    }

    #[test]
    fn get_text_fetches_body() {
        let srv = TestServer::spawn(|_path, _range| (200, b"hello world".to_vec()));
        let out = get_text(&format!("{}/x", srv.base)).unwrap();
        assert_eq!(out, "hello world");
    }
}
