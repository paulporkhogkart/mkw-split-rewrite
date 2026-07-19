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
    // tar-rs 0.4.46 `unpack` silently skips or errors on paths escaping dest (proven by test
    // `untar_extracts_and_rejects_traversal`); keep it (no per-entry loop needed).
    ar.unpack(dest)
        .map_err(|e| format!("untar {tar_path:?}: {e}"))
}

use std::sync::atomic::{AtomicU8, Ordering};

pub const CTL_RUN: u8 = 0;
pub const CTL_PAUSE: u8 = 1;
pub const CTL_CANCEL: u8 = 2;

pub enum Outcome {
    Complete,
    Paused,
    Cancelled,
}

#[derive(Clone, Serialize)]
pub struct Progress {
    pub tag: String,
    pub done: usize,
    pub total: usize,
    pub shard: String,
    pub shard_bytes: u64,
    pub state: String,
}

fn ctl_state(ctl: &AtomicU8) -> u8 {
    ctl.load(Ordering::SeqCst)
}

/// Clears `ACTIVE_PACK_TAG` on drop — covers every exit path of `run_pack` (early
/// returns on error/pause/cancel as well as the success fall-through) with one guard.
struct ActiveTagGuard;
impl ActiveTagGuard {
    fn new(tag: &str) -> Self {
        *super::ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()) = Some(tag.to_string());
        ActiveTagGuard
    }
}
impl Drop for ActiveTagGuard {
    fn drop(&mut self) {
        *super::ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }
}

/// Download `url` to `dest`, resuming from an existing partial via Range. Returns
/// Some(outcome) when interrupted, None when byte-complete.
fn download(
    url: &str,
    dest: &Path,
    ctl: &AtomicU8,
    mut tick: impl FnMut(u64),
) -> Result<Option<Outcome>, String> {
    use std::io::{Seek, SeekFrom, Write};
    if let Some(p) = dest.parent() {
        std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    let mut have = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0);
    let client = reqwest::blocking::Client::new();
    let mut req = client
        .get(url)
        .timeout(std::time::Duration::from_secs(24 * 3600));
    if have > 0 {
        req = req.header("Range", format!("bytes={have}-"));
    }
    let mut resp = req
        .send()
        .and_then(|r| {
            // 416 = our partial is already the full file (server has nothing past `have`)
            if r.status().as_u16() == 416 {
                Ok(r)
            } else {
                r.error_for_status()
            }
        })
        .map_err(|e| format!("chips: GET {url}: {e}"))?;
    match resp.status().as_u16() {
        416 => return Ok(None),
        206 => {}
        200 => have = 0, // server ignored Range: restart from zero
        s => return Err(format!("chips: GET {url}: HTTP {s}")),
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .open(dest)
        .map_err(|e| e.to_string())?;
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
        if n == 0 {
            break;
        }
        f.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        have += n as u64;
        since_tick += n as u64;
        if since_tick >= 1_000_000 {
            since_tick = 0;
            tick(have);
        }
    }
    tick(have);
    // Re-check right after the final flush: for shards smaller than the ~1MB tick
    // interval, this is the *only* tick that ever fires with bytes>0, so a pause/cancel
    // requested in response to it must still be honored before we call the shard
    // byte-complete and move on to verify/untar.
    match ctl_state(ctl) {
        CTL_PAUSE => return Ok(Some(Outcome::Paused)),
        CTL_CANCEL => return Ok(Some(Outcome::Cancelled)),
        _ => {}
    }
    Ok(None)
}

pub fn run_pack(
    dir: &Path,
    ctl: &AtomicU8,
    emit: &(dyn Fn(&Progress) + Send + Sync),
) -> Result<Outcome, String> {
    let lock_text = super::net::get_text(&format!("{}/lock", super::net::site_base()))?;
    let lock = store::parse_lock(&lock_text)?;
    let tag_dir = dir.join(&lock.tag);
    std::fs::create_dir_all(tag_dir.join("chips")).map_err(|e| e.to_string())?;
    // Anything that isn't this lock's tag is stale staging — but never touch the
    // current on-demand tag; refresh_manifest owns that eviction.
    let _guard = ActiveTagGuard::new(&lock.tag);
    let mut state = reconcile(&lock, load_state(&tag_dir));
    save_state(&tag_dir, &state)?;
    let total = state.shards.len();
    let pending = state.shards.iter().filter(|s| s.status != Status::Done).count();
    if pending > 0 {
        let free = fs2::available_space(dir).unwrap_or(u64::MAX);
        if free < 8 * 1024 * 1024 * 1024 && pending == total {
            return Err(format!(
                "chips: need ~8 GB free, have {} GB",
                free / (1024 * 1024 * 1024)
            ));
        }
    }
    let prog = |state_str: &str, i: usize, shard: &str, bytes: u64| Progress {
        tag: lock.tag.clone(),
        done: i,
        total,
        shard: shard.into(),
        shard_bytes: bytes,
        state: state_str.into(),
    };
    for i in 0..state.shards.len() {
        let (name, sha) = (state.shards[i].name.clone(), state.shards[i].sha.clone());
        if state.shards[i].status == Status::Done {
            continue;
        }
        let staged = tag_dir.join(".stage").join(&name);
        if state.shards[i].status != Status::Downloaded {
            emit(&prog("downloading", i, &name, 0));
            let url = format!("{}/{}", lock.base, name);
            if let Some(out) = download(&url, &staged, ctl, |b| emit(&prog("downloading", i, &name, b)))? {
                save_state(&tag_dir, &state)?;
                emit(&prog(
                    if matches!(out, Outcome::Paused) { "paused" } else { "cancelled" },
                    i,
                    &name,
                    0,
                ));
                return Ok(out);
            }
            emit(&prog("verifying", i, &name, 0));
            if sha256_file(&staged)? != sha {
                // bad partial or corrupt transfer: refetch once from zero
                std::fs::remove_file(&staged).map_err(|e| e.to_string())?;
                emit(&prog("downloading", i, &name, 0));
                let url = format!("{}/{}", lock.base, name);
                if let Some(out) =
                    download(&url, &staged, ctl, |b| emit(&prog("downloading", i, &name, b)))?
                {
                    save_state(&tag_dir, &state)?;
                    emit(&prog(
                        if matches!(out, Outcome::Paused) { "paused" } else { "cancelled" },
                        i,
                        &name,
                        0,
                    ));
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
    if store::current_tag(dir).is_none() {
        store::set_current_tag(dir, &lock.tag)?;
    }
    emit(&prog("done", total, "", 0));
    Ok(Outcome::Complete)
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

        // Second tar: build one with a traversal entry ../escaped.bin.
        // tar-rs Builder::append_data() and set_path() refuse .. paths at the API level.
        // Construct the header manually and write via write_all to bypass safety checks.
        let tarp2 = t.path().join("x2.tar");
        {
            use std::io::Write;
            let mut f = std::fs::File::create(&tarp2).unwrap();
            let data = b"bad";
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64);
            h.set_mode(0o644);
            // Set the path field (first 100 bytes of the header) to the traversal string.
            // This bypasses the set_path() safety check.
            let path = b"../escaped.bin";
            h.as_mut_bytes()[0..path.len()].copy_from_slice(path);
            h.set_cksum();
            // Write header + data + padding directly
            f.write_all(h.as_bytes()).unwrap();
            f.write_all(data).unwrap();
            // Pad to 512-byte boundary
            let padding = (512 - (data.len() % 512)) % 512;
            f.write_all(&vec![0u8; padding]).unwrap();
            // Write end-of-archive marker (two zero blocks)
            f.write_all(&[0u8; 512]).unwrap();
            f.write_all(&[0u8; 512]).unwrap();
        }
        let dest2 = t.path().join("out2");
        // Verify untar_into rejects or skips the traversal entry
        let result = untar_into(&tarp2, &dest2);
        // tar-rs 0.4.46 silently skips entries escaping dest (observed behavior).
        // Verify nothing escaped to the parent directory:
        assert!(!t.path().join("escaped.bin").exists(),
            "traversal entry must not escape to parent dir");
        // Verify the out2 dir either doesn't exist (Err case) or exists but escaped.bin isn't there
        if dest2.exists() {
            assert!(!dest2.join("escaped.bin").exists(),
                "escaped entry must not exist in dest");
        }
        // Observed: tar-rs 0.4.46 unpack() silently skips the traversal entry and returns Ok
        match result {
            Ok(_) => {}, // tar-rs silently skips entries escaping dest
            Err(e) => panic!("untar_into returned error (unexpected): {}", e),
        }
    }
}

#[cfg(test)]
mod runner_tests {
    use super::*;
    use crate::chips::testutil::{TestServer, TmpDir};
    use std::sync::atomic::{AtomicU8, Ordering};
    use std::sync::{Arc, Mutex, OnceLock};

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static ENV: Mutex<()> = Mutex::new(());
        ENV.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// One-shard pack: tar containing a__idle.webp; returns (sha-of-tar, tar bytes).
    fn fixture() -> (String, Vec<u8>) {
        let mut tarbuf = Vec::new();
        {
            let mut b = tar::Builder::new(&mut tarbuf);
            let data = vec![9u8; 300_000]; // big enough to pause mid-flight
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64);
            h.set_mode(0o644);
            h.set_cksum();
            b.append_data(&mut h, "a__idle.webp", &data[..]).unwrap();
            b.finish().unwrap();
        }
        use sha2::{Digest, Sha256};
        let sha = format!("{:x}", Sha256::digest(&tarbuf));
        (sha, tarbuf)
    }

    /// Spawns a TestServer serving `/lock` (base = the server's own address, filled in
    /// lazily once `.base` is known) and `/chips-a.tar` (Range-sliced tar bytes).
    fn spawn_pack_server(sha: String, tarbuf: Vec<u8>) -> TestServer {
        let lock_text: Arc<OnceLock<String>> = Arc::new(OnceLock::new());
        let lt = lock_text.clone();
        let srv = TestServer::spawn(move |path, range| {
            if path == "/lock" {
                let text = lt.get().expect("lock text set post-spawn").clone();
                return (200, text.into_bytes());
            }
            if path == "/chips-a.tar" {
                let from = range.unwrap_or(0) as usize;
                let slice = tarbuf[from.min(tarbuf.len())..].to_vec();
                let status = if from > 0 { 206 } else { 200 };
                return (status, slice);
            }
            (404, Vec::new())
        });
        lock_text
            .set(format!("tag chips-v1\nbase {}\n{sha}  chips-a.tar\n", srv.base))
            .unwrap();
        srv
    }

    #[test]
    fn downloads_verifies_untars_and_completes() {
        let _g = env_lock();
        let (sha, tarbuf) = fixture();
        let srv = spawn_pack_server(sha, tarbuf);
        std::env::set_var("PBENGUIN_CHIPS_URL", &srv.base);
        let t = TmpDir::new();
        let ctl = AtomicU8::new(CTL_RUN);
        let out = run_pack(t.path(), &ctl, &|_p| {}).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(matches!(out, Outcome::Complete));
        let td = t.path().join("chips-v1");
        assert!(td.join(".complete").exists());
        assert!(td.join("chips/a__idle.webp").exists());
        assert!(
            !td.join(".stage/chips-a.tar").exists(),
            "tar deleted after untar"
        );
    }

    #[test]
    fn pause_persists_partial_and_resume_completes_via_range() {
        let _g = env_lock();
        let (sha, tarbuf) = fixture();
        let srv = spawn_pack_server(sha, tarbuf);
        std::env::set_var("PBENGUIN_CHIPS_URL", &srv.base);
        let t = TmpDir::new();
        // pause after the first progress event that reports shard bytes
        let ctl = Arc::new(AtomicU8::new(CTL_RUN));
        let c2 = ctl.clone();
        let out = run_pack(t.path(), &ctl, &move |p| {
            if p.state == "downloading" && p.shard_bytes > 0 {
                c2.store(CTL_PAUSE, Ordering::SeqCst);
            }
        })
        .unwrap();
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
        let (sha, tarbuf) = fixture();
        let srv = spawn_pack_server(sha, tarbuf);
        std::env::set_var("PBENGUIN_CHIPS_URL", &srv.base);
        let t = TmpDir::new();
        // poison a pre-existing partial so the first sha check fails
        let stage = t.path().join("chips-v1/.stage");
        std::fs::create_dir_all(&stage).unwrap();
        std::fs::write(stage.join("chips-a.tar"), b"garbage-longer-than-real? no: shorter").unwrap();
        let ctl = AtomicU8::new(CTL_RUN);
        let out = run_pack(t.path(), &ctl, &|_p| {}).unwrap();
        std::env::remove_var("PBENGUIN_CHIPS_URL");
        assert!(
            matches!(out, Outcome::Complete),
            "bad partial → refetch from zero, still completes"
        );
    }
}
