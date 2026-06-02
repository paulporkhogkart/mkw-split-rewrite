import { writable } from "svelte/store";

const ENABLED_KEY = "discord_enabled";
const TWITCH_KEY = "discord_twitch_url";

const initialEnabled = localStorage.getItem(ENABLED_KEY);
export const discordEnabled = writable(initialEnabled === null ? true : initialEnabled === "true");
export const twitchUrl = writable(localStorage.getItem(TWITCH_KEY) || "");

discordEnabled.subscribe((v) => localStorage.setItem(ENABLED_KEY, String(v)));
twitchUrl.subscribe((v) => localStorage.setItem(TWITCH_KEY, v || ""));
