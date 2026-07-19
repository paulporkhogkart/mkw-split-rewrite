//! Full-pack download bookkeeping: .pack-state.json + sha + untar. The runner (Task 6)
//! drives these; everything here is synchronous and unit-testable.

use super::store::{self, Lock};
use serde::{Deserialize, Serialize};
use std::io::Read;
use std::path::Path;

#[derive(Clone, Serialize, Deserialize, PartialEq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Pending,
    Downloaded,
    Done,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Shard {
    pub name: String,
    pub sha: String,
    pub status: Status,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct PackState {
    pub tag: String,
    pub base: String,
    pub shards: Vec<Shard>,
}

const STATE_FILE: &str = ".pack-state.json";

pub fn load_state(tag_dir: &Path) -> Option<PackState> {
    serde_json::from_str(&std::fs::read_to_string(tag_dir.join(STATE_FILE)).ok()?).ok()
}

pub fn save_state(tag_dir: &Path, s: &PackState) -> Result<(), String> {
    store::write_atomic(
        &tag_dir.join(STATE_FILE),
        serde_json::to_string_pretty(s)
            .map_err(|e| e.to_string())?
            .as_bytes(),
    )
    .map_err(|e| e.to_string())
}

/// New tag ⇒ everything Pending. Same tag ⇒ a shard stays Done only if its sha still
/// matches the lock (spec: "same tag but changed shas → only changed shards redo").
pub fn reconcile(lock: &Lock, prev: Option<PackState>) -> PackState {
    let prev = prev.filter(|p| p.tag == lock.tag);
    let shards = lock
        .files
        .iter()
        .map(|(sha, name)| {
            let status = prev
                .as_ref()
                .and_then(|p| p.shards.iter().find(|s| &s.name == name))
                .filter(|s| &s.sha == sha && s.status == Status::Done)
                .map(|_| Status::Done)
                .unwrap_or(Status::Pending);
            Shard {
                name: name.clone(),
                sha: sha.clone(),
                status,
            }
        })
        .collect();
    PackState {
        tag: lock.tag.clone(),
        base: lock.base.clone(),
        shards,
    }
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut h = Sha256::new();
    let mut buf = [0u8; 65536];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
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
    ar.unpack(dest)
        .map_err(|e| format!("untar {tar_path:?}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lock(tag: &str, files: &[(&str, &str)]) -> Lock {
        Lock {
            tag: tag.into(),
            base: "https://x/dl".into(),
            files: files
                .iter()
                .map(|(s, n)| (s.to_string(), n.to_string()))
                .collect(),
        }
    }

    #[test]
    fn reconcile_fresh_and_tag_change() {
        let l = lock("chips-v1", &[("a".repeat(64).leak(), "one.tar")]);
        let s = reconcile(&l, None);
        assert!(matches!(s.shards[0].status, Status::Pending));
        let prev = PackState {
            tag: "chips-v0".into(),
            base: s.base.clone(),
            shards: s.shards.clone(),
        };
        let s2 = reconcile(&l, Some(prev));
        assert!(
            matches!(s2.shards[0].status, Status::Pending),
            "tag change restarts"
        );
        assert_eq!(s2.tag, "chips-v1");
    }

    #[test]
    fn reconcile_same_tag_keeps_done_only_on_matching_sha() {
        let sha_a = "a".repeat(64);
        let sha_b = "b".repeat(64);
        let l = lock("chips-v1", &[(&sha_a, "one.tar"), (&sha_b, "two.tar")]);
        let mut prev = reconcile(&l, None);
        prev.shards[0].status = Status::Done;
        prev.shards[1].status = Status::Done;
        prev.shards[1].sha = "c".repeat(64); // sha changed upstream
        let s = reconcile(&l, Some(prev));
        assert!(matches!(s.shards[0].status, Status::Done));
        assert!(matches!(s.shards[1].status, Status::Pending));
    }

    #[test]
    fn state_roundtrips_via_disk() {
        let t = crate::chips::testutil::TmpDir::new();
        let l = lock("chips-v1", &[("d".repeat(64).leak(), "one.tar")]);
        let s = reconcile(&l, None);
        save_state(t.path(), &s).unwrap();
        let r = load_state(t.path()).unwrap();
        assert_eq!(r.tag, "chips-v1");
        assert_eq!(r.shards.len(), 1);
    }

    #[test]
    fn sha256_streams_correctly() {
        let t = crate::chips::testutil::TmpDir::new();
        let p = t.path().join("f");
        std::fs::write(&p, b"abc").unwrap();
        assert_eq!(
            sha256_file(&p).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn untar_extracts_and_rejects_traversal() {
        let t = crate::chips::testutil::TmpDir::new();
        let tarp = t.path().join("x.tar");
        {
            // build a tar with one good entry
            let f = std::fs::File::create(&tarp).unwrap();
            let mut b = tar::Builder::new(f);
            let data = b"hello";
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64);
            h.set_mode(0o644);
            h.set_cksum();
            b.append_data(&mut h, "a__idle.webp", &data[..])
                .unwrap();
            b.finish().unwrap();
        }
        let dest = t.path().join("out");
        untar_into(&tarp, &dest).unwrap();
        assert_eq!(
            std::fs::read(dest.join("a__idle.webp")).unwrap(),
            b"hello"
        );
        untar_into(&tarp, &dest).unwrap(); // overwrite-idempotent (resume re-untars)
    }
}
