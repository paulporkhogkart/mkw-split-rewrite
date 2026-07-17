//! The free correctness gate (spec §6.6) + the retry tier lookup (§6.4) — currently a
//! single tier; see `Tier`'s doc for why the escalation tier was removed.

use super::engine::{time_to_ms, Finalized};
use super::WrError;

/// Which source to download for this attempt (spec §6.4 retry tiers).
///
/// A 4K tier (`Downscaled4k`, 2160p60 downscaled to 1080p) was tried and REMOVED
/// 2026-07-15: it was measured to raise the median badge NCC 0.796 -> 0.829 on ONE clean
/// video, but through an ffmpeg lanczos downscale step that was never actually built —
/// this module handed 2160p straight to the engine instead, whose `_norm()` resizes with
/// `cv2.INTER_LINEAR`, which *aliases* on a 2:1 downscale and can be worse than native
/// 1080p, not better. So the tier paid 3.6x the bandwidth to plausibly make things worse,
/// on the strength of a gain that was never even measured through the code path this
/// module runs. Its value on a genuinely marginal video was a hypothesis, not evidence.
/// Deliberately left as a one-variant enum rather than deleted outright — a later plan
/// may re-add a real tier, but only with evidence a specific video needed it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier {
    /// YouTube's native 1080p60. The only source this module downloads.
    Native1080p60,
}

/// Every attempt downloads native 1080p60 — see the `Tier` doc for why the 4K escalation
/// tier was removed rather than fixed. Kept as a function of `attempt` (rather than a
/// constant) so a future real tier can slot back in without changing every call site.
pub fn tier_for(_attempt: i64) -> Tier {
    Tier::Native1080p60
}

/// The free correctness gate: the engine read the time off the video without consulting
/// mkwrs, so an exact match is strong evidence we processed the right video in full.
/// Returns the trail on success.
pub fn verify(f: &Finalized, expected_ms: i64) -> Result<Vec<[f64; 5]>, WrError> {
    // Order matters: with points empty, total_time (if present at all) is not a real
    // reading of anything — reporting TimeMismatch off it would report a mismatch that was
    // never actually measured. NoTrail is the honest diagnosis, and the job stays
    // claimable up to the attempts cap regardless of which of the two this returns.
    if f.points.is_empty() { return Err(WrError::NoTrail); }
    let detected_ms = match f.total_time.as_deref().and_then(time_to_ms) {
        Some(ms) => ms,
        None => return Err(WrError::NoTrail),
    };
    if detected_ms != expected_ms {
        return Err(WrError::TimeMismatch { detected_ms, expected_ms });
    }
    // Fragment floor. The timer (HUD digits) and the trail (minimap) come from
    // independent trackers, so an exact time does NOT prove the trail covers the race —
    // the badge can be lost mid-race and never re-acquired. A wr_trails row is
    // permanently "done", so a stub accepted here is a stub forever. Deliberately AFTER
    // the mismatch check: a fragment of the WRONG video is primarily wrong (terminal on
    // the Pi), only secondarily short.
    let last_t = f.points.last().map(|p| p[0]).unwrap_or(0.0);
    if last_t < 0.8 * expected_ms as f64 {
        return Err(WrError::NoTrail);
    }
    Ok(f.points.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `n` points spread across ~63s of race clock (matching the 1:02.934 the tests
    /// use). The old fixture packed all points into the first n MILLISECONDS, which a
    /// duration floor rightly rejects — a real trail spans the race.
    fn fin(total: Option<&str>, n: usize) -> Finalized {
        Finalized {
            total_time: total.map(str::to_string),
            status: Some("finished".into()),
            points: (0..n)
                .map(|i| [((i + 1) as f64) * 63_000.0 / (n as f64), 1635.0, 875.0, 0.79, 1.0])
                .collect(),
        }
    }

    #[test]
    fn accepts_an_exact_match_and_returns_the_trail() {
        // The real spike numbers: JaK, Mario Circuit, 1:02.934 == mkwrs 1'02"934.
        let pts = verify(&fin(Some("1:02.934"), 1732), 62934).expect("exact match must pass");
        assert_eq!(pts.len(), 1732);
    }

    #[test]
    fn rejects_a_mismatched_time_and_reports_both_values() {
        let err = verify(&fin(Some("1:02.934"), 100), 62000).unwrap_err();
        assert_eq!(err, WrError::TimeMismatch { detected_ms: 62934, expected_ms: 62000 });
        assert!(err.reason().starts_with("time_mismatch"));
    }

    #[test]
    fn rejects_an_empty_trail_as_no_trail_not_a_mismatch() {
        // The minimap never locked — a run that produced no reading at all, distinct from
        // a wrong video that produced a confident, wrong reading. Both keep the job
        // claimable up to the attempts cap; this is about which reason gets reported.
        assert_eq!(verify(&fin(Some("1:02.934"), 0), 62934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn rejects_a_run_with_no_total_time() {
        // e.g. an invalidated run: the engine emits status finished with total_time null.
        assert_eq!(verify(&fin(None, 500), 62934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn checks_no_trail_before_the_time_so_the_reason_is_actionable() {
        // The time here is WELL-FORMED and WRONG (62934 vs 62000). That combination is the
        // only thing that can distinguish the two orderings: with the empty-trail check
        // first this is NoTrail; with the time check first it would be TimeMismatch.
        // A malformed time would yield NoTrail either way and prove nothing.
        //
        // The distinction is load-bearing even with a single tier: NoTrail says "this run
        // produced no real reading" (total_time, if present, isn't a measurement of
        // anything); TimeMismatch says "we have a reading and it disagrees with mkwrs".
        // Collapsing them would report a time mismatch off a number that was never real.
        let err = verify(&fin(Some("1:02.934"), 0), 62000).unwrap_err();
        assert_eq!(err, WrError::NoTrail,
                   "an empty trail must report NoTrail even when the time also disagrees");
    }

    #[test]
    fn every_attempt_downloads_native_1080p_now_that_the_4k_tier_is_removed() {
        for attempt in [1, 2, 3, 4, 5, 99] {
            assert_eq!(tier_for(attempt), Tier::Native1080p60,
                "attempt {attempt} must stay native — the 4K escalation tier was removed");
        }
    }

    #[test]
    fn cancelled_is_never_a_verification_outcome() {
        // verify() must only ever report on the CONTENT. Cancellation is the caller's
        // concern and is handled by release(), which refunds the attempt.
        let e = verify(&fin(Some("1:02.934"), 0), 62934).unwrap_err();
        assert_ne!(e, WrError::Cancelled);
        assert_eq!(e, WrError::NoTrail);
    }

    #[test]
    fn rejects_a_fragment_trail_that_stops_mid_race() {
        // The HUD timer and the minimap are read by INDEPENDENT trackers, so a run can
        // read the exact right total while the badge was lost for most of the race
        // (e.g. HDR washout, permanent LOST after lap 1). 500 points, all inside the
        // first 40% of the race, exact time match: without a duration floor this
        // uploads a stub and the wr_trails row permanently marks the job done.
        let mut f = fin(Some("1:02.934"), 500);
        for (i, p) in f.points.iter_mut().enumerate() {
            p[0] = ((i + 1) as f64) * (0.4 * 62_934.0) / 500.0;
        }
        assert_eq!(verify(&f, 62_934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn a_wrong_video_fragment_still_reports_time_mismatch_first() {
        // Mismatch is the more actionable diagnosis (wrong/mislinked video) and the Pi
        // treats it as terminal; the fragment floor must not mask it.
        let mut f = fin(Some("1:02.934"), 500);
        for (i, p) in f.points.iter_mut().enumerate() {
            p[0] = ((i + 1) as f64) * (0.4 * 62_934.0) / 500.0;
        }
        assert!(matches!(verify(&f, 62_000).unwrap_err(), WrError::TimeMismatch { .. }));
    }

    #[test]
    fn a_trail_reaching_exactly_the_floor_boundary_passes() {
        // Pins the boundary DIRECTION: the floor is `<`, so exactly 0.8x must be
        // accepted. Nothing else guards an accidental `<=` flip — every other
        // fragment test sits deep inside the reject region. The last point is
        // hard-set to the same expression verify() computes, so the comparison is
        // bit-exact rather than at the mercy of loop arithmetic.
        let mut f = fin(Some("1:02.934"), 500);
        let n = f.points.len() as f64;
        for (i, p) in f.points.iter_mut().enumerate() {
            p[0] = ((i + 1) as f64) * (0.8 * 62_934.0) / n;
        }
        let last = f.points.len() - 1;
        f.points[last][0] = 0.8 * 62_934.0;
        assert!(verify(&f, 62_934).is_ok(),
            "a trail whose last point lands exactly on the 0.8 floor must pass");
    }
}
