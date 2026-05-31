// Graph layout, edge list, and pan/zoom math for the screen-graph navigator.
// Pure JS — no DOM, no Svelte. Used by both the footer graph in App.svelte
// and the standalone ScreenGraph.svelte component.

// Node box dimensions
export const NW = 88;
export const NH = 24;

// Overall logical canvas dimensions (content bounding box at 1× zoom)
export const GRAPH_W = 860;
export const GRAPH_H = 205;

// Screen graph nodes — each positioned in logical px on the GRAPH_W × GRAPH_H canvas.
export const GRAPH_NODES = [
  { id:"UNKNOWN",            x:5,   y:175, label:"UNKNOWN"    },
  { id:"TITLE",              x:5,   y:5,   label:"TITLE"      },
  { id:"HOME",               x:5,   y:45,  label:"HOME"       },
  { id:"GALLERY",            x:5,   y:85,  label:"GALLERY"    },
  { id:"MAIN_MENU",          x:115, y:5,   label:"MAIN MENU"  },
  { id:"SINGLEPLAYER_MENU",  x:225, y:5,   label:"SP MENU"    },
  { id:"TIME_TRIALS",        x:225, y:31,  label:"SP [TT SEL]"},
  { id:"CHARACTER_SELECT",   x:335, y:5,   label:"CHAR SEL"   },
  { id:"KART_SELECT",        x:445, y:5,   label:"KART SEL"   },
  { id:"COURSE_SELECT",      x:550, y:5,   label:"COURSE SEL" },
  { id:"START_TIME_TRIAL",   x:760, y:41,  label:"START TT"   },
  { id:"START_REPLAY",       x:760, y:81,  label:"START RPY"  },
  { id:"RACING",             x:655, y:101, label:"RACING"     },
  { id:"GHOST",              x:760, y:121, label:"GHOST"      },
  { id:"UNKNOWN_RACE_ACTIVE",x:655, y:139, label:"UNK RACE"   },
  { id:"RACE_MENU",          x:550, y:101, label:"RACE MENU"  },
  { id:"REPLAY_MENU",        x:550, y:139, label:"REPLAY MENU"},
  { id:"RESET",              x:445, y:139, label:"RESET"      },
  { id:"GHOST_RESET",        x:445, y:175, label:"GHOST RST"  },
  { id:"UNKNOWN_RESET",      x:335, y:139, label:"UNK RESET"  },
  { id:"REPLAY_RACE_AGAINST",x:550, y:175, label:"REPLAY [RA]"},
  { id:"POST_TIME_TRIAL",    x:655, y:175, label:"POST TT"    },
];

// HOME edges: HOME↔TITLE and HOME↔GALLERY are constant (full opacity).
// All other →HOME edges are present but rendered dimmed by the consumer.
export const GRAPH_EDGES = [
  // HOME constant two-way connections
  ["HOME","TITLE"],["HOME","GALLERY"],["TITLE","HOME"],["GALLERY","HOME"],
  // all other →HOME (dimmed in renderer when not contextually relevant)
  ["MAIN_MENU","HOME"],["SINGLEPLAYER_MENU","HOME"],["TIME_TRIALS","HOME"],
  ["CHARACTER_SELECT","HOME"],["KART_SELECT","HOME"],["COURSE_SELECT","HOME"],
  ["START_TIME_TRIAL","HOME"],["START_REPLAY","HOME"],
  ["RACING","HOME"],["GHOST","HOME"],["UNKNOWN_RACE_ACTIVE","HOME"],
  ["RESET","HOME"],["GHOST_RESET","HOME"],["UNKNOWN_RESET","HOME"],
  ["POST_TIME_TRIAL","HOME"],["RACE_MENU","HOME"],
  ["REPLAY_MENU","HOME"],["REPLAY_RACE_AGAINST","HOME"],
  // TITLE
  ["TITLE","MAIN_MENU"],
  // MAIN_MENU
  ["MAIN_MENU","SINGLEPLAYER_MENU"],["MAIN_MENU","TIME_TRIALS"],["MAIN_MENU","TITLE"],
  // SINGLEPLAYER_MENU
  ["SINGLEPLAYER_MENU","TIME_TRIALS"],["SINGLEPLAYER_MENU","MAIN_MENU"],
  // TIME_TRIALS
  ["TIME_TRIALS","CHARACTER_SELECT"],["TIME_TRIALS","SINGLEPLAYER_MENU"],["TIME_TRIALS","MAIN_MENU"],
  // CHARACTER_SELECT
  ["CHARACTER_SELECT","KART_SELECT"],["CHARACTER_SELECT","TIME_TRIALS"],
  // KART_SELECT
  ["KART_SELECT","COURSE_SELECT"],["KART_SELECT","CHARACTER_SELECT"],
  // COURSE_SELECT
  ["COURSE_SELECT","START_TIME_TRIAL"],["COURSE_SELECT","START_REPLAY"],["COURSE_SELECT","KART_SELECT"],
  // START_TIME_TRIAL
  ["START_TIME_TRIAL","RACING"],["START_TIME_TRIAL","RACE_MENU"],["START_TIME_TRIAL","COURSE_SELECT"],
  // START_REPLAY
  ["START_REPLAY","GHOST"],["START_REPLAY","REPLAY_MENU"],["START_REPLAY","COURSE_SELECT"],
  // RACING
  ["RACING","POST_TIME_TRIAL"],["RACING","RACE_MENU"],
  // GHOST
  ["GHOST","REPLAY_MENU"],
  // UNKNOWN_RACE_ACTIVE
  ["UNKNOWN_RACE_ACTIVE","RACE_MENU"],["UNKNOWN_RACE_ACTIVE","REPLAY_MENU"],
  ["UNKNOWN_RACE_ACTIVE","POST_TIME_TRIAL"],["UNKNOWN_RACE_ACTIVE","RESET"],
  // RESET
  ["RESET","RACING"],["RESET","CHARACTER_SELECT"],["RESET","COURSE_SELECT"],
  ["RESET","MAIN_MENU"],["RESET","TITLE"],
  // GHOST_RESET
  ["GHOST_RESET","GHOST"],["GHOST_RESET","MAIN_MENU"],["GHOST_RESET","COURSE_SELECT"],
  // UNKNOWN_RESET
  ["UNKNOWN_RESET","UNKNOWN_RACE_ACTIVE"],["UNKNOWN_RESET","RACING"],["UNKNOWN_RESET","GHOST"],
  ["UNKNOWN_RESET","CHARACTER_SELECT"],["UNKNOWN_RESET","COURSE_SELECT"],
  ["UNKNOWN_RESET","MAIN_MENU"],["UNKNOWN_RESET","TITLE"],
  // POST_TIME_TRIAL
  ["POST_TIME_TRIAL","COURSE_SELECT"],["POST_TIME_TRIAL","RESET"],
  // RACE_MENU
  ["RACE_MENU","RACING"],["RACE_MENU","RESET"],
  // REPLAY_MENU
  ["REPLAY_MENU","GHOST"],["REPLAY_MENU","REPLAY_RACE_AGAINST"],["REPLAY_MENU","GHOST_RESET"],
  // REPLAY_RACE_AGAINST
  ["REPLAY_RACE_AGAINST","RESET"],["REPLAY_RACE_AGAINST","REPLAY_MENU"],
  // GALLERY (HOME→GALLERY is dynamic)
];

// Prebuilt id→node lookup.
export const GRAPH_NODE_MAP = Object.fromEntries(GRAPH_NODES.map(n => [n.id, n]));

/**
 * Compute the pan/zoom state that fits the graph inside a wrapper of size
 * (wrapW × wrapH), centering it, with a small initial zoom-in bias (×1 wheel
 * notch past the 80 % fit).
 *
 * @param {number} wrapW  Wrapper width in px
 * @param {number} wrapH  Wrapper height in px
 * @returns {{ zoom: number, panX: number, panY: number }}
 */
export function fitToWrapper(wrapW, wrapH) {
  const zoom = Math.max(0.4, 0.92 * wrapW / GRAPH_W);
  const panX = (wrapW - GRAPH_W * zoom) / 2;
  const panY = (wrapH - GRAPH_H * zoom) / 2;
  return { zoom, panX, panY };
}

/**
 * Apply a zoom step centered on cursor position (cx, cy), matching the
 * original wheel-notch factor (×1.12 / ÷1.12) and clamping to [0.25, 6].
 *
 * @param {{ zoom: number, panX: number, panY: number }} state  Current state
 * @param {number} deltaY   WheelEvent deltaY (negative = zoom-in)
 * @param {number} cx       Cursor X relative to the viewport element
 * @param {number} cy       Cursor Y relative to the viewport element
 * @returns {{ zoom: number, panX: number, panY: number }}
 */
export function zoomAt(state, deltaY, cx, cy) {
  const { zoom, panX, panY } = state;
  const nz = Math.min(6, Math.max(0.25, zoom * (deltaY < 0 ? 1.12 : 1 / 1.12)));
  return {
    zoom: nz,
    panX: cx - (cx - panX) * (nz / zoom),
    panY: cy - (cy - panY) * (nz / zoom),
  };
}
