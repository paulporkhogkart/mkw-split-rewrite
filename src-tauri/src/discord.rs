use std::sync::Mutex;
use std::time::{Duration, Instant};
use discord_rich_presence::{activity, DiscordIpc, DiscordIpcClient};

/// Replace with the "Mario Kart World" Discord Application ID (see docs/discord-setup.md).
const DISCORD_APP_ID: &str = "REPLACE_WITH_APPLICATION_ID";
/// Minimum spacing between activity updates (Discord rate-limits ~5 / 20s).
const MIN_INTERVAL: Duration = Duration::from_millis(2500);

#[derive(Clone, Default, PartialEq, serde::Deserialize)]
pub struct Presence {
    pub details: Option<String>,
    pub state: Option<String>,
    pub large_image: Option<String>,
    pub small_image: Option<String>,
    pub button_label: Option<String>,
    pub button_url: Option<String>,
}

/// Pure debounce decision: send if enough time passed OR the payload changed.
pub fn should_send(now: Instant, last_sent: Option<Instant>, changed: bool) -> bool {
    match last_sent {
        None => true,
        Some(t) => changed || now.duration_since(t) >= MIN_INTERVAL,
    }
}

struct State {
    client: Option<DiscordIpcClient>,
    connected: bool,
    last_sent: Option<Instant>,
    last_payload: Option<Presence>,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn ensure_state(s: &mut Option<State>) -> &mut State {
    if s.is_none() {
        *s = Some(State {
            client: None,
            connected: false,
            last_sent: None,
            last_payload: None,
        });
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
    try_connect(st);
    if !st.connected {
        st.last_payload = Some(payload);
        return;
    }

    let changed = st.last_payload.as_ref() != Some(&payload);
    if !should_send(Instant::now(), st.last_sent, changed) {
        return;
    }

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

    if let Some(c) = st.client.as_mut() {
        match c.set_activity(act) {
            Ok(_) => {
                st.last_sent = Some(Instant::now());
                st.last_payload = Some(payload);
            }
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
    fn first_send_always_allowed() {
        assert!(should_send(Instant::now(), None, false));
    }
    #[test]
    fn changed_payload_sends_immediately() {
        let now = Instant::now();
        assert!(should_send(now, Some(now), true));
    }
    #[test]
    fn unchanged_payload_waits_for_interval() {
        let now = Instant::now();
        assert!(!should_send(now, Some(now), false));
        assert!(should_send(now + MIN_INTERVAL, Some(now), false));
    }
}
