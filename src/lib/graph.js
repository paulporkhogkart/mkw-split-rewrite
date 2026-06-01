// Graph layout, edge list, and pan/zoom math for the screen-graph navigator.
// Pure JS - no DOM, no Svelte. Used by both the footer graph in App.svelte
// and the standalone ScreenGraph.svelte component.

// Node = an image card: a 16:9 reference screenshot + a label strip beneath.
export const NW   = 130;   // card width
export const NIMG = 73;    // screenshot-area height (≈ 16:9 of NW)
export const NH   = 92;    // total card height (image + label strip)

// Logical canvas (content bounding box at 1× zoom).
export const GRAPH_W = 1346;
export const GRAPH_H = 316;

// Layout: nine column-groups, each a tight vertical stack capped at 3 rows tall,
// so the graph is wide-and-short (fits the edit strip at a big zoom) with little
// dead space. x = col·152, y = row·112. Columns L→R:
//   0 Unknown   1 Entry(Title/Home/Gallery)   2 Top menus(+Time Trials)
//   3 Selection(Char/Kart/Course)   4 Launch   5 Active race
//   6 In-race menus   7 Reset   8 Post-TT
// The two identical-tell families are each a single stacked column: the race
// cluster (Racing/Ghost/Race(?)) in col 5 and the reset cluster
// (Reset/Ghost Reset/Reset(?)) in col 7. Lone nodes (Unknown, Post-TT) are
// vertically centred (y=112).
export const GRAPH_NODES = [
  // col 0 - unknown (lone, far left)
  { id:"UNKNOWN",            x:0,    y:112, label:"Unknown"     },
  // col 1 - entry / Switch-overlay cluster
  { id:"TITLE",              x:152,  y:0,   label:"Title"       },
  { id:"HOME",               x:152,  y:112, label:"Home"        },
  { id:"GALLERY",            x:152,  y:224, label:"Gallery"     },
  // col 2 - top menus (+ Time Trials)
  { id:"MAIN_MENU",          x:304,  y:0,   label:"Main Menu"   },
  { id:"SINGLEPLAYER_MENU",  x:304,  y:112, label:"Singleplayer"},
  { id:"TIME_TRIALS",        x:304,  y:224, label:"Time Trials" },
  // col 3 - pre-race selection (vertical sub-flow)
  { id:"CHARACTER_SELECT",   x:456,  y:0,   label:"Character"   },
  { id:"KART_SELECT",        x:456,  y:112, label:"Kart"        },
  { id:"COURSE_SELECT",      x:456,  y:224, label:"Course"      },
  // col 4 - launch
  { id:"START_TIME_TRIAL",   x:608,  y:0,   label:"Start TT"    },
  { id:"START_REPLAY",       x:608,  y:112, label:"Start Replay"},
  // col 5 - active race (identical-tell cluster, stacked)
  { id:"RACING",             x:760,  y:0,   label:"Racing"      },
  { id:"GHOST",              x:760,  y:112, label:"Ghost"       },
  { id:"UNKNOWN_RACE_ACTIVE",x:760,  y:224, label:"Race (?)"    },
  // col 6 - in-race menus
  { id:"RACE_MENU",          x:912,  y:0,   label:"Race Menu"   },
  { id:"REPLAY_MENU",        x:912,  y:112, label:"Replay Menu" },
  { id:"REPLAY_RACE_AGAINST",x:912,  y:224, label:"Race Against"},
  // col 7 - reset (identical-tell cluster, stacked)
  { id:"RESET",              x:1064, y:0,   label:"Reset"       },
  { id:"GHOST_RESET",        x:1064, y:112, label:"Ghost Reset" },
  { id:"UNKNOWN_RESET",      x:1064, y:224, label:"Reset (?)"   },
  // col 8 - post-time-trial (lone, far right)
  { id:"POST_TIME_TRIAL",    x:1216, y:112, label:"Post-TT"     },
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
 * Point on `node`'s border along the ray from its center toward (tx, ty).
 * Used to terminate directed edges at the box edge so an arrowhead lands on the
 * border instead of being buried under the node.
 *
 * @param {{x:number,y:number}} node  Node (top-left at x,y; size NW×NH)
 * @param {number} tx  Target x (the other node's center)
 * @param {number} ty  Target y
 * @returns {{x:number,y:number}}
 */
export function edgePoint(node, tx, ty) {
  const cx = node.x + NW / 2, cy = node.y + NH / 2;
  const dx = tx - cx, dy = ty - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const sx = dx !== 0 ? (NW / 2) / Math.abs(dx) : Infinity;
  const sy = dy !== 0 ? (NH / 2) / Math.abs(dy) : Infinity;
  const s = Math.min(sx, sy);
  return { x: cx + dx * s, y: cy + dy * s };
}

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
  // Contain: fit the whole card grid within the viewport (cards are large now,
  // so fit by the tighter of width/height rather than width alone).
  const zoom = Math.max(0.25, Math.min(wrapW * 0.96 / GRAPH_W, wrapH * 0.96 / GRAPH_H));
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
