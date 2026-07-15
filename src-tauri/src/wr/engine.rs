//! Drives the Python engine over stdio to extract a trail from a WR video.
//!
//! `EngineDriver` is PURE: feed it stdout lines, it returns stdin commands. All the
//! sequencing knowledge proven by the 2026-07-15 spike lives here and is unit-tested;
//! `run_video` (Task 3) is the thin process shell around it.

use serde_json::json;

/// Engine DISPLAY names (not slugs). The caller maps slug -> display before constructing.
#[derive(Debug, Clone)]
pub struct Selections {
    pub course: String,
    pub character: String,
    /// None = base costume. Must be OMITTED from set_selection, not sent as null.
    pub costume: Option<String>,
    pub kart: Option<String>,
}

/// What the engine reported at the end of the run.
#[derive(Debug, Clone)]
pub struct Finalized {
    pub total_time: Option<String>,
    /// [t_ms, cx, cy, score, lap]; lap = -1.0 when the engine omitted it (legacy 4-tuple).
    pub points: Vec<[f64; 5]>,
}

/// Pure sequencer: stdout line in, stdin commands out.
pub struct EngineDriver {
    selections: Selections,
    injected: bool,
    forced: bool,
    finalized: Option<Finalized>,
}

impl EngineDriver {
    pub fn new(selections: Selections) -> Self {
        Self { selections, injected: false, forced: false, finalized: None }
    }

    pub fn finalized(&self) -> Option<&Finalized> { self.finalized.as_ref() }

    /// Feed one engine stdout line; returns JSON commands to write to its stdin.
    /// Non-JSON lines (the engine's print() diagnostics) yield nothing.
    pub fn on_line(&mut self, line: &str) -> Vec<String> {
        let v: serde_json::Value = match serde_json::from_str(line.trim()) {
            Ok(v) => v,
            Err(_) => return vec![],
        };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("ready") if !self.injected => {
                self.injected = true;
                let mut m = serde_json::Map::new();
                m.insert("type".into(), json!("set_selection"));
                m.insert("course".into(), json!(self.selections.course));
                m.insert("character".into(), json!(self.selections.character));
                if let Some(c) = &self.selections.costume { m.insert("costume".into(), json!(c)); }
                if let Some(k) = &self.selections.kart { m.insert("kart".into(), json!(k)); }
                vec![serde_json::Value::Object(m).to_string()]
            }
            Some("screen_change")
                if !self.forced
                    && v.get("to").and_then(|t| t.as_str()) == Some("UNKNOWN_RACE_ACTIVE") =>
            {
                self.forced = true;
                // WR uploads cut from a menu straight into the countdown, so the detector
                // parks in UNKNOWN_RACE_ACTIVE and every tracker stays gated off.
                // RESET first (it is in _RACE_START_SCREENS) so RESET->RACING reads as a
                // genuine fresh start. Forcing RACING directly hits race.py:182 ->
                // _invalidate() -> _mm_rec.stop(), which CLEARS the points.
                vec![
                    json!({"type":"force_screen","screen":"RESET"}).to_string(),
                    json!({"type":"force_screen","screen":"RACING"}).to_string(),
                ]
            }
            Some("run_finalized") => {
                let points = v.get("points").and_then(|p| p.as_array()).map(|arr| {
                    arr.iter().filter_map(|p| {
                        let a = p.as_array()?;
                        if a.len() < 4 { return None; }
                        let n = |i: usize| a.get(i).and_then(|x| x.as_f64());
                        Some([n(0)?, n(1)?, n(2)?, n(3)?, n(4).unwrap_or(-1.0)])
                    }).collect()
                }).unwrap_or_default();
                self.finalized = Some(Finalized {
                    total_time: v.get("total_time").and_then(|t| t.as_str()).map(str::to_string),
                    points,
                });
                vec![]
            }
            _ => vec![],
        }
    }
}

/// Parse the engine's `M:SS.mmm` into milliseconds.
pub fn time_to_ms(s: &str) -> Option<i64> {
    let (m, rest) = s.split_once(':')?;
    let (sec, ms) = rest.split_once('.')?;
    let m: i64 = m.trim().parse().ok()?;
    let sec: i64 = sec.parse().ok()?;
    let ms: i64 = ms.parse().ok()?;
    if sec >= 60 || ms >= 1000 { return None; }
    Some(m * 60_000 + sec * 1_000 + ms)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sel() -> Selections {
        Selections {
            course: "Mario Circuit".into(), character: "Toadette".into(),
            costume: Some("Explorer".into()), kart: Some("Baby Blooper".into()),
        }
    }

    #[test]
    fn injects_selections_on_ready_with_top_level_keys() {
        let mut d = EngineDriver::new(sel());
        let out = d.on_line(r#"{"type":"ready","version":"2.7.0"}"#);
        assert_eq!(out.len(), 1);
        let v: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        assert_eq!(v["type"], "set_selection");
        // TOP-LEVEL keys — main.py:179 reads msg.get("course"), NOT {field,value}.
        assert_eq!(v["course"], "Mario Circuit");
        assert_eq!(v["character"], "Toadette");
        assert_eq!(v["costume"], "Explorer");
        assert_eq!(v["kart"], "Baby Blooper");
    }

    #[test]
    fn omits_a_base_costume_rather_than_sending_null() {
        let mut d = EngineDriver::new(Selections {
            course: "Choco Mountain".into(), character: "Bowser".into(),
            costume: None, kart: Some("Reel Racer".into()),
        });
        let out = d.on_line(r#"{"type":"ready"}"#);
        let v: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        assert!(v.get("costume").is_none(), "base costume must be omitted, not null");
        assert_eq!(v["character"], "Bowser");
    }

    #[test]
    fn injects_only_once() {
        let mut d = EngineDriver::new(sel());
        assert_eq!(d.on_line(r#"{"type":"ready"}"#).len(), 1);
        assert_eq!(d.on_line(r#"{"type":"ready"}"#).len(), 0, "re-injecting would fight the tracker");
    }

    #[test]
    fn forces_reset_then_racing_on_unknown_race_active() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        let out = d.on_line(r#"{"type":"screen_change","from":"UNKNOWN","to":"UNKNOWN_RACE_ACTIVE"}"#);
        assert_eq!(out.len(), 2, "must force RESET then RACING");
        let a: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        let b: serde_json::Value = serde_json::from_str(&out[1]).unwrap();
        assert_eq!(a["type"], "force_screen");
        // RESET first: it is in _RACE_START_SCREENS, so RESET->RACING is a genuine fresh
        // start. Forcing RACING directly from UNKNOWN_RACE_ACTIVE hits race.py:182 ->
        // _invalidate() -> _mm_rec.stop() -> the points are CLEARED.
        assert_eq!(a["screen"], "RESET");
        assert_eq!(b["screen"], "RACING");
    }

    #[test]
    fn forces_only_once_even_if_the_screen_flaps() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        assert_eq!(d.on_line(r#"{"type":"screen_change","to":"UNKNOWN_RACE_ACTIVE"}"#).len(), 2);
        assert_eq!(d.on_line(r#"{"type":"screen_change","to":"UNKNOWN_RACE_ACTIVE"}"#).len(), 0);
    }

    #[test]
    fn never_forces_when_the_video_has_a_real_loading_screen() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        let out = d.on_line(r#"{"type":"screen_change","from":"RESET","to":"RACING"}"#);
        assert!(out.is_empty(), "a genuine start must never be forced — forcing would invalidate it");
    }

    #[test]
    fn tolerates_the_engines_non_json_stdout_diagnostics() {
        let mut d = EngineDriver::new(sel());
        // The engine print()s these alongside the JPC stream (tracker.py:166 etc).
        let out = d.on_line("  [MinimapTracker] Seeded (1636,876) r=20 conf_thr=0.65");
        assert!(out.is_empty());
        assert!(d.finalized().is_none());
    }

    #[test]
    fn captures_run_finalized_with_its_trail() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"run_finalized","status":"finished","total_time":"1:02.934",
                      "points":[[14,1635,875,0.79,1],[114,1636,870,0.81,1]]}"#);
        let f = d.finalized().expect("must capture run_finalized");
        assert_eq!(f.total_time.as_deref(), Some("1:02.934"));
        assert_eq!(f.points.len(), 2);
        assert_eq!(f.points[0][0], 14.0);
        assert_eq!(f.points[0][1], 1635.0);
    }

    #[test]
    fn accepts_a_legacy_four_tuple_point() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"run_finalized","status":"finished","total_time":"1:02.934",
                      "points":[[14,1635,875,0.79]]}"#);
        let f = d.finalized().unwrap();
        assert_eq!(f.points[0][4], -1.0, "missing lap becomes the -1 sentinel");
    }

    #[test]
    fn time_to_ms_parses_the_engines_format() {
        assert_eq!(time_to_ms("1:02.934"), Some(62934));
        assert_eq!(time_to_ms("2:09.606"), Some(129606));
        assert_eq!(time_to_ms("0:18.213"), Some(18213));
        assert_eq!(time_to_ms(""), None);
        assert_eq!(time_to_ms("nonsense"), None);
    }
}
