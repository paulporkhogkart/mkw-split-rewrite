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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chips::testutil::TmpDir;

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
        let t = TmpDir::new();
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
        let t = TmpDir::new();
        let p = t.path().join("f.json");
        write_atomic(&p, b"one").unwrap();
        write_atomic(&p, b"two").unwrap();
        assert_eq!(std::fs::read(&p).unwrap(), b"two");
        assert!(!t.path().join("f.json.part").exists());
    }
}
