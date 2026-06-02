import { writable } from "svelte/store";

// Discord settings, persisted in localStorage (decoupled from the Python config).
const ENABLED_KEY = "discord_enabled";
const BTN_ENABLED_KEY = "discord_button_enabled";
const BTN_LABEL_KEY = "discord_button_label";
const URL_KEY = "discord_twitch_url";

const bool = (key, dflt) => {
  const v = localStorage.getItem(key);
  return v === null ? dflt : v === "true";
};

export const discordEnabled      = writable(bool(ENABLED_KEY, true));      // master on/off
export const twitchButtonEnabled = writable(bool(BTN_ENABLED_KEY, true));  // show the button at all
export const twitchLabel         = writable(localStorage.getItem(BTN_LABEL_KEY) || "Watch on Twitch");
export const twitchUrl           = writable(localStorage.getItem(URL_KEY) || "");

discordEnabled.subscribe((v) => localStorage.setItem(ENABLED_KEY, String(v)));
twitchButtonEnabled.subscribe((v) => localStorage.setItem(BTN_ENABLED_KEY, String(v)));
twitchLabel.subscribe((v) => localStorage.setItem(BTN_LABEL_KEY, v || ""));
twitchUrl.subscribe((v) => localStorage.setItem(URL_KEY, v || ""));
