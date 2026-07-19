//! The Pi's WR job API + the job payload.
//!
//! Auth is the ordinary PLAYER token (header only — a ?token= in a write URL would leak
//! into logs), plus X-Worker-Id for the per-MACHINE lease: a player token identifies a
//! person, and one person may run this on several PCs (spec §6).

use super::WrError;

#[derive(Debug, Clone)]
pub struct WrJob {
    pub wr_id: i64,
    pub course_slug: String,
    pub video_url: String,
    pub record_ms: i64,
    pub character_slug: String,
    /// None = base costume. Legitimate and the common case (24 of 30 live WRs).
    pub costume_slug: Option<String>,
    pub kart_slug: Option<String>,
    /// 1-based, post-increment. Drives the retry tier (verify::tier_for).
    pub attempt: i64,
}

/// Parse a claim response. None = unusable (not merely absent).
pub fn parse_job(body: &str) -> Option<WrJob> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_string);
    let i = |k: &str| v.get(k).and_then(|x| x.as_i64());

    // Strict like the required fields below: a PRESENT-but-wrong-type "attempt" (e.g.
    // "attempt":"3") must reject the whole job, not silently coerce to the default —
    // `unwrap_or` alone cannot tell "absent" and "wrong type" apart. Absent is fine:
    // attempt 1 is the legitimate default for a job's first claim.
    let attempt = match v.get("attempt") {
        None | Some(serde_json::Value::Null) => 1,
        Some(x) => x.as_i64()?,
    };

    Some(WrJob {
        wr_id: i("wr_id")?,
        course_slug: s("course_slug")?,
        video_url: s("video_url")?,
        record_ms: i("record_ms")?,
        // Required: no character => no set_selection => the minimap cannot seed.
        character_slug: s("character_slug")?,
        costume_slug: s("costume_slug"),
        kart_slug: s("kart_slug"),
        attempt,
    })
}

/// slug -> the engine's DISPLAY name. Mirrors how engine template keys are derived from
/// filenames: `_` -> space, then title-case each word (selection.py:69).
pub fn slug_to_display(slug: &str) -> String {
    slug.split('_')
        .filter(|w| !w.is_empty())
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// slug -> the display name the ENGINE keys its minimap seeds/ROIs/thresholds on.
///
/// NOT the Pi's canonical display name. The engine's course names are detection-derived
/// (template filename stem via `_`->space + title-case, templates.py:93) and courses are
/// deliberately not canonicalized to their marketing names (selection.py:76) — so the
/// seed table keys on "Dk Spaceport" / "Mario Bros Circuit" / "Toads Factory", while the
/// Pi sends "DK Spaceport" / "Mario Bros. Circuit" / "Toad’s Factory" (server/courses.py).
/// Sending the Pi name verbatim finds no seed on 7 of 30 courses, the tracker never
/// seeds, and the run is a guaranteed no_trail (verified on the real DB, 2026-07-17).
///
/// `slug_to_display` reproduces the engine derivation for 29 of 30 courses. The single
/// exception is Sky-High Sundae: its seed row was written by the engine's own schema
/// migration (migrations.py _SEED_V2) with the hyphenated name, which title-casing a
/// filename can never produce — so it is matched literally, as the row exists on every
/// install. If the engine ever migrates that row to "Sky High Sundae" (which would also
/// fix live-detection seeding on that course — the same latent mismatch), DELETE this
/// exception in the same commit.
pub fn course_display_for_engine(course_slug: &str) -> String {
    match course_slug {
        "sky_high_sundae" => "Sky-High Sundae".into(),
        s => slug_to_display(s),
    }
}

/// The Pi's WR job API.
pub struct Client {
    base: String,
    token: String,
    worker_id: String,
    http: reqwest::blocking::Client,
}

impl Client {
    pub fn new(server_url: &str, token: &str, worker_id: &str) -> Self {
        Self {
            base: server_url.trim_end_matches('/').to_string(),
            token: token.to_string(),
            worker_id: worker_id.to_string(),
            http: reqwest::blocking::Client::new(),
        }
    }

    fn post(&self, path: &str, body: Option<String>) -> Result<reqwest::blocking::Response, String> {
        let mut rq = self.http
            .post(format!("{}{}", self.base, path))
            .bearer_auth(&self.token)               // header ONLY — never ?token=
            .header("X-Worker-Id", &self.worker_id) // per-MACHINE lease identity
            .timeout(std::time::Duration::from_secs(30));
        if let Some(b) = body {
            rq = rq.header("content-type", "application/json").body(b);
        }
        rq.send().map_err(|e| e.to_string())
    }

    /// Ok(None) = 204, nothing claimable right now.
    pub fn claim(&self) -> Result<Option<WrJob>, String> {
        let res = self.post("/v1/wr-jobs/claim", None)?;
        if res.status().as_u16() == 204 { return Ok(None); }
        if !res.status().is_success() { return Err(format!("claim: HTTP {}", res.status())); }
        let body = res.text().map_err(|e| e.to_string())?;
        parse_job(&body).map(Some).ok_or_else(|| format!("claim: unusable job: {body}"))
    }

    /// Ok(false) = the request did not succeed — any non-2xx status folds to false here,
    /// not just a 409. In practice that's almost always a 409 (we no longer hold the
    /// lease), but a transient 5xx would read identically: treat false as "stop working,
    /// we cannot confirm we still hold this job" rather than specifically "409".
    ///
    /// Called every 120s by run_job's heartbeat thread while the engine runs (Plan 3),
    /// so the lease outlives any legal run regardless of the download's duration.
    pub fn heartbeat(&self, wr_id: i64) -> Result<bool, String> {
        Ok(self.post(&format!("/v1/wr-jobs/{wr_id}/heartbeat"), None)?.status().is_success())
    }

    /// Hand the job back voluntarily. The Pi REFUNDS the attempt, so a pause never
    /// counts against the cap (unlike a crash, where the lease just lapses).
    /// Ok(false) = the request did not succeed — like `heartbeat`, any non-2xx status
    /// (not only a 409/lease-already-lapsed) folds to false here.
    pub fn release(&self, wr_id: i64) -> Result<bool, String> {
        Ok(self.post(&format!("/v1/wr-jobs/{wr_id}/release"), None)?.status().is_success())
    }

    pub fn complete(&self, wr_id: i64, points: &[[f64; 5]]) -> Result<(), String> {
        // Wire format is [t_ms, cx, cy, score, lap?]; -1.0 is our "engine omitted lap"
        // sentinel and must go back as null, which the Pi stores as the codec's LAP_NULL.
        let pts: Vec<serde_json::Value> = points.iter().map(|p| {
            if p[4] < 0.0 { serde_json::json!([p[0], p[1], p[2], p[3]]) }
            else { serde_json::json!([p[0], p[1], p[2], p[3], p[4]]) }
        }).collect();
        let body = serde_json::json!({ "ok": true, "points": pts }).to_string();
        let res = self.post(&format!("/v1/wr-jobs/{wr_id}/result"), Some(body))?;
        if res.status().is_success() { Ok(()) } else { Err(format!("result: HTTP {}", res.status())) }
    }

    pub fn fail(&self, wr_id: i64, err: &WrError) -> Result<(), String> {
        let body = serde_json::json!({ "ok": false, "error": err.reason() }).to_string();
        let res = self.post(&format!("/v1/wr-jobs/{wr_id}/result"), Some(body))?;
        if res.status().is_success() { Ok(()) } else { Err(format!("result: HTTP {}", res.status())) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Verbatim from a live claim during the Plan 1 end-to-end verification.
    const LIVE: &str = r#"{"wr_id":6,"cc":150,"course_slug":"shy_guy_bazaar",
      "course_name":"Shy Guy Bazaar","video_url":"https://www.youtube.com/watch?v=wTZXUMhimbw",
      "record_ms":110449,"lap_splits_ms":null,"character_slug":"swoop","costume_slug":null,
      "kart_slug":"rob_hog","attempt":1,"lease_until":"2026-07-15 04:38:40"}"#;

    #[test]
    fn parses_a_real_claim_response() {
        let j = parse_job(LIVE).expect("must parse the live shape");
        assert_eq!(j.wr_id, 6);
        assert_eq!(j.record_ms, 110449);
        assert_eq!(j.attempt, 1);
        assert_eq!(j.character_slug, "swoop");
        assert_eq!(j.costume_slug, None, "null costume = base costume, NOT an error");
        assert_eq!(j.kart_slug.as_deref(), Some("rob_hog"));
    }

    #[test]
    fn parses_a_job_that_has_a_costume() {
        let j = parse_job(&LIVE.replace(r#""costume_slug":null"#, r#""costume_slug":"explorer""#)).unwrap();
        assert_eq!(j.costume_slug.as_deref(), Some("explorer"));
    }

    #[test]
    fn rejects_a_job_with_no_character_slug() {
        // The Pi's claim filters these out, but never trust the wire: without a character
        // there is no set_selection to build, so the engine cannot seed the minimap.
        let bad = LIVE.replace(r#""character_slug":"swoop""#, r#""character_slug":null"#);
        assert!(parse_job(&bad).is_none());
    }

    #[test]
    fn rejects_a_job_with_wrong_json_type_for_wr_id() {
        // A string where a number is expected must fail parsing, not silently coerce.
        let bad = LIVE.replacen(r#""wr_id":6"#, r#""wr_id":"6""#, 1);
        assert!(parse_job(&bad).is_none());
    }

    #[test]
    fn rejects_a_job_with_wrong_json_type_for_attempt() {
        // "attempt":"3" must reject the whole job, not silently become 1 —
        // `unwrap_or(1)` alone cannot distinguish "absent" from "present but wrong type".
        let bad = LIVE.replacen(r#""attempt":1"#, r#""attempt":"3""#, 1);
        assert!(parse_job(&bad).is_none(),
            "a string attempt must be rejected, not silently coerced to the default");
    }

    #[test]
    fn defaults_a_missing_attempt_to_one() {
        // Absent is legitimately different from wrong-type: a job's first claim may simply
        // omit attempt, and 1 is the correct default for that case.
        let without_attempt = LIVE.replacen(r#","attempt":1"#, "", 1);
        assert!(!without_attempt.contains("\"attempt\""), "precondition: attempt must be gone");
        let j = parse_job(&without_attempt).expect("a missing attempt must still parse");
        assert_eq!(j.attempt, 1);
    }

    #[test]
    fn slug_to_display_matches_the_engines_template_keys() {
        // Engine keys come from filenames via `_`->space + .title() (selection.py:69).
        assert_eq!(slug_to_display("baby_blooper"), "Baby Blooper");
        assert_eq!(slug_to_display("toadette"), "Toadette");
        assert_eq!(slug_to_display("mario_circuit"), "Mario Circuit");
        assert_eq!(slug_to_display("w_twin_chopper"), "W Twin Chopper");
        assert_eq!(slug_to_display(""), "");
    }

    #[test]
    fn fail_reasons_are_the_stable_strings_the_pi_stores() {
        assert_eq!(WrError::NoTrail.reason(), "no_trail");
        assert_eq!(WrError::No1080p60.reason(), "no_1080p60");
        assert_eq!(WrError::Timeout.reason(), "timeout");
        assert_eq!(WrError::VideoUnavailable.reason(), "video_unavailable");
        assert!(WrError::EngineFailed("boom".into()).reason().starts_with("engine_failed"));
        assert!(WrError::EngineIncompatible("x".into()).reason().starts_with("engine_incompatible"),
            "must stay distinguishable from engine_failed if the release() contract is ever broken");
    }

    #[test]
    fn a_time_mismatch_reason_carries_both_numbers_for_a_human() {
        // This is the reason Paul reads when mkwrs links the wrong video, so it has to
        // say what we saw AND what was expected -- "time_mismatch" alone is useless.
        let r = WrError::TimeMismatch { detected_ms: 62934, expected_ms: 62000 }.reason();
        assert!(r.contains("62934") && r.contains("62000"), "unhelpful reason: {r}");
    }

    #[test]
    fn course_display_matches_the_engines_seed_keys_not_the_pi_names() {
        // The engine's minimap seeds key on DETECTION-derived names (filename stem via
        // `_`->space + title-case; courses are NOT canonicalized — selection.py:76).
        // The Pi's canonical names differ on 7 of 30 courses; each of these inputs is
        // one where sending the Pi's course_name verbatim finds NO seed (verified
        // against the real DB, 2026-07-17) and the run is a guaranteed no_trail.
        assert_eq!(course_display_for_engine("dk_spaceport"), "Dk Spaceport");
        assert_eq!(course_display_for_engine("dk_pass"), "Dk Pass");
        assert_eq!(course_display_for_engine("mario_bros_circuit"), "Mario Bros Circuit");
        assert_eq!(course_display_for_engine("toads_factory"), "Toads Factory");
        assert_eq!(course_display_for_engine("bowsers_castle"), "Bowsers Castle");
        assert_eq!(course_display_for_engine("warios_galleon"), "Warios Galleon");
        assert_eq!(course_display_for_engine("great_block_ruins"), "Great Block Ruins");
        // The 23 already-agreeing courses must keep working unchanged.
        assert_eq!(course_display_for_engine("mario_circuit"), "Mario Circuit");
        assert_eq!(course_display_for_engine("rainbow_road"), "Rainbow Road");
    }

    #[test]
    fn sky_high_sundae_is_the_one_hyphenated_seed_key_exception() {
        // Its seed row was written by the engine's own migration (migrations.py
        // _SEED_V2) as 'Sky-High Sundae' — a hyphen title-casing a filename can never
        // produce, so slug_to_display would miss it. Match the row as it exists on
        // every install.
        assert_eq!(course_display_for_engine("sky_high_sundae"), "Sky-High Sundae");
    }
}
