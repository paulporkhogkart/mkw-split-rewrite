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
