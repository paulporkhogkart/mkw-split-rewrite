//! Chip sprite-sheet cache + full-pack download (spec 2026-07-19).
//! store = disk layout/lock/tags (pure). net = site fetch + manifest rewrite.
//! pack = full-pack downloader. commands = tauri commands + protocol glue.

pub mod store;

#[cfg(test)]
pub mod testutil;
