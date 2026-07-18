//! The in-flight phase, for the tray tooltip. service.rs writes it at the download and
//! engine boundaries; the tray reads it. A tiny global rather than a channel because the
//! producer (run_job) has no handle to the tray and must not depend on it.

use std::sync::Mutex;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PhaseKind { Downloading, Processing }

/// Read by `get` below, which has no non-test caller until Task 7's tray lands and
/// reads it for the tooltip (this module's Interfaces note in the Task 3 brief).
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct Phase {
    pub kind: PhaseKind,
    pub course_slug: String,
}

static PHASE: Mutex<Option<Phase>> = Mutex::new(None);

pub fn set(p: Option<Phase>) {
    *PHASE.lock().unwrap_or_else(|e| e.into_inner()) = p;
}

/// No non-test caller until Task 7's tray reads the tooltip phase.
#[allow(dead_code)]
pub fn get() -> Option<Phase> {
    PHASE.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phase_roundtrips_and_clears() {
        set(Some(Phase { kind: PhaseKind::Downloading, course_slug: "dk_spaceport".into() }));
        let p = get().expect("phase must be readable while set");
        assert!(matches!(p.kind, PhaseKind::Downloading));
        assert_eq!(p.course_slug, "dk_spaceport");
        set(Some(Phase { kind: PhaseKind::Processing, course_slug: "dk_spaceport".into() }));
        assert!(matches!(get().unwrap().kind, PhaseKind::Processing));
        set(None);
        assert!(get().is_none(), "a finished job must clear the phase");
    }
}
