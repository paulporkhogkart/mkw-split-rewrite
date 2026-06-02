use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};
use discord_rich_presence::{activity, DiscordIpc, DiscordIpcClient};

/// The "Mario Kart World" Discord Application ID (see docs/discord-setup.md).
const DISCORD_APP_ID: &str = "1511489327291564134";

#[derive(Clone, Default, PartialEq, serde::Deserialize)]
pub struct Presence {
    pub details: Option<String>,
    pub state: Option<String>,
    pub large_image: Option<String>,
    pub small_image: Option<String>,
    pub button_label: Option<String>,
    pub button_url: Option<String>,
}

/// True when the next payload differs from the last one we sent. We push only on
/// a real change and never re-send an identical payload (re-sending made Discord
/// reset the activity, which restarted the elapsed timer on every screen change).
pub fn changed(last: Option<&Presence>, next: &Presence) -> bool {
    last != Some(next)
}

/// App launch time as Unix epoch milliseconds (Discord's timestamp unit), captured
/// once. Used as a FIXED activity start so Discord shows a continuous "since launch"
/// elapsed timer that does not reset when the presence updates.
static LAUNCH_MS: OnceLock<i64> = OnceLock::new();
fn launch_ms() -> i64 {
    *LAUNCH_MS.get_or_init(|| {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as i64)
            .unwrap_or(0)
    })
}

struct State {
    client: Option<DiscordIpcClient>,
    connected: bool,
    last_payload: Option<Presence>,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn ensure_state(s: &mut Option<State>) -> &mut State {
    if s.is_none() {
        *s = Some(State { client: None, connected: false, last_payload: None });
    }
    s.as_mut().unwrap()
}

fn try_connect(st: &mut State) {
    if st.connected {
        return;
    }
    if st.client.is_none() {
        st.client = Some(DiscordIpcClient::new(DISCORD_APP_ID));
    }
    if let Some(c) = st.client.as_mut() {
        match c.connect() {
            Ok(_) => {
                st.connected = true;
                log::info!("[discord] connected");
            }
            Err(e) => log::debug!("[discord] not connected (Discord closed?): {e}"),
        }
    }
}

#[tauri::command]
pub fn discord_set_presence(payload: Presence) {
    let mut guard = STATE.lock().unwrap();
    let st = ensure_state(&mut guard);
    launch_ms(); // prime the launch time at the first presence attempt (~app launch)
    try_connect(st);
    if !st.connected {
        st.last_payload = Some(payload); // retry on the next call
        return;
    }

    // Only push when the rendered content actually changed; never re-send the
    // same payload (avoids resetting Discord's activity / elapsed indicator).
    if !changed(st.last_payload.as_ref(), &payload) {
        return;
    }

    // Hold owned strings so the borrowed Activity stays valid for set_activity.
    let details = payload.details.clone().unwrap_or_default();
    let state_str = payload.state.clone().unwrap_or_default();
    let large = payload.large_image.clone().unwrap_or_default();
    let small = payload.small_image.clone().unwrap_or_default();
    let blabel = payload.button_label.clone().unwrap_or_default();
    let burl = payload.button_url.clone().unwrap_or_default();

    let mut act = activity::Activity::new();
    if !details.is_empty() {
        act = act.details(&details);
    }
    if !state_str.is_empty() {
        act = act.state(&state_str);
    }
    let mut assets = activity::Assets::new();
    if !large.is_empty() {
        assets = assets.large_image(&large);
    }
    if !small.is_empty() {
        assets = assets.small_image(&small);
    }
    act = act.assets(assets);
    if !blabel.is_empty() && !burl.is_empty() {
        act = act.buttons(vec![activity::Button::new(&blabel, &burl)]);
    }
    // Fixed start = app launch, so the elapsed timer counts continuously since
    // launch and never resets across presence updates.
    act = act.timestamps(activity::Timestamps::new().start(launch_ms()));

    if let Some(c) = st.client.as_mut() {
        match c.set_activity(act) {
            Ok(_) => st.last_payload = Some(payload),
            Err(e) => {
                log::debug!("[discord] set_activity failed: {e}");
                st.connected = false;
            }
        }
    }
}

#[tauri::command]
pub fn discord_clear_presence() {
    let mut guard = STATE.lock().unwrap();
    let st = ensure_state(&mut guard);
    if let Some(c) = st.client.as_mut() {
        let _ = c.clear_activity();
    }
    st.last_payload = None;
}

/// Called on app exit.
pub fn shutdown() {
    if let Ok(mut guard) = STATE.lock() {
        if let Some(st) = guard.as_mut() {
            if let Some(c) = st.client.as_mut() {
                let _ = c.clear_activity();
                let _ = c.close();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_payload_is_a_change() {
        assert!(changed(None, &Presence::default()));
    }

    #[test]
    fn identical_payload_is_not_a_change() {
        let p = Presence { details: Some("Idle".into()), ..Default::default() };
        assert!(!changed(Some(&p), &p.clone()));
    }

    #[test]
    fn different_payload_is_a_change() {
        let a = Presence { details: Some("Idle".into()), ..Default::default() };
        let b = Presence { details: Some("In the menus".into()), ..Default::default() };
        assert!(changed(Some(&a), &b));
    }
}
