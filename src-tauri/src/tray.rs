//! The system tray (spec 2026-07-17 §2). Exists only while at least one background
//! feature is enabled; grey when idle, red while a job is in flight.

use crate::wr::{self, phase::Phase};
use tauri::Manager;

const TRAY_ID: &str = "pbenguin-tray";

/// Tooltip per spec §2. The parent spec's "Idle — 3 queued" needs a Pi endpoint that
/// doesn't exist; dropped for v1.
pub fn tooltip(run_wr_service: bool, paused: bool, gate_open: bool, phase: Option<&Phase>) -> String {
    if !run_wr_service { return "WR service off".into(); }
    if paused { return "Paused".into(); }
    if let Some(p) = phase {
        return match p.kind {
            wr::phase::PhaseKind::Downloading => format!("Downloading {}…", p.course_slug),
            wr::phase::PhaseKind::Processing => format!("Processing {}…", p.course_slug),
        };
    }
    if !gate_open { return "Waiting — tracking active".into(); }
    "Idle".into()
}

/// Red only while a job is actually in flight.
pub fn is_active(phase: Option<&Phase>) -> bool { phase.is_some() }

fn icon(active: bool) -> Result<tauri::image::Image<'static>, tauri::Error> {
    let bytes: &[u8] = if active {
        include_bytes!("../icons/tray-16-active.png")
    } else {
        include_bytes!("../icons/tray-16-idle.png")
    };
    tauri::image::Image::from_bytes(bytes)
}

fn settings(app: &tauri::AppHandle) -> (bool, bool, bool, bool) {
    match wr::settings_db(app) {
        Ok(c) => (
            wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY),
            wr::state::get_flag(&c, wr::state::SETTING_START_AT_LOGIN),
            wr::state::get_flag(&c, wr::state::SETTING_RUN_WR_SERVICE),
            wr::state::get_flag(&c, wr::state::SETTING_KEEP_TRACKING_IN_TRAY),
        ),
        Err(_) => (false, false, false, false),
    }
}

/// Create or destroy the tray so it exists iff any background feature is on
/// (all-defaults = no tray = zero visible change; spec §2). Rebuilds the menu each call
/// so the Pause/Resume label tracks runner state.
pub fn sync_tray(app: &tauri::AppHandle) {
    let (close_to_tray, start_at_login, run_wr, _) = settings(app);
    let wanted = close_to_tray || start_at_login || run_wr;
    let existing = app.tray_by_id(TRAY_ID);

    if !wanted {
        if existing.is_some() { let _ = app.remove_tray_by_id(TRAY_ID); }
        return;
    }

    let paused = app
        .try_state::<crate::RunnerState>()
        .and_then(|rs| rs.0.lock().ok().map(|g| g.as_ref().map(|r| r.is_paused()).unwrap_or(false)))
        .unwrap_or(false);
    let pause_label = if paused { "Resume WR service" } else { "Pause WR service" };

    let menu = (|| -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
        use tauri::menu::{MenuBuilder, MenuItemBuilder};
        MenuBuilder::new(app)
            .item(&MenuItemBuilder::with_id("pause", pause_label).enabled(run_wr).build(app)?)
            .separator()
            .item(&MenuItemBuilder::with_id("open", "Open pbenguin").build(app)?)
            .item(&MenuItemBuilder::with_id("quit", "Quit").build(app)?)
            .build()
    })();
    let Ok(menu) = menu else { return };

    if let Some(tray) = existing {
        let _ = tray.set_menu(Some(menu));
        refresh_tray_status(app);
        return;
    }

    let Ok(img) = icon(false) else { return };
    let built = tauri::tray::TrayIconBuilder::with_id(TRAY_ID)
        .icon(img)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => crate::show_main_window(app),
            "quit" => app.exit(0),
            "pause" => {
                if let Some(rs) = app.try_state::<crate::RunnerState>() {
                    if let Ok(guard) = rs.0.lock() {
                        if let Some(r) = guard.as_ref() { r.set_paused(!r.is_paused()); }
                    }
                }
                sync_tray(app); // relabel Pause/Resume + retint
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up, ..
            } = event
            {
                crate::show_main_window(tray.app_handle());
            }
        })
        .build(app);
    if built.is_ok() { refresh_tray_status(app); }
}

/// Retint + retooltip from live state. Called by the runner's refresh hook at phase
/// transitions and by sync_tray.
pub fn refresh_tray_status(app: &tauri::AppHandle) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else { return };
    let (_, _, run_wr, _) = settings(app);
    let paused = app
        .try_state::<crate::RunnerState>()
        .and_then(|rs| rs.0.lock().ok().map(|g| g.as_ref().map(|r| r.is_paused()).unwrap_or(false)))
        .unwrap_or(false);
    let phase = wr::phase::get();
    let gate = wr::gate::gate_open(
        wr::gate::ACTIVITY.tracking_running(),
        wr::gate::ACTIVITY.last_change_epoch_ms(),
        wr::gate::now_epoch_ms(),
    );
    let _ = tray.set_tooltip(Some(tooltip(run_wr, paused, gate, phase.as_ref())));
    if let Ok(img) = icon(is_active(phase.as_ref())) {
        let _ = tray.set_icon(Some(img));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wr::phase::PhaseKind;

    fn ph(kind: PhaseKind) -> Phase {
        Phase { kind, course_slug: "dk_spaceport".into() }
    }

    #[test]
    fn tooltip_states() {
        // Priority: off > paused > working > waiting > idle.
        assert_eq!(tooltip(false, false, true, None), "WR service off");
        assert_eq!(tooltip(true, true, true, None), "Paused");
        // Paused must OUTRANK an in-flight phase: after Pause is clicked the phase
        // lingers until the cancel lands (≥250ms, up to a slow release round-trip),
        // and advertising "Downloading…" in that window would say the pause didn't
        // take. Found unpinned by mutation in the 2026-07-18 review — the priority
        // flip passed this test as it stood.
        assert_eq!(tooltip(true, true, true, Some(&ph(PhaseKind::Downloading))), "Paused");
        assert_eq!(tooltip(true, false, true, Some(&ph(PhaseKind::Downloading))),
                   "Downloading dk_spaceport…");
        assert_eq!(tooltip(true, false, true, Some(&ph(PhaseKind::Processing))),
                   "Processing dk_spaceport…");
        assert_eq!(tooltip(true, false, false, None), "Waiting — tracking active");
        assert_eq!(tooltip(true, false, true, None), "Idle");
    }

    #[test]
    fn active_icon_only_while_a_job_is_in_flight() {
        assert!(!is_active(None));
        assert!(is_active(Some(&ph(PhaseKind::Downloading))));
        assert!(is_active(Some(&ph(PhaseKind::Processing))));
    }
}
