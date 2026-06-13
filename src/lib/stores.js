import { writable } from "svelte/store";

// Backend-derived state, read by the rebuilt components. These are populated from the
// `tracker-event` handler; each store is wired off App.svelte's legacy local vars in the
// phase that builds the component consuming it (so the live app never breaks mid-refactor).

export const connection = writable({ connected: false, spawned: false, fps: 0, lastHeartbeat: 0 });
export const screen     = writable("-");                  // backend Screen enum name
export const liveScore  = writable(0);
export const candidates = writable({});                   // { screen:[{name,score}], char:[…], kart:[…], course:[…], costume:[…] }
export const selection  = writable({ char: null, charConf: 0, costume: null, costumeConf: 0,
                                     kart: null, kartConf: 0, course: null, courseConf: 0 });
export const race       = writable({ curLap: null, totLap: null, coins: null, mushrooms: 0,
                                     splits: {}, finishTime: null, elapsedMs: null });
export const minimap    = writable(null);                 // { cx, cy, radius, trackState, roi:[x,y,w,h] } | null (current run only)
export const sample     = writable(null);                 // raw base64 PNG (no data-URI prefix) of the locked icon template | null
export const devices    = writable({ video: [], audio: [], selectedVideo: "", selectedAudio: "" });
export const tells      = writable([]);                   // detection tell trees (list_tells)
export const rois       = writable({});                   // selection/HUD config ROIs (list_rois)
export const logs       = writable([]);                   // event log lines
export const view       = writable("monitor");            // "monitor" | "edit" | "settings"
export const setup      = writable({ complete: null, open: false, step: "language" });
export const language   = writable({ app: "en_uk", switch2: "en_uk" });
export const pbSplits     = writable(null);  // {lap: split_ms} for current course PB | null
export const pbTotalMs    = writable(null);  // PB total time in ms | null
export const friendsPbs    = writable([]);   // [{player_id, display_name, total_time_ms, total_time_str, rank}] (server; data-only)
export const trailRuns     = writable([]);   // render-ready ghost trails: [{points:[[t,cx,cy,score]], color, opacity}]
export const trailLegend   = writable([]);   // [{name, color, mode, n}] for the active players (overlay legend)
export const presence      = writable({});   // {player_id: PresenceEntry} live status of roster players (server /v1/presence)
export const myPlayerId    = writable(null); // this client's roster id (presence snapshot `you`) | null
export const serverConnection = writable({ connected: false, syncedAt: null }); // season-server link state for the player panel + StatusBar
