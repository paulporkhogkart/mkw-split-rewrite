//! The free correctness gate (spec §6.6) + the retry escalation tiers (§6.4).

use super::engine::{time_to_ms, Finalized};
use super::WrError;

/// Which source to download for this attempt (spec §6.4 retry tiers).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier {
    /// YouTube's native 1080p60. The default and almost always right.
    Native1080p60,
    /// 2160p60 downscaled to 1080p with ffmpeg lanczos. Measured to raise the median
    /// badge NCC 0.796 -> 0.829 but to produce an IDENTICAL trail, because
    /// calibrate_from_race scales its margin by (1 - median) so a better image tightens
    /// its own threshold. Costs 3.6x the bandwidth + a ~58s transcode, so it is worth it
    /// ONLY as a last resort for a video that produced no trail at all.
    Downscaled4k,
}

/// Escalate only for a genuinely marginal video. A time_mismatch must never escalate —
/// a wrong video is wrong at any bitrate; it needs a human, not more pixels.
pub fn tier_for(attempt: i64) -> Tier {
    if attempt == 3 { Tier::Downscaled4k } else { Tier::Native1080p60 }
}

/// The free correctness gate: the engine read the time off the video without consulting
/// mkwrs, so an exact match is strong evidence we processed the right video in full.
/// Returns the trail on success.
pub fn verify(f: &Finalized, expected_ms: i64) -> Result<Vec<[f64; 5]>, WrError> {
    // Order matters: "no trail" is the actionable reason and is retryable at a higher
    // tier; a bogus time on an empty run would otherwise mask it as a mismatch.
    if f.points.is_empty() { return Err(WrError::NoTrail); }
    let detected_ms = match f.total_time.as_deref().and_then(time_to_ms) {
        Some(ms) => ms,
        None => return Err(WrError::NoTrail),
    };
    if detected_ms != expected_ms {
        return Err(WrError::TimeMismatch { detected_ms, expected_ms });
    }
    Ok(f.points.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fin(total: Option<&str>, n: usize) -> Finalized {
        Finalized {
            total_time: total.map(str::to_string),
            points: (0..n).map(|i| [i as f64, 1635.0, 875.0, 0.79, 1.0]).collect(),
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
        // The minimap never locked. Distinct from a wrong video: this one is worth retrying
        // at higher quality (tier 3), a mismatch never is.
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
        // The distinction is load-bearing: NoTrail is retryable at a higher quality tier,
        // TimeMismatch never is (a wrong video is wrong at any bitrate).
        let err = verify(&fin(Some("1:02.934"), 0), 62000).unwrap_err();
        assert_eq!(err, WrError::NoTrail,
                   "an empty trail must report NoTrail even when the time also disagrees");
    }

    #[test]
    fn tier_1_and_2_are_native_1080p_and_3_escalates_to_4k() {
        assert_eq!(tier_for(1), Tier::Native1080p60);
        assert_eq!(tier_for(2), Tier::Native1080p60, "attempt 2 = a plain re-download (throttling/403)");
        assert_eq!(tier_for(3), Tier::Downscaled4k);
        assert_eq!(tier_for(4), Tier::Native1080p60, "past the escalation, back off rather than re-pay 197MB");
    }

    #[test]
    fn cancelled_is_never_a_verification_outcome() {
        // verify() must only ever report on the CONTENT. Cancellation is the caller's
        // concern and is handled by release(), which refunds the attempt.
        let e = verify(&fin(Some("1:02.934"), 0), 62934).unwrap_err();
        assert_ne!(e, WrError::Cancelled);
        assert_eq!(e, WrError::NoTrail);
    }
}
