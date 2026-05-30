<script>
  import { onMount, onDestroy, afterUpdate } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { attachLogger } from "@tauri-apps/plugin-log";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { t } from "./translations.js";

  let appWindow = null;
  function winMinimize()       { appWindow?.minimize(); }
  function winToggleMaximize() { appWindow?.toggleMaximize(); }
  function winClose()          { appWindow?.close(); }

  // ── Core state ────────────────────────────────────────────────────────────────
  let version = "";
  let trackerConnected = false;
  let trackerSpawned = false;   // process launched but not yet ready
  let logs = [];
  let logEl;
  let unlisten;

  // ── Backend health ────────────────────────────────────────────────────────────
  let backendFps = 0;
  let backendScreen = "—";
  let prevBackendScreen = null;
  let lastHeartbeatTs = 0;
  let liveScore = 0.0;
  let candidateScores = {};
  let _tick = 0;
  $: backendAlive = trackerConnected && _tick >= 0 && (Date.now() - lastHeartbeatTs) < 4000;
  $: statusDot = !trackerConnected ? "#444" : backendAlive ? "#4caf50" : "#f59e0b";
  let editMode = false;   // true → "Edit Screens" view is open
  $: view = setupComplete === null ? "startup"
          : setupComplete === false ? "setup"
          : editMode ? "edit" : "main";

  // ── Selection state ───────────────────────────────────────────────────────────
  let selChar = null, selCharConf = 0;
  let selCostume = null, selCostumeConf = 0;
  let selKart = null, selKartConf = 0;
  let selCourse = null, selCourseConf = 0;

  // ── Race HUD state ────────────────────────────────────────────────────────────
  let curLap = null, totLap = null;
  let coins = null;
  let mushrooms = 0;
  let raceSplits = {};      // lap number → split time string
  let raceFinishTime = null; // total time string from finish event

  // ── Device / updater ──────────────────────────────────────────────────────────
  let devices = [];
  let configuredDevice = "";
  let deviceSwitching = false;   // true while tracker is restarting after a device change
  let _pendingDeviceSwitchTimeout = null;
  let restartNeeded = false;
  let pendingUpdate = null;
  let updateVersion = "";
  let downloadTotal = 0;
  let downloadReceived = 0;
  let updateReady = false;
  $: downloadPercent = downloadTotal > 0 ? Math.min(100, Math.round(downloadReceived / downloadTotal * 100)) : null;

  // ── Language ──────────────────────────────────────────────────────────────────
  const LANGUAGES = [
    { id: "en_uk", name: "English (UK/AU)" },
    { id: "en_us", name: "English (US)" },
    { id: "fr_fr", name: "Français (France)" },
    { id: "fr_ca", name: "Français (Canada)" },
    { id: "de",    name: "Deutsch" },
    { id: "es_es", name: "Español (España)" },
    { id: "es_la", name: "Español (Latinoamérica)" },
    { id: "it",    name: "Italiano" },
    { id: "nl",    name: "Nederlands" },
    { id: "pt_pt", name: "Português (Portugal)" },
    { id: "pt_br", name: "Português (Brasil)" },
    { id: "ru",    name: "Русский" },
    { id: "ja",    name: "日本語" },
    { id: "zh_tw", name: "中文 (繁體)" },
    { id: "zh_cn", name: "中文 (简体)" },
    { id: "ko",    name: "한국어" },
    { id: "pl",    name: "Polish/English" },
    { id: "th",    name: "Thai/English" },
  ];
  let appLanguage     = "en_uk";
  let switch2Language = "en_uk";
  let langDialogEl;
  // Staging vars for lang dialog (committed on Save)
  let langDlgApp = "en_uk";
  let langDlgSw2 = "en_uk";
  $: appLangName  = LANGUAGES.find(l => l.id === appLanguage)?.name     ?? appLanguage;
  $: sw2LangName  = LANGUAGES.find(l => l.id === switch2Language)?.name ?? switch2Language;

  // ── Sidebar panel open/close ──────────────────────────────────────────────────
  let panelOpen = { detection: true, candidates: true, selection: true, hud: true, thresholds: false, log: true };
  let graphOpen = true;

  // ── Wizard state ──────────────────────────────────────────────────────────────
  let setupComplete = null;  // null = unknown (waiting for ready), false = needs setup, true = done
  let wizardOpen = false;
  let wizardStep = "language";
  let resetConfirmPending = false;
  let screenIdx = 0, selectionIdx = 0, hudIdx = 0;

  // ── Edit Screens model ──────────────────────────────────────────────────────────
  let selectedNode = null;                      // Screen name currently open in the editor
  let activeTab = "detection";                  // "detection" | "selection" | "hud" | "templates"
  let activeRegion = { group: 0, region: 0 };   // Detection tab: selected region
  let detResetPending = false;                  // confirm gate for "reset detection to defaults"
  // Detection feed zoom/pan (so small ROIs can be adjusted precisely)
  let fZoom = 1, fPanX = 0, fPanY = 0;
  let _fPanning = false, _fStart = null;

  // Which extra tabs each node owns (beyond the always-present Detection tab).
  const TAB_LABELS = { detection:"Detection", selection:"Selection", hud:"HUD", templates:"Templates" };
  const NODE_SELECTION = { CHARACTER_SELECT:["char_name","costume"], KART_SELECT:["kart_name"], COURSE_SELECT:["course_name"] };
  const NODE_HUD       = { RACING:["lap_current","lap_total","coin_left","coin_right","finish","mushroom"] };
  const NODE_TEMPLATES = { CHARACTER_SELECT:["characters","costumes"], KART_SELECT:["karts"], COURSE_SELECT:["courses"], RACING:["mushrooms"] };
  function tabsForNode(n) {
    const t = ["detection"];
    if (NODE_SELECTION[n]) t.push("selection");
    if (NODE_HUD[n])       t.push("hud");
    if (NODE_TEMPLATES[n]) t.push("templates");
    return t;
  }
  function openNode(screenName) {
    selectedNode = screenName;
    activeTab = "detection";
    activeRegion = { group: 0, region: 0 };
    detResetPending = false;
    resetFeedZoom();
    if (!editMode) fitGraph();   // re-fit the graph to width when entering the view
    editMode = true;
    send({ type: "list_tells" });
    send({ type: "list_rois" });
  }
  function closeEdit() { editMode = false; stopRoiPoll(); }
  function openSettings() { openWizard(); }   // modal wizard, now limited to Language + Camera

  // ── Edit-view graph pan/zoom ────────────────────────────────────────────────────
  // Graph content spans ~860×248 user units. The strip is a fixed-height viewport;
  // a transform group provides zoom (wheel) + pan (drag). Initial zoom fits width.
  const GRAPH_W = 860;
  let gZoom = 1, gPanX = 0, gPanY = 0, gWrapW = 0, gWrapH = 0, gFitted = false;
  let _gPanning = false, _gMoved = false, _gStart = null;
  const GRAPH_H = 205;   // content height after the vertical compression
  $: if (gWrapW && !gFitted) {
    gZoom = Math.max(0.4, 0.8 * gWrapW / GRAPH_W);
    gPanX = (gWrapW - GRAPH_W * gZoom) / 2;
    gPanY = Math.max(0, (gWrapH - GRAPH_H * gZoom) / 2);   // center vertically, don't upscale
    gFitted = true;
  }
  function fitGraph() { gFitted = false; }    // re-fit on next measure (called on entering edit)
  function onGraphWheel(e) {
    e.preventDefault();
    const r = e.currentTarget.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    const nz = Math.min(6, Math.max(0.25, gZoom * (e.deltaY < 0 ? 1.12 : 1/1.12)));
    gPanX = cx - (cx - gPanX) * (nz / gZoom);
    gPanY = cy - (cy - gPanY) * (nz / gZoom);
    gZoom = nz;
  }
  function onGraphDown(e) { _gPanning = true; _gMoved = false; _gStart = { x:e.clientX, y:e.clientY, px:gPanX, py:gPanY }; }
  function onGraphMove(e) {
    if (!_gPanning) return;
    const dx = e.clientX - _gStart.x, dy = e.clientY - _gStart.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) _gMoved = true;
    gPanX = _gStart.px + dx; gPanY = _gStart.py + dy;
  }
  function onGraphUp() { _gPanning = false; }
  function nodeClick(id) { if (_gMoved) return; openNode(id); }   // ignore the click that ends a pan-drag

  // ── Detection tab (boolean-tree) helpers ────────────────────────────────────────
  $: editTell = tells.find(t => t.screen === selectedNode) ?? null;
  $: activeRegionObj = editTell?.groups?.[activeRegion.group]?.[activeRegion.region] ?? null;

  // All regions of the open tell, flattened for drawing on the feed canvas.
  function editRois() {
    if (!editTell) return [];
    const out = [];
    editTell.groups.forEach((grp, gi) => grp.forEach((reg, ri) => {
      const active   = gi === activeRegion.group && ri === activeRegion.region;
      const sameGrp  = gi === activeRegion.group;
      out.push({ roi: reg.roi, gi, ri, active,
                 color: active ? "#7eb8f7" : (sameGrp ? "#00ccff" : "#ffcc00") });
    }));
    return out;
  }

  function selectRegion(gi, ri) {
    activeRegion = { group: gi, region: ri };
    currentScore = null; templateImg = null; liveCropImg = null;
    syncThreshToScreen();
    if (selectedNode) {
      send({ type:"get_region_images", screen:selectedNode, group:gi, region:ri });
      send({ type:"test_region",       screen:selectedNode, group:gi, region:ri });
    }
    drawRoi();
  }
  function addRegion(gi) {
    if (!selectedNode || !editTell) return;
    const newIdx = (editTell.groups[gi]?.length ?? 0);   // appended at the end of the group
    send({ type:"add_region", screen:selectedNode, group:gi });
    activeRegion = { group: gi, region: newIdx };
  }
  function addGroup() {
    if (!selectedNode || !editTell) return;
    const newGi = editTell.groups.length;
    send({ type:"add_group", screen:selectedNode });
    activeRegion = { group: newGi, region: 0 };
  }
  function removeActiveRegion() {
    if (!selectedNode) return;
    send({ type:"remove_region", screen:selectedNode, group:activeRegion.group, region:activeRegion.region });
    activeRegion = { group: 0, region: 0 };
  }
  function onKindChange(kind) {
    if (!selectedNode) return;
    const extra = kind === "dark_loading" && activeRegionObj && !activeRegionObj.icon_roi
      ? { icon_roi: [1700, 920, 1870, 1030] } : {};
    send({ type:"update_region", screen:selectedNode, group:activeRegion.group, region:activeRegion.region, kind, ...extra });
  }
  function recaptureRegion() {
    if (!selectedNode) return;
    capturingTemplate = true; currentScore = null;
    send({ type:"capture_region_template", screen:selectedNode, group:activeRegion.group, region:activeRegion.region });
  }
  function resetDetection() {
    if (!selectedNode) return;
    send({ type:"reset_tell", screen:selectedNode });
    activeRegion = { group: 0, region: 0 };
    detResetPending = false;
  }

  let tells = [];
  let rois = {};
  let currentScore = null;
  let capturingTemplate = false;
  let templateImg = null;
  let liveCropImg = null;

  // ── ROI drag editing ──────────────────────────────────────────────────────────
  let dragging = false, dragHandle = null, dragStartMouse = null, dragStartRoi = null;
  let hoveredHandle = null;
  let liveRoiCrop = null;
  let assetTemplateImg = null, assetLiveCrop = null;
  let templateCategory = "characters", templateItemIdx = 0;
  let _roiPollTimer = null;
  let currentBinaryThresh = 170;
  let activeRoiKey = "primary";
  const HANDLE_HIT_RADIUS = 9;

  // ── Camera ────────────────────────────────────────────────────────────────────
  let mainVideoEl = null, wizVideoEl = null, canvasEl = null, videoStream = null;
  let cameraStatus = "idle";
  let trackerCameraPaused = false;
  let browserDevices = [], selectedBrowserDeviceId = "";
  let audioDevices = [];
  let selectedAudioDeviceId = "";
  let pythonCameraStatus = "idle", pythonCameraError = "";
  let engineFrame = null;
  let _feedPollTimer = null;
  let pythonFrameW = 1920, pythonFrameH = 1080;

  // ── Feed audio / video controls ───────────────────────────────────────────────
  let feedVolume    = 0.5;    // 0–1
  let feedMuted     = false;
  let feedVideoHidden = false;
  let _audioCtx  = null;
  let _gainNode  = null;
  let _hasAudio  = false;    // true if current stream has audio tracks

  function _setupAudio() {
    _teardownAudio();
    if (!videoStream) return;
    if (!setupComplete) return;
    _hasAudio = videoStream.getAudioTracks().length > 0;
    if (!_hasAudio) return;
    _audioCtx = new AudioContext();
    _gainNode = _audioCtx.createGain();
    _gainNode.gain.value = feedMuted ? 0 : feedVolume;
    _audioCtx.createMediaStreamSource(videoStream).connect(_gainNode);
    _gainNode.connect(_audioCtx.destination);
  }

  function _teardownAudio() {
    if (_audioCtx) { _audioCtx.close(); _audioCtx = null; _gainNode = null; }
    _hasAudio = false;
  }

  // Keep gain in sync whenever mute or volume changes
  $: if (_gainNode) _gainNode.gain.value = feedMuted ? 0 : feedVolume;

  // ── Wizard step definitions ───────────────────────────────────────────────────
  const FIRST_TIME_STEPS = ["language", "camera", "done"];
  // Post-setup, the ⚙ modal is a slim Settings panel: Language + Camera only.
  // Screen/tell/HUD/template editing now lives in the Edit Screens view.
  const RERUN_STEPS      = ["language", "camera"];
  const STEP_LABELS = {
    language: "Language", camera: "Camera", screens: "Screens",
    selection: "Selection", hud: "HUD", templates: "Templates", done: "Done",
  };
  $: STEPS = setupComplete ? RERUN_STEPS : FIRST_TIME_STEPS;

  // ── Asset data ────────────────────────────────────────────────────────────────
  const ASSET_CATEGORIES = [
    { key: "characters", label: "Characters" },
    { key: "karts",      label: "Karts"      },
    { key: "courses",    label: "Courses"    },
    { key: "costumes",   label: "Costumes"   },
    { key: "mushrooms",  label: "Mushrooms"  },
  ];

  function toFilename(n) { return n.toLowerCase().replace(/[?'.]/g, "").replace(/\s+/g, "_"); }
  function fileToDisplayName(f) { return f.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()); }

  const ASSET_ITEMS = {
    characters: [
      "Baby Daisy","Baby Luigi","Baby Mario","Baby Peach","Baby Rosalina",
      "Birdo","Bowser","Bowser Jr","Cataquack","Chargin' Chuck","Cheep Cheep",
      "Coin Coffer","Conkdor","Cow","Daisy","Dolphin","Donkey Kong","Dry Bones",
      "Fish Bone","Goomba","Hammer Bro","King Boo","Koopa Troopa","Lakitu",
      "Luigi","Mario","Monty Mole","Nabbit","Para-Biddybud","Pauline","Peach",
      "Peepa","Penguin","Pianta","Piranha Plant","Pokey","Rocky Wrench","Rosalina",
      "Shy Guy","Sidestepper","Snowman","Spike","Stingby","Swoop","Toad",
      "Toadette","Waluigi","Wario","Wiggler","Yoshi",
    ].map(n => ({ name: n, file: toFilename(n) })),
    karts: [
      "b_dasher","baby_blooper","big_horn","billdozer","blastronaut_iii",
      "bowser_bruiser","buggybud","bumble_v","carpet_flyer","chargin_truck",
      "cloud_9","cute_scoot","dolphin_dasher","dread_sled","fin_twin",
      "funky_dorrie","hot_rod","hyper_pipe","junkyard_hog","lil_dumpy",
      "lobster_roller","loco_moto","mach_rocket","mecha_trike","pipe_frame",
      "plushbuggy","rally_bike","rally_kart","rally_romper","rallygator",
      "reel_racer","ribbit_revster","roadster_royale","rob_hog","standard_bike",
      "standard_kart","stellar_sled","tune_thumper","w-twin_chopper","zoom_buggy",
    ].map(f => ({ name: fileToDisplayName(f), file: f })),
    courses: [
      "Acorn Heights","Airship Fortress","Boo Cinema","Bowser's Castle",
      "Cheep Cheep Falls","Choco Mountain","Crown City","Dandelion Depths",
      "Desert Hills","Dino Dino Jungle","DK Pass","DK Spaceport","Dry Bones Burnout",
      "Faraway Oasis","Great ? Block Ruins","Koopa Troopa Beach","Mario Bros. Circuit",
      "Mario Circuit","Moo Moo Meadows","Peach Beach","Peach Stadium","Rainbow Road",
      "Salty Salty Speedway","Shy Guy Bazaar","Sky-High Sundae","Starview Peak",
      "Toad's Factory","Wario Stadium","Wario's Galleon","Whistlestop Summit",
    ].map(n => ({ name: n, file: toFilename(n) })),
    costumes: [
      "aero","all-terrain","aristocrat","aurora","aviator","biker","biker_jr",
      "burger_bud","conductor","cowboy","dune_rider","engineer","explorer",
      "farmer","fisherman","food_slinger","gondolier","happi","mariachi",
      "matsuri","mechanic","oasis","pirate","pit_crew","pro_racer","road_ruffian",
      "runner","sailor","sightseeing","slope_styler","soft_server","supercharged",
      "swimwear","touring","vacation","wampire","wicked_wasp","work_crew","yukata",
    ].map(f => ({ name: fileToDisplayName(f), file: f })),
    mushrooms: [
      { name: "3 Mushrooms", file: "3mush" },
      { name: "2 Mushrooms", file: "2mush" },
      { name: "1 Mushroom",  file: "1mush" },
    ],
  };

  const ASSET_ROI_KEYS = {
    characters: "char_name", karts: "kart_name",
    courses: "course_name", costumes: "costume", mushrooms: "mushroom",
  };
  const ASSET_HINTS = {
    characters: n => `Navigate to character select in-game and choose ${n}.`,
    karts:      n => `Navigate to kart select in-game and choose ${n}.`,
    courses:    n => `Navigate to course select in-game and choose ${n}.`,
    costumes:   n => `Navigate to character select, pick a character that has the ${n} costume, and equip it.`,
    mushrooms:  n => `Start a time trial race with ${n} and wait for racing to begin.`,
  };

  // ── Screen graph data ─────────────────────────────────────────────────────────
  const SCREEN_NAMES = [
    "TITLE","HOME","MAIN_MENU","SINGLEPLAYER_MENU","TIME_TRIALS",
    "CHARACTER_SELECT","KART_SELECT","COURSE_SELECT",
    "START_TIME_TRIAL","START_REPLAY",
    "RACING","RACE_MENU","REPLAY_MENU","REPLAY_RACE_AGAINST",
    "RESET","POST_TIME_TRIAL","GALLERY",
  ];
  const TELL_GROUP_ALIASES = {
    RACING: ["GHOST","UNKNOWN_RACE_ACTIVE"],
    RESET:  ["GHOST_RESET","UNKNOWN_RESET"],
  };
  const TELL_GROUP_NOTES = {
    RACING: "This ROI setup also applies to Ghost Race and Unknown Race states — they share the same detection tell.",
    RESET:  "This ROI setup also applies to Ghost Reset and Unknown Reset states — they share the same detection tell.",
  };
  const SCREEN_LABELS = {
    TITLE:"Title Screen",HOME:"Home / Profile Select",MAIN_MENU:"Main Menu",
    SINGLEPLAYER_MENU:"Single Player Mode Menu",TIME_TRIALS:"Time Trials Menu",
    CHARACTER_SELECT:"Character Selection",KART_SELECT:"Kart & Parts Selection",
    COURSE_SELECT:"Course Selection",START_TIME_TRIAL:"Race Countdown (Time Trial)",
    START_REPLAY:"Ghost Race Countdown",RACING:"In Race",GHOST:"Ghost Race",
    UNKNOWN_RACE_ACTIVE:"Active Race (Unknown Type)",RACE_MENU:"Race Pause Menu",
    REPLAY_MENU:"Ghost Replay Menu",REPLAY_RACE_AGAINST:"Race Against Ghost",
    RESET:"Reset / Retry Screen",GHOST_RESET:"Ghost Reset Screen",
    POST_TIME_TRIAL:"Post-Race Results",GALLERY:"Gallery Browser",UNKNOWN:"Unknown",
  };
  const SCREEN_HINTS = {
    TITLE:"The startup title/logo screen.",
    HOME:"The player profile selection screen shown after title.",
    MAIN_MENU:"Main menu with single player, multiplayer, etc.",
    SINGLEPLAYER_MENU:"Single player mode selector (Time Trials, Grand Prix…).",
    TIME_TRIALS:"Time trials mode menu — character and course selection.",
    CHARACTER_SELECT:"The character/driver selection screen.",
    KART_SELECT:"The kart body, tires, and glider selection screen.",
    COURSE_SELECT:"The track/course selection grid.",
    START_TIME_TRIAL:"The 3-2-1 countdown before a time trial race begins.",
    START_REPLAY:"The 3-2-1 countdown before a ghost race begins.",
    RACING:"Active racing — coin counter and flag icon visible bottom-left. Covers all race types.",
    GHOST:"Racing against a ghost replay.",
    UNKNOWN_RACE_ACTIVE:"An active race detected without clear type identification.",
    RACE_MENU:"The in-race pause menu.",
    REPLAY_MENU:"The ghost replay options menu.",
    REPLAY_RACE_AGAINST:"The 'Race Against Ghost' options menu.",
    RESET:"The reset/retry confirmation screen.",
    GHOST_RESET:"The ghost race reset screen.",
    POST_TIME_TRIAL:"The results screen displayed after finishing a time trial.",
    GALLERY:"The replay gallery / save data browser.",
  };
  const SELECTION_ROIS = [
    { key:"char_name",   label:"Character Name",  hint:"Character name text, bottom-right panel on character select screen." },
    { key:"costume",     label:"Costume Name",     hint:"Costume/variant text below character name." },
    { key:"kart_name",   label:"Kart Name",        hint:"Kart body name text on kart selection screen." },
    { key:"course_name", label:"Course Name",      hint:"Course name displayed in the course selection screen." },
  ];
  const HUD_ROIS = [
    { key:"lap_current", label:"Lap Counter (current)", hint:"Current lap digit — bottom-left race HUD." },
    { key:"lap_total",   label:"Lap Counter (total)",   hint:"Total laps digit next to current lap." },
    { key:"coin_left",   label:"Coin Digit (tens)",     hint:"Left/tens coin counter digit." },
    { key:"coin_right",  label:"Coin Digit (units)",    hint:"Right/units coin counter digit." },
    { key:"finish",      label:"Finish Position",       hint:"1st / 2nd / 3rd finish overlay, top-right area." },
    { key:"mushroom",    label:"Mushroom Count",        hint:"Mushroom stack indicator, top-left area." },
  ];
  const HUD_ROI_CONFIG_KEYS = {
    lap_current:"lap_current_roi", lap_total:"lap_total_roi",
    coin_left:"coin_left_roi",    coin_right:"coin_right_roi",
    finish:"finish_roi",           mushroom:"mushroom_roi",
  };
  const SELECTION_ROI_CONFIG_KEYS = {
    char_name:"char_name_roi", costume:"costume_roi",
    kart_name:"kart_name_roi", course_name:"course_name_roi",
  };

  // Screen graph nodes (NW×NH boxes)
  const NW = 88, NH = 24;
  const GRAPH_NODES = [
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
  // All other →HOME edges are present but rendered dimmed.
  // The dynamic prevBackendScreen↔HOME edge is drawn separately at full opacity.
  const GRAPH_EDGES = [
    // UNKNOWN: no edges (isolated indicator node)
    // HOME constant two-way connections
    ["HOME","TITLE"],["HOME","GALLERY"],["TITLE","HOME"],["GALLERY","HOME"],
    // all other →HOME (dimmed in renderer)
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
  $: graphNodeMap = Object.fromEntries(GRAPH_NODES.map(n => [n.id, n]));
  // When on HOME, compute all screens reachable from prevBackendScreen via
  // GRAPH_EDGES — these match what the Python detector will actually scan.
  $: homeContextScreens = (backendScreen === "HOME" && prevBackendScreen)
    ? new Set(GRAPH_EDGES.filter(([f, t]) => f === prevBackendScreen && t !== "HOME").map(([,t]) => t))
    : new Set();
  function edgePath(from, to) {
    const a = graphNodeMap[from], b = graphNodeMap[to];
    if (!a || !b) return "";
    return `M${a.x+NW/2},${a.y+NH/2} L${b.x+NW/2},${b.y+NH/2}`;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function send(msg) { invoke("send_to_tracker", { message: JSON.stringify(msg) }).catch(() => {}); }

  function pushLog(line) {
    logs = [...logs.slice(-299), line];
    setTimeout(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; }, 0);
  }

  // Forward browser console output to the log pane so everything visible in the
  // VS Code / Tauri devtools console is also visible here.
  (function _interceptConsole() {
    const _fmt = (args) => args.map(a =>
      (a instanceof Error) ? `${a.message}` :
      (typeof a === 'object' && a !== null) ? JSON.stringify(a) : String(a)
    ).join(' ');
    for (const [level, prefix] of [['log','[js]'],['warn','[warn]'],['error','[error]']]) {
      const _orig = console[level].bind(console);
      console[level] = (...args) => { _orig(...args); pushLog(`${prefix} ${_fmt(args)}`); };
    }
  })();

  function handleMsg(msg) {
    switch (msg.type) {
      case "stderr":
        pushLog(`[err] ${msg.line}`);
        break;
      case "spawned":
        trackerSpawned = true;
        pushLog(`[app] engine process launched ${_elapsed()} — waiting for Python to initialise (Windows may be scanning files)`);
        break;
      case "ready":
        trackerSpawned = true;
        trackerConnected = true;
        lastHeartbeatTs = Date.now();
        pushLog(`[app] tracker connected ${_elapsed()} — setup_complete=${msg.setup_complete}`);
        if (_pendingDeviceSwitchTimeout) {
          clearTimeout(_pendingDeviceSwitchTimeout);
          _pendingDeviceSwitchTimeout = null;
        }
        deviceSwitching = false;
        if (msg.app_language)     appLanguage     = msg.app_language;
        if (msg.switch2_language) switch2Language = msg.switch2_language;
        send({ type: "list_devices" });
        send({ type: "list_tells" });
        send({ type: "list_rois" });
        if (msg.setup_complete) {
          setupComplete = true;
          // Camera open is deferred until devices_list arrives so both browser
          // and Python always open the same physical device.
        } else {
          setupComplete = false;
          pushLog("[app] first-time setup required");
        }
        break;
      case "camera_status":
        pythonCameraStatus = msg.ok ? "ok" : "error";
        pythonCameraError  = msg.error ?? "";
        pushLog(msg.ok
          ? `[cam] opened: ${msg.device || "unknown"} ${msg.width}x${msg.height}`
          : `[cam] failed: ${msg.error}`);
        if (!msg.ok) engineFrame = null;   // clear stale frame so error state shows immediately
        if (msg.ok) {
          if (msg.width  > 0) pythonFrameW = msg.width;
          if (msg.height > 0) pythonFrameH = msg.height;
          trackerCameraPaused = false;
          if (msg.device) {
            const pyDev = msg.device.toLowerCase();
            const match = browserDevices.find(d => {
              const clean = d.label.replace(/\s*\([0-9a-f:]+\)\s*$/i,"").trim().toLowerCase();
              return clean === pyDev || clean.includes(pyDev) || pyDev.includes(clean);
            });
            if (match && match.deviceId !== selectedBrowserDeviceId) {
              // Python opened a different device — force the browser to match it
              selectedBrowserDeviceId = match.deviceId;
              if (wizardStep === "camera") startCamera(match.deviceId);
            } else if (wizardStep === "camera" && cameraStatus === "idle") {
              startCamera(selectedBrowserDeviceId || undefined);
            }
          } else if (wizardStep === "camera" && cameraStatus === "idle") {
            startCamera(selectedBrowserDeviceId || undefined);
          }
        }
        break;
      case "frame_data":
        // Discard frames that arrive while Python is mid-switch — they belong to the old camera.
        if (pythonCameraStatus !== "opening") engineFrame = `data:image/jpeg;base64,${msg.data}`;
        break;
      case "heartbeat":
        backendFps      = msg.fps    ?? 0;
        backendScreen   = msg.screen ?? "—";
        lastHeartbeatTs = Date.now();
        liveScore       = msg.current_score ?? 0;
        candidateScores = msg.candidate_scores ?? {};
        break;
      case "camera_paused":
        _pauseIntent = "";
        break;
      case "camera_resumed":
        trackerCameraPaused = false;
        break;
      case "template_images":
        if (msg.screen === selectedNode && msg.group === activeRegion.group && msg.region === activeRegion.region) {
          templateImg = msg.template_img ? `data:image/png;base64,${msg.template_img}` : null;
          liveCropImg = msg.live_crop    ? `data:image/png;base64,${msg.live_crop}`    : null;
        }
        break;
      case "template_score":
        if (msg.screen === selectedNode && msg.group === activeRegion.group && msg.region === activeRegion.region) {
          currentScore = { screen:msg.screen, score:msg.score, threshold:msg.threshold, matched:msg.matched };
          if (msg.template_img) templateImg = `data:image/png;base64,${msg.template_img}`;
          if (msg.live_crop)    liveCropImg  = `data:image/png;base64,${msg.live_crop}`;
        }
        capturingTemplate = false;
        break;
      case "template_saved":
        currentScore = { screen:msg.screen, score:msg.score, threshold:msg.threshold, matched:msg.matched };
        capturingTemplate = false;
        break;
      case "tells_list":
        tells = msg.tells ?? [];
        syncThreshToScreen();
        break;
      case "rois_list":
        rois = msg.rois ?? {};
        break;
      case "roi_preview":
        liveRoiCrop = msg.data ? `data:image/png;base64,${msg.data}` : null;
        break;
      case "asset_preview":
        if (msg.category === templateCategory) {
          assetTemplateImg = msg.template_img ? `data:image/png;base64,${msg.template_img}` : null;
          assetLiveCrop    = msg.live_crop    ? `data:image/png;base64,${msg.live_crop}`    : null;
        }
        break;
      case "asset_saved":
        capturingTemplate = false;
        if (msg.category === templateCategory)
          send({ type:"get_asset_template", category:msg.category, item_name:msg.item_name });
        break;
      case "devices_list":
        devices = msg.devices ?? [];
        configuredDevice = msg.configured ?? "";
        // Restore saved audio device preference by label-matching against current devices.
        // audioDevices is already populated from onMount's loadBrowserDevices() call.
        if (msg.audio_label) {
          if (msg.audio_label === "none") {
            selectedAudioDeviceId = "none";
          } else {
            const savedAud = audioDevices.find(d =>
              d.label && d.label.toLowerCase() === msg.audio_label.toLowerCase()
            );
            if (savedAud) selectedAudioDeviceId = savedAud.deviceId;
          }
        }
        pushLog(`[devices] found ${devices.length}: ${devices.join(", ") || "none"}`);
        // On a normal (post-setup) launch, now that we have both the Python device
        // list and the browser device list (loadBrowserDevices runs next), pick a
        // matching pair and open both simultaneously.
        if (setupComplete === true && cameraStatus === "idle")
          _openMatchedCameras();
        break;
      case "screen_change":
        pushLog(`[screen] ${msg.from} → ${msg.to}`);
        // HOME and its direct children (GALLERY, TITLE) are part of the Switch overlay
        // cluster — leaving them back to HOME should not overwrite the game-state context.
        const _homeCluster = new Set(["UNKNOWN","HOME","GALLERY"]);
        if (msg.from && !_homeCluster.has(msg.from)) {
          prevBackendScreen = msg.from;
        }
        backendScreen = msg.to ?? backendScreen;
        break;
      case "selection_update":
        selChar    = msg.character ?? null; selCharConf    = msg.char_conf    ?? 0;
        selCostume = msg.costume   ?? null; selCostumeConf = msg.costume_conf ?? 0;
        selKart    = msg.kart      ?? null; selKartConf    = msg.kart_conf    ?? 0;
        selCourse  = msg.course    ?? null; selCourseConf  = msg.course_conf  ?? 0;
        pushLog(`[sel] ${msg.character ?? "—"} / ${msg.kart ?? "—"} / ${msg.course ?? "—"}${msg.costume ? ` / ${msg.costume}` : ""}`);
        break;
      case "lap_update":
        // Reset splits when lap 1 starts — marks the beginning of a fresh race
        if (msg.current === 1) { raceSplits = {}; raceFinishTime = null; }
        curLap = msg.current; totLap = msg.total;
        if (msg.split && msg.current != null)
          raceSplits = { ...raceSplits, [msg.current - 1]: msg.split };
        pushLog(`[lap] ${msg.current}/${msg.total}${msg.split ? `  ${msg.split}` : ""}`);
        break;
      case "coin_update": coins = msg.coins; pushLog(`[coins] ${msg.coins}`); break;
      case "mush_update": mushrooms = msg.count; pushLog(`[mush] ${msg.count}`); break;
      case "split_recorded":
        raceSplits = { ...raceSplits, [msg.lap]: msg.time };
        if (msg.is_final) raceFinishTime = msg.time;
        break;
      case "finish":
        raceFinishTime = msg.total_time ?? raceFinishTime;
        if (msg.splits) raceSplits = { ...raceSplits, ...msg.splits };
        pushLog(`[finish] ${msg.result}  ${msg.total_time ?? "—"}`);
        break;
      case "error":  pushLog(`[ERR] ${msg.message}`); break;
    }
  }

  // ── Camera ────────────────────────────────────────────────────────────────────
  // Open the browser camera and Python camera on the same physical device.
  // Called once on every normal (post-setup) launch after devices_list arrives.
  async function _openMatchedCameras() {
    await loadBrowserDevices();
    // Only auto-select a browser device when none is explicitly chosen yet.
    // If selectedBrowserDeviceId is already set (e.g. user picked from dropdown),
    // honour it — don't override with a configuredDevice name match.
    if (!selectedBrowserDeviceId && browserDevices.length > 0) {
      if (configuredDevice) {
        const lower = configuredDevice.toLowerCase();
        const match = browserDevices.find(d => {
          const clean = d.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim().toLowerCase();
          return clean === lower || clean.includes(lower) || lower.includes(clean);
        });
        selectedBrowserDeviceId = match ? match.deviceId : browserDevices[0].deviceId;
      } else {
        selectedBrowserDeviceId = browserDevices[0].deviceId;
      }
    }
    // Start the browser camera first so permission is granted before Python opens.
    await startCamera(selectedBrowserDeviceId || undefined);
    // Now resolve the Python device name from the browser label we actually got.
    const chosen = browserDevices.find(d => d.deviceId === selectedBrowserDeviceId);
    if (chosen && devices.length > 0) {
      const cleanLabel = chosen.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim();
      const match = devices.find(d =>
        d.toLowerCase() === cleanLabel.toLowerCase() ||
        cleanLabel.toLowerCase().includes(d.toLowerCase()) ||
        d.toLowerCase().includes(cleanLabel.toLowerCase())
      );
      const pyDevice = match ?? configuredDevice;
      if (pyDevice && pyDevice !== configuredDevice) {
        configuredDevice = pyDevice;
        send({ type:"update_config", key:"camera_device", value:pyDevice });
      }
    }
    send({ type:"open_camera" });
  }

  async function loadBrowserDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      browserDevices = all.filter(d => d.kind === "videoinput");
      audioDevices   = all.filter(d => d.kind === "audioinput");
      if (!selectedBrowserDeviceId && browserDevices.length > 0)
        selectedBrowserDeviceId = browserDevices[0].deviceId;
    } catch { /* ignore */ }
  }

  async function startCamera(deviceId) {
    stopCamera(); cameraStatus = "requesting";
    const vc = deviceId
      ? { deviceId:{ exact:deviceId }, width:{ ideal:1920 }, height:{ ideal:1080 } }
      : { width:{ ideal:1920 }, height:{ ideal:1080 } };
    try {
      // Resolve audio device in priority order:
      //   1. "none" sentinel — user explicitly wants video-only, skip all audio logic
      //   2. Specific device ID chosen by user this session
      //   3. Audio input sharing a non-empty groupId with the chosen video device
      //   4. Video-only fallback — never grab the default mic
      const audioExplicitNone = selectedAudioDeviceId === "none";
      let resolvedAudioId = (!audioExplicitNone && selectedAudioDeviceId) ? selectedAudioDeviceId : null;
      if (!resolvedAudioId && !audioExplicitNone && deviceId) {
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          const vid = all.find(d => d.kind === "videoinput" && d.deviceId === deviceId);
          // groupId is "" when permissions haven't been granted yet — skip in that case
          // to avoid accidentally matching unrelated devices that also have groupId "".
          if (vid && vid.groupId) {
            const aud = all.find(d => d.kind === "audioinput" && d.groupId === vid.groupId);
            if (aud) resolvedAudioId = aud.deviceId;
          }
        } catch { /* no audio pairing */ }
      }
      if (resolvedAudioId) {
        const rawAudio = {
          deviceId:         { exact: resolvedAudioId },
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl:  false,
        };
        try {
          videoStream = await navigator.mediaDevices.getUserMedia({ video:vc, audio: rawAudio });
        } catch {
          videoStream = await navigator.mediaDevices.getUserMedia({ video:vc });
        }
      } else {
        // No paired audio found — video only. Never grab a random mic.
        videoStream = await navigator.mediaDevices.getUserMedia({ video:vc });
      }
      cameraStatus = "ok";
      await loadBrowserDevices();
      // If audio labels are blank (mic permission not yet granted), make a brief
      // audio-only request purely to unlock enumerateDevices labels, then stop it.
      // This never keeps a mic stream open — it's discarded immediately.
      if (audioDevices.some(d => !d.label)) {
        try {
          const tmp = await navigator.mediaDevices.getUserMedia({ audio: true });
          tmp.getTracks().forEach(t => t.stop());
          await loadBrowserDevices();
        } catch { /* mic denied — labels stay blank */ }
      }
      // Sync selectedAudioDeviceId to whatever was actually captured so the
      // Audio dropdown in Detection reflects the current state.
      const audioTracks = videoStream.getAudioTracks();
      const capturedAudioId = audioTracks.length > 0 ? (audioTracks[0].getSettings().deviceId ?? "") : "";
      if (capturedAudioId) selectedAudioDeviceId = capturedAudioId;
      _setupAudio();
    } catch (err) {
      videoStream = null;
      cameraStatus = (err.name === "NotReadableError" || err.name === "TrackStartError") ? "busy" : "error";
    }
  }

  function stopCamera() {
    _teardownAudio();
    if (videoStream) { videoStream.getTracks().forEach(t => t.stop()); videoStream = null; }
    cameraStatus = "idle";
  }

  // Engine feed poll — runs continuously at 100ms so main view always has a fresh frame
  function startFeedPoll() {
    if (_feedPollTimer) return;
    _feedPollTimer = setInterval(() => {
      if (trackerConnected) send({ type:"capture_frame", scale:0.333 });
    }, 100);
  }
  function stopFeedPoll() {
    if (_feedPollTimer) { clearInterval(_feedPollTimer); _feedPollTimer = null; }
  }

  // ── ROI overlay helpers ───────────────────────────────────────────────────────
  function getAllRoisForTell(tell) {
    if (!tell) return [];
    const r = [];
    if (tell.roi) r.push({ key:"primary", roi:tell.roi, type:"primary", label:"Primary" });
    (tell.required_also ?? []).forEach((ra, i) => {
      if (ra.roi) r.push({ key:`and_${i}`, roi:ra.roi, type:"and", label:`AND ${i+1}` });
    });
    if (tell.alt_roi && tell.alt_image_path)
      r.push({ key:"alt", roi:tell.alt_roi, type:"or", label:"OR Alt" });
    return r;
  }

  function getCurrentRoi() {
    if (view === "edit") {
      if (activeTab === "detection") return activeRegionObj?.roi ?? null;
      return null;   // selection/hud tab ROI editing lands in a later increment
    }
    if (wizardStep === "screens") {
      const tell = tells.find(t => t.screen === SCREEN_NAMES[screenIdx]);
      if (!tell) return null;
      if (activeRoiKey === "primary") return tell.roi ?? null;
      if (activeRoiKey === "alt")     return tell.alt_roi ?? null;
      if (activeRoiKey.startsWith("and_"))
        return tell.required_also?.[parseInt(activeRoiKey.slice(4))]?.roi ?? null;
      return tell.roi ?? null;
    }
    if (wizardStep === "selection") return rois[SELECTION_ROIS[selectionIdx]?.key] ?? null;
    if (wizardStep === "hud")       return rois[HUD_ROIS[hudIdx]?.key] ?? null;
    if (wizardStep === "templates") return rois[ASSET_ROI_KEYS[templateCategory]] ?? null;
    return null;
  }

  function getTransform() {
    if (!canvasEl) return null;
    const rect = canvasEl.getBoundingClientRect();
    // Base (untransformed) layout size — clientWidth/Height ignore the CSS zoom,
    // so ROI geometry stays in unscaled canvas px and the zoom is applied on top.
    const bw = canvasEl.clientWidth, bh = canvasEl.clientHeight;
    if (!bw || !bh) return null;
    const isEdit = view === "edit";
    const z = isEdit ? fZoom : 1, px = isEdit ? fPanX : 0, py = isEdit ? fPanY : 0;
    const pyw = pythonFrameW||1920, pyh = pythonFrameH||1080;
    const eAR = bw/bh, vAR = pyw/pyh;
    let rendW, rendH, ox, oy;
    if (vAR > eAR) { rendW=bw; rendH=bw/vAR; ox=0; oy=(bh-rendH)/2; }
    else            { rendH=bh; rendW=bh*vAR; ox=(bw-rendW)/2; oy=0; }
    return { ox, oy, sx:rendW/pyw, sy:rendH/pyh, rect, z, px, py };
  }
  // The canvas overlay is NOT zoomed (so lines stay crisp); instead the zoom is
  // folded into the drawing/hit math so boxes track the zoomed video underneath.
  function frameToCanvas(fx, fy, t) {
    return { cx: t.px + (t.ox + fx*t.sx)*t.z, cy: t.py + (t.oy + fy*t.sy)*t.z };
  }
  function canvasToFrame(clientX, clientY, t) {
    const mx = clientX - t.rect.left, my = clientY - t.rect.top;
    return { fx: ((mx - t.px)/t.z - t.ox)/t.sx, fy: ((my - t.py)/t.z - t.oy)/t.sy };
  }
  function _clampPan() {
    if (!canvasEl) return;
    const W = canvasEl.clientWidth, H = canvasEl.clientHeight, OVER = 100;
    fPanX = Math.min(OVER, Math.max(W*(1-fZoom) - OVER, fPanX));
    fPanY = Math.min(OVER, Math.max(H*(1-fZoom) - OVER, fPanY));
  }

  function getHandlePositions(roi) {
    if (!roi||roi.length<4) return [];
    const [x1,y1,x2,y2]=roi, mx=(x1+x2)/2, my=(y1+y2)/2;
    return [
      {id:"tl",fx:x1,fy:y1,cursor:"nw-resize"},{id:"tr",fx:x2,fy:y1,cursor:"ne-resize"},
      {id:"bl",fx:x1,fy:y2,cursor:"sw-resize"},{id:"br",fx:x2,fy:y2,cursor:"se-resize"},
      {id:"t", fx:mx,fy:y1,cursor:"n-resize"}, {id:"b", fx:mx,fy:y2,cursor:"s-resize"},
      {id:"l", fx:x1,fy:my,cursor:"w-resize"}, {id:"r", fx:x2,fy:my,cursor:"e-resize"},
    ];
  }

  function hitTest(clientX, clientY, roi) {
    const t = getTransform();
    if (!t||!roi||roi.length<4) return null;
    const mx = clientX - t.rect.left, my = clientY - t.rect.top;
    for (const h of getHandlePositions(roi)) {
      const c = frameToCanvas(h.fx, h.fy, t);
      if (Math.hypot(mx-c.cx, my-c.cy) <= HANDLE_HIT_RADIUS)
        return { handle:h.id, cursor:h.cursor };
    }
    const a = frameToCanvas(roi[0], roi[1], t), b = frameToCanvas(roi[2], roi[3], t);
    if (mx>=a.cx&&mx<=b.cx&&my>=a.cy&&my<=b.cy) return { handle:"move", cursor:"move" };
    return null;
  }

  function applyDrag(roi, handle, dx, dy) {
    let [x1,y1,x2,y2]=roi;
    const MIN=4, W=pythonFrameW||1920, H=pythonFrameH||1080;
    if      (handle==="tl")   { x1+=dx; y1+=dy; }
    else if (handle==="tr")   { x2+=dx; y1+=dy; }
    else if (handle==="bl")   { x1+=dx; y2+=dy; }
    else if (handle==="br")   { x2+=dx; y2+=dy; }
    else if (handle==="t")    { y1+=dy; }
    else if (handle==="b")    { y2+=dy; }
    else if (handle==="l")    { x1+=dx; }
    else if (handle==="r")    { x2+=dx; }
    else if (handle==="move") { x1+=dx; x2+=dx; y1+=dy; y2+=dy; }
    x1=Math.max(0,Math.min(x1,W-MIN)); x2=Math.max(x1+MIN,Math.min(x2,W));
    y1=Math.max(0,Math.min(y1,H-MIN)); y2=Math.max(y1+MIN,Math.min(y2,H));
    return [Math.round(x1),Math.round(y1),Math.round(x2),Math.round(y2)];
  }

  function updateCurrentRoi(roi) {
    if (view === "edit") {
      if (activeTab === "detection" && selectedNode) {
        const g = activeRegion.group, r = activeRegion.region, sn = selectedNode;
        tells = tells.map(t => t.screen !== sn ? t : { ...t,
          groups: t.groups.map((grp, gi) => gi !== g ? grp
            : grp.map((reg, ri) => ri !== r ? reg : { ...reg, roi })) });
      }
      return;
    }
    if (wizardStep==="screens") {
      const sn=SCREEN_NAMES[screenIdx];
      if (activeRoiKey==="primary") {
        tells=tells.map(t=>t.screen===sn?{...t,roi}:t);
      } else if (activeRoiKey==="alt") {
        tells=tells.map(t=>t.screen===sn?{...t,alt_roi:roi}:t);
      } else if (activeRoiKey.startsWith("and_")) {
        const idx=parseInt(activeRoiKey.slice(4));
        tells=tells.map(t=>{
          if (t.screen!==sn) return t;
          return {...t,required_also:(t.required_also??[]).map((ra,i)=>i===idx?{...ra,roi}:ra)};
        });
      }
    } else if (wizardStep==="selection") {
      const k=SELECTION_ROIS[selectionIdx]?.key;
      if (k) rois={...rois,[k]:roi};
    } else if (wizardStep==="hud") {
      const k=HUD_ROIS[hudIdx]?.key;
      if (k) rois={...rois,[k]:roi};
    }
  }

  function saveCurrentRoi(roi) {
    if (view === "edit") {
      if (activeTab === "detection" && selectedNode)
        send({ type:"update_region", screen:selectedNode, group:activeRegion.group, region:activeRegion.region, roi });
      return;
    }
    if (wizardStep==="screens") {
      const sn=SCREEN_NAMES[screenIdx];
      if (activeRoiKey==="primary") send({type:"update_tell",screen:sn,roi});
      else if (activeRoiKey==="alt") send({type:"update_tell",screen:sn,alt_roi:roi});
      else if (activeRoiKey.startsWith("and_")) {
        const idx=parseInt(activeRoiKey.slice(4));
        const tell=tells.find(t=>t.screen===sn);
        const requiredAlsoRois=(tell?.required_also??[]).map((ra,i)=>i===idx?roi:ra.roi);
        send({type:"update_tell",screen:sn,required_also_rois:requiredAlsoRois});
      }
    } else if (wizardStep==="selection") {
      const cfk=SELECTION_ROI_CONFIG_KEYS[SELECTION_ROIS[selectionIdx]?.key];
      if (cfk) send({type:"update_config",key:cfk,value:roi});
    } else if (wizardStep==="hud") {
      const cfk=HUD_ROI_CONFIG_KEYS[HUD_ROIS[hudIdx]?.key];
      if (cfk) send({type:"update_config",key:cfk,value:roi});
    }
  }

  // ── Canvas events ─────────────────────────────────────────────────────────────
  function onCanvasMouseDown(e) {
    const roi=getCurrentRoi(), hit=roi?hitTest(e.clientX,e.clientY,roi):null;
    if (hit) {
      const t=getTransform(); const fr = canvasToFrame(e.clientX,e.clientY,t);
      dragging=true; dragHandle=hit.handle; dragStartRoi=[...roi];
      dragStartMouse={x:fr.fx, y:fr.fy};
      e.preventDefault(); return;
    }
    if (view === "edit" && activeTab === "detection") {
      for (const re of editRois()) {
        if (re.active || !re.roi) continue;
        if (hitTest(e.clientX,e.clientY,re.roi)) {
          selectRegion(re.gi, re.ri); hoveredHandle=null;
          e.preventDefault(); return;
        }
      }
      // nothing hit → pan the (zoomed) feed
      _fPanning = true; _fStart = { x:e.clientX, y:e.clientY, px:fPanX, py:fPanY };
      e.preventDefault(); return;
    }
    if (wizardStep==="screens") {
      const tell=tells.find(t=>t.screen===SCREEN_NAMES[screenIdx]);
      for (const re of getAllRoisForTell(tell)) {
        if (re.key===activeRoiKey||!re.roi) continue;
        if (hitTest(e.clientX,e.clientY,re.roi)) {
          activeRoiKey=re.key; syncThreshToScreen(); hoveredHandle=null; drawRoi();
          e.preventDefault(); return;
        }
      }
    }
  }

  function onCanvasMouseMove(e) {
    if (_fPanning) {
      fPanX = _fStart.px + (e.clientX - _fStart.x);
      fPanY = _fStart.py + (e.clientY - _fStart.y);
      _clampPan();
      return;
    }
    const roi=getCurrentRoi();
    if (!dragging) {
      const hit=roi?hitTest(e.clientX,e.clientY,roi):null;
      const nh=hit?.handle??null;
      if (nh!==hoveredHandle) { hoveredHandle=nh; drawRoi(); }
      if (canvasEl) canvasEl.style.cursor=hit?.cursor ?? (view==="edit"&&fZoom>1?"grab":"default");
      return;
    }
    const t=getTransform(); if (!t) return;
    const fr = canvasToFrame(e.clientX,e.clientY,t);
    const dx=fr.fx-dragStartMouse.x, dy=fr.fy-dragStartMouse.y;
    updateCurrentRoi(applyDrag(dragStartRoi,dragHandle,dx,dy)); drawRoi();
  }

  function onFeedWheel(e) {
    if (view !== "edit" || !canvasEl) return;
    e.preventDefault();
    const r = canvasEl.getBoundingClientRect();
    const u = e.clientX - r.left, v = e.clientY - r.top;
    const nz = Math.min(8, Math.max(1, fZoom * (e.deltaY < 0 ? 1.15 : 1/1.15)));
    fPanX += u * (1 - nz / fZoom);
    fPanY += v * (1 - nz / fZoom);
    fZoom = nz;
    if (nz === 1) { fPanX = 0; fPanY = 0; }   // fully zoomed out → snap back to fit
    else _clampPan();
    drawRoi();
  }
  function resetFeedZoom() { fZoom = 1; fPanX = 0; fPanY = 0; }

  function onWindowMouseUp() {
    if (_fPanning) { _fPanning = false; return; }
    if (!dragging) return;
    dragging=false;
    const roi=getCurrentRoi(); if (roi) saveCurrentRoi(roi);
    dragHandle=null; dragStartRoi=null; dragStartMouse=null;
  }

  const ROI_COLORS={primary:"#ffffff",and:"#ffcc00",or:"#00ccff"};

  function _drawOneRoi(ctx,t,roi,color,showHandles) {
    if (!roi||roi.length<4) return;
    const a=frameToCanvas(roi[0],roi[1],t), b=frameToCanvas(roi[2],roi[3],t);
    const cx1=a.cx, cy1=a.cy, cw=b.cx-a.cx, ch=b.cy-a.cy;
    ctx.strokeStyle="rgba(0,0,0,0.7)"; ctx.lineWidth=4; ctx.setLineDash([]);
    ctx.strokeRect(cx1,cy1,cw,ch);
    ctx.strokeStyle=color; ctx.lineWidth=2; ctx.setLineDash([7,4]);
    ctx.strokeRect(cx1,cy1,cw,ch); ctx.setLineDash([]);
    if (showHandles) {
      for (const h of getHandlePositions(roi)) {
        const hc=frameToCanvas(h.fx,h.fy,t), hcx=hc.cx, hcy=hc.cy, r=5;
        const active=hoveredHandle===h.id||(dragging&&dragHandle===h.id);
        ctx.fillStyle=active?"#7eb8f7":color;
        ctx.strokeStyle="rgba(0,0,0,0.85)"; ctx.lineWidth=1.5;
        ctx.beginPath(); ctx.rect(hcx-r,hcy-r,r*2,r*2); ctx.fill(); ctx.stroke();
      }
    }
  }

  function drawRoi() {
    if (!canvasEl) return;
    const t=getTransform(); if (!t) return;
    canvasEl.width=canvasEl.clientWidth; canvasEl.height=canvasEl.clientHeight;
    const ctx=canvasEl.getContext("2d");
    ctx.clearRect(0,0,canvasEl.width,canvasEl.height);
    if (view === "edit") {
      if (activeTab === "detection") {
        const all = editRois();
        for (const re of all) if (!re.active) _drawOneRoi(ctx,t,re.roi,re.color,false);
        const ae = all.find(r=>r.active);
        if (ae) _drawOneRoi(ctx,t,ae.roi,ae.color,true);
      }
      return;
    }
    if (wizardStep==="screens") {
      const tell=tells.find(tell=>tell.screen===SCREEN_NAMES[screenIdx]);
      const allRois=getAllRoisForTell(tell);
      for (const re of allRois) {
        if (re.key===activeRoiKey) continue;
        _drawOneRoi(ctx,t,re.roi,ROI_COLORS[re.type]??"#ffffff",false);
      }
      const ae=allRois.find(r=>r.key===activeRoiKey);
      if (ae) _drawOneRoi(ctx,t,ae.roi,ROI_COLORS[ae.type]??"#ffffff",true);
      return;
    }
    _drawOneRoi(ctx,t,getCurrentRoi(),"#ffffff",true);
  }

  let _pauseIntent="";

  function releaseAndOpenSettings() {
    _pauseIntent="open_settings"; trackerCameraPaused=true;
    send({type:"pause_camera"});
    invoke("open_url",{url:"ms-settings:camera"}).catch(()=>{});
  }

  function releaseForSettings() {
    stopCamera(); trackerCameraPaused=true; send({type:"pause_camera"});
  }

  async function retryNow() {
    // Reset paused state immediately so both panes show "Opening…" right away.
    trackerCameraPaused = false;
    pythonCameraStatus = "opening";
    engineFrame = null;
    // Start both simultaneously — same pattern as the initial camera step open.
    // Don't wait for Python's camera_status to trigger the browser restart;
    // if Python fails the browser would be stuck idle indefinitely.
    startCamera(selectedBrowserDeviceId || undefined);
    send({type:"open_camera"});
  }

  // ── ROI preview poll ──────────────────────────────────────────────────────────
  function startRoiPoll() {
    if (_roiPollTimer) return;
    _roiPollTimer=setInterval(()=>{
      if (!trackerConnected) return;
      if (view === "edit") {
        if (activeTab === "detection" && selectedNode)
          send({type:"test_region",screen:selectedNode,group:activeRegion.group,region:activeRegion.region});
        return;
      }
      if (!wizardOpen) return;
      if (wizardStep==="screens") {
        send({type:"test_template",screen:SCREEN_NAMES[screenIdx],roi_key:activeRoiKey});
      } else if (wizardStep==="templates") {
        const item=ASSET_ITEMS[templateCategory]?.[templateItemIdx];
        if (item) send({type:"get_asset_template",category:templateCategory,item_name:item.file});
      } else {
        const roi=getCurrentRoi(); if (!roi) return;
        const isCostume=wizardStep==="selection"&&SELECTION_ROIS[selectionIdx]?.key==="costume";
        if (isCostume) send({type:"get_roi_preview",roi,use_edges:true});
        else           send({type:"get_roi_preview",roi,binary_thresh:currentBinaryThresh});
      }
    },1000);
  }
  function stopRoiPoll() {
    if (_roiPollTimer) { clearInterval(_roiPollTimer); _roiPollTimer=null; }
    liveRoiCrop=null;
  }

  function onThreshChange() {
    if (view === "edit") {
      if (activeTab === "detection" && selectedNode && trackerConnected) {
        const g = activeRegion.group, r = activeRegion.region, sn = selectedNode;
        tells = tells.map(t => t.screen !== sn ? t : { ...t,
          groups: t.groups.map((grp, gi) => gi !== g ? grp
            : grp.map((reg, ri) => ri !== r ? reg : { ...reg, thresh: currentBinaryThresh })) });
        send({ type:"update_region", screen:sn, group:g, region:r, thresh:currentBinaryThresh });
        send({ type:"test_region", screen:sn, group:g, region:r });
      }
      return;
    }
    if (wizardStep==="screens"&&trackerConnected) {
      const sn=SCREEN_NAMES[screenIdx];
      if (activeRoiKey==="primary") {
        tells=tells.map(t=>t.screen===sn?{...t,binary_thresh:currentBinaryThresh}:t);
        send({type:"update_tell",screen:sn,binary_thresh:currentBinaryThresh});
      } else if (activeRoiKey==="alt") {
        tells=tells.map(t=>t.screen===sn?{...t,alt_binary_thresh:currentBinaryThresh}:t);
        send({type:"update_tell",screen:sn,alt_binary_thresh:currentBinaryThresh});
      } else if (activeRoiKey.startsWith("and_")) {
        const idx=parseInt(activeRoiKey.slice(4));
        tells=tells.map(t=>{
          if (t.screen!==sn) return t;
          return {...t,required_also:(t.required_also??[]).map((ra,i)=>i===idx?{...ra,thresh:currentBinaryThresh}:ra)};
        });
        const tell=tells.find(t=>t.screen===sn);
        send({type:"update_tell",screen:sn,required_also_thresh:(tell?.required_also??[]).map(ra=>ra.thresh??170)});
      }
      send({type:"test_template",screen:sn,roi_key:activeRoiKey});
    }
  }

  // ── Wizard controls ───────────────────────────────────────────────────────────
  async function openWizard() {
    wizardOpen=true; wizardStep="language";
    screenIdx=0; selectionIdx=0; hudIdx=0; currentScore=null;
    await loadBrowserDevices();
  }
  function closeWizard() {
    stopRoiPoll();
    if (trackerCameraPaused) { send({type:"resume_camera"}); trackerCameraPaused=false; }
    wizardOpen=false; resetConfirmPending=false;
  }
  function completeSetup() {
    send({type:"mark_setup_complete"}); setupComplete=true; closeWizard(); _setupAudio();
    // During setup, mic permission wasn't granted when the camera first opened so
    // groupId pairing fell back to video-only. Now permission exists — restart the
    // stream so grouped audio is picked up automatically, matching reboot behaviour.
    if (!selectedAudioDeviceId) startCamera(selectedBrowserDeviceId || undefined);
  }
  function goStep(step) {
    wizardStep=step; screenIdx=0; selectionIdx=0; hudIdx=0;
    templateCategory="characters"; templateItemIdx=0;
    currentScore=null; templateImg=null; liveCropImg=null;
    liveRoiCrop=null; assetTemplateImg=null; assetLiveCrop=null;
    hoveredHandle=null; activeRoiKey="primary"; resetConfirmPending=false; syncThreshToScreen();
    if (step==="camera") {
      // Ask Python to open its camera if not already open
      if (pythonCameraStatus!=="ok") {
        pythonCameraStatus="opening"; engineFrame=null;
        if (!setupComplete) {
          // First-time setup: _openMatchedCameras coordinates both feeds opening on the
          // same physical device simultaneously.
          _openMatchedCameras();
        } else {
          send({type:"open_camera"});
        }
      }
      // Re-run setup: browser camera is already live from the main feed;
      // only restart it if it stopped.
      if (setupComplete && cameraStatus==="idle") startCamera(selectedBrowserDeviceId||undefined);
    }
  }

  function addRequiredAlso() {
    send({type:"add_required_also",screen:SCREEN_NAMES[screenIdx],roi:[935,515,985,565]});
    activeRoiKey="and_0"; syncThreshToScreen();
  }
  function removeRequiredAlso(index) {
    send({type:"remove_required_also",screen:SCREEN_NAMES[screenIdx],index});
    activeRoiKey="primary";
  }
  function addAlt() {
    send({type:"add_alt",screen:SCREEN_NAMES[screenIdx],roi:[935,515,985,565]});
    activeRoiKey="alt"; syncThreshToScreen();
  }
  function removeAlt() {
    send({type:"remove_alt",screen:SCREEN_NAMES[screenIdx]}); activeRoiKey="primary";
  }
  function testScreen() {
    currentScore=null; liveCropImg=null;
    send({type:"test_template",screen:SCREEN_NAMES[screenIdx],roi_key:activeRoiKey});
  }
  function captureScreen() {
    capturingTemplate=true; currentScore=null;
    send({type:"capture_template",screen:SCREEN_NAMES[screenIdx],roi_key:activeRoiKey});
  }
  function captureAsset() {
    const item=ASSET_ITEMS[templateCategory]?.[templateItemIdx]; if (!item) return;
    capturingTemplate=true; assetTemplateImg=null;
    send({type:"capture_asset_template",category:templateCategory,item_name:item.file});
  }

  function prevItem() {
    currentScore=null; liveCropImg=null; liveRoiCrop=null;
    assetTemplateImg=null; assetLiveCrop=null; hoveredHandle=null; activeRoiKey="primary";
    if (wizardStep==="camera") goStep("language");
    else if (wizardStep==="screens") { if (screenIdx>0) screenIdx--; else goStep("camera"); }
    else if (wizardStep==="selection") { if (selectionIdx>0) selectionIdx--; else goStep("screens"); }
    else if (wizardStep==="hud") { if (hudIdx>0) hudIdx--; else goStep("selection"); }
    else if (wizardStep==="templates") {
      if (templateItemIdx>0) { templateItemIdx--; }
      else {
        const ci=ASSET_CATEGORIES.findIndex(c=>c.key===templateCategory);
        if (ci>0) { templateCategory=ASSET_CATEGORIES[ci-1].key; templateItemIdx=ASSET_ITEMS[templateCategory].length-1; }
        else goStep("hud");
      }
    }
    syncThreshToScreen();
  }

  function nextItem() {
    currentScore=null; liveCropImg=null; liveRoiCrop=null;
    assetTemplateImg=null; assetLiveCrop=null; hoveredHandle=null; activeRoiKey="primary";
    if (wizardStep==="screens") { if (screenIdx<SCREEN_NAMES.length-1) screenIdx++; else goStep("selection"); }
    else if (wizardStep==="selection") { if (selectionIdx<SELECTION_ROIS.length-1) selectionIdx++; else goStep("hud"); }
    else if (wizardStep==="hud") { if (hudIdx<HUD_ROIS.length-1) hudIdx++; else goStep("templates"); }
    else if (wizardStep==="templates") {
      const items=ASSET_ITEMS[templateCategory];
      if (templateItemIdx<items.length-1) { templateItemIdx++; }
      else {
        const ci=ASSET_CATEGORIES.findIndex(c=>c.key===templateCategory);
        if (ci<ASSET_CATEGORIES.length-1) { templateCategory=ASSET_CATEGORIES[ci+1].key; templateItemIdx=0; }
        else goStep("done");
      }
    }
    syncThreshToScreen();
  }

  // ── Language handlers ─────────────────────────────────────────────────────────
  function onAppLanguageChange()   { send({type:"update_config",key:"app_language",   value:appLanguage}); }
  function onSwitch2LanguageChange(){ send({type:"update_config",key:"switch2_language",value:switch2Language}); }

  function openLangDialog() {
    langDlgApp=appLanguage; langDlgSw2=switch2Language;
    langDialogEl?.showModal();
  }
  function saveLangDialog() {
    if (langDlgApp!==appLanguage) {
      appLanguage=langDlgApp;
      send({type:"update_config",key:"app_language",value:appLanguage});
    }
    if (langDlgSw2!==switch2Language) {
      switch2Language=langDlgSw2;
      send({type:"update_config",key:"switch2_language",value:switch2Language});
    }
    langDialogEl?.close();
  }

  // ── Device / update ───────────────────────────────────────────────────────────

  async function handleDeviceChange(e) {
    const prevDevice = configuredDevice;
    const prevBrowserId = selectedBrowserDeviceId;
    configuredDevice = e.target.value;
    send({type:"update_config",key:"camera_device",value:configuredDevice});
    // Reset audio to auto when video device changes — new device may have no
    // associated audio (e.g. OBS Virtual Camera). groupId matching in startCamera
    // will pick up the right audio if one exists, otherwise no audio is used.
    selectedAudioDeviceId = "";

    // Match a browser device by label so the preview swaps too
    if (browserDevices.length > 0) {
      const lower = configuredDevice.toLowerCase();
      const match = browserDevices.find(d =>
        d.label.toLowerCase().replace(/\s*\([0-9a-f:]+\)\s*$/i,"").trim() === lower ||
        d.label.toLowerCase().includes(lower) ||
        lower.includes(d.label.toLowerCase().replace(/\s*\([0-9a-f:]+\)\s*$/i,"").trim())
      );
      selectedBrowserDeviceId = match ? match.deviceId : prevBrowserId;
    }

    // Give Python a moment to process the update_config message and write to SQLite
    // before we kill it — otherwise it restarts reading the old device value.
    deviceSwitching = true;
    trackerConnected = false; trackerSpawned = false;
    await new Promise(r => setTimeout(r, 300));
    await invoke("restart_tracker");

    // Swap browser preview in parallel with the restart
    startCamera(selectedBrowserDeviceId || undefined);

    // Revert if tracker doesn't come back within 10 s
    const switchTimeout = setTimeout(() => {
      if (deviceSwitching) {
        deviceSwitching = false;
        configuredDevice = prevDevice;
        selectedBrowserDeviceId = prevBrowserId;
        send({type:"update_config",key:"camera_device",value:prevDevice});
        startCamera(prevBrowserId || undefined);
      }
    }, 10000);

    // Clear timeout flag on ready (handled in handleMsg below)
    _pendingDeviceSwitchTimeout = switchTimeout;
  }
  async function handleCameraDeviceChange(e) {
    // Guard against rapid switches during an in-progress open. The `disabled`
    // attribute handles the normal case but has a Svelte reactivity timing gap;
    // this synchronous check is the true gate.
    if (pythonCameraStatus==="opening" || cameraStatus==="requesting") {
      e.target.value = selectedBrowserDeviceId; // snap visual selection back
      return;
    }
    selectedBrowserDeviceId=e.target.value;
    // Reset audio to auto — new device may have different associated audio.
    selectedAudioDeviceId = "";
    // Clear stale frame and show "opening" immediately.
    engineFrame = null; pythonCameraStatus = "opening";
    if (!setupComplete&&wizardStep==="camera") {
      // First-time setup: user explicitly chose a device — sync Python to match it
      // directly (no auto-select logic needed), then open both simultaneously.
      stopCamera();
      const chosen = browserDevices.find(d => d.deviceId === selectedBrowserDeviceId);
      if (chosen) {
        const cleanLabel = chosen.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim();
        const match = devices.find(d =>
          d.toLowerCase() === cleanLabel.toLowerCase() ||
          d.toLowerCase().includes(cleanLabel.toLowerCase()) ||
          cleanLabel.toLowerCase().includes(d.toLowerCase())
        );
        const pyDevice = match ?? cleanLabel;
        if (pyDevice !== configuredDevice) {
          configuredDevice = pyDevice;
          send({ type:"update_config", key:"camera_device", value:pyDevice });
        }
      }
      send({ type:"open_camera" });
      startCamera(selectedBrowserDeviceId);
    } else {
      send({type:"open_camera"});
      await startCamera(selectedBrowserDeviceId);
    }
  }
  async function handleAudioDeviceChange(e) {
    selectedAudioDeviceId = e.target.value;
    // Persist the choice by label (not ID — IDs change between sessions).
    const label = selectedAudioDeviceId === "none"
      ? "none"
      : (audioDevices.find(d => d.deviceId === selectedAudioDeviceId)?.label ?? "");
    send({ type:"update_config", key:"audio_device_label", value:label });
    // Restart the camera stream so the new audio device takes effect.
    if (videoStream) await startCamera(selectedBrowserDeviceId || undefined);
  }
  async function restartTracker() {
    restartNeeded=false; devices=[]; trackerConnected=false; trackerSpawned=false; await invoke("restart_tracker");
  }
  async function applyUpdate() {
    if (pendingUpdate) { await invoke("stop_tracker"); await pendingUpdate.install(); }
  }
  async function checkForUpdate() {
    try {
      const u=await check(); if (!u) return;
      pendingUpdate=u; updateVersion=u.version;
      await u.download(ev=>{
        if (ev.event==="Started")       { downloadTotal=ev.data.contentLength??0; downloadReceived=0; }
        else if (ev.event==="Progress") { downloadReceived+=ev.data.chunkLength; }
        else if (ev.event==="Finished") { updateReady=true; }
      });
    } catch { /* silent */ }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────────
  let sidecarStartupError = false;

  const _t0 = Date.now();
  function _elapsed() { return `+${((Date.now()-_t0)/1000).toFixed(1)}s`; }

  onMount(async () => {
    appWindow=getCurrentWindow();
    version=await getVersion();
    pushLog(`[app] v${version} starting… ${_elapsed()}`);
    await invoke("start_tracker");
    pushLog(`[app] tracker spawn requested ${_elapsed()}`);
    // Pre-populate browser device lists now — Tauri grants camera+mic permissions
    // before the webview loads, so labels and groupIds are available immediately.
    // This ensures the Detection > Audio dropdown shows real names from the start.
    loadBrowserDevices();
    unlisten=await listen("tracker-event", ev=>{
      sidecarStartupError = false;
      try { handleMsg(JSON.parse(ev.payload)); }
      catch { pushLog(String(ev.payload)); }
    });
    pushLog(`[app] listening for tracker events ${_elapsed()}`);
    // Warn periodically if the tracker hasn't connected yet.
    // First-run can be slow (60-90s) while Windows Defender scans _internal/ DLLs.
    const _startupTimer = setInterval(() => {
      if (trackerConnected) { clearInterval(_startupTimer); return; }
      const secs = Math.round((Date.now()-_t0)/1000);
      if (!trackerSpawned) {
        pushLog(`[app] engine process has not spawned yet… (${secs}s elapsed) — antivirus may be blocking launch`);
      } else {
        pushLog(`[app] engine launched but Python not ready yet… (${secs}s elapsed) — Windows Defender may still be scanning DLLs`);
      }
      if (secs >= 120) { sidecarStartupError = true; clearInterval(_startupTimer); }
    }, 10000);
    // Forward Rust log::* records to the log pane, skipping tauri plugin noise.
    const _levelPrefix = { error:"[rust/err]", warn:"[rust/warn]", info:"[rust]", debug:"[rust/dbg]", trace:null };
    attachLogger(record => {
      if (record.target?.startsWith("tauri")) return;
      if (record.message?.includes("[tauri_")) return;  // pre-formatted plugin records
      const prefix = _levelPrefix[record.level] ?? "[rust]";
      if (prefix) pushLog(`${prefix} ${record.message}`);
    }).catch(() => {});
    setInterval(()=>{ _tick++; },1000);
    checkForUpdate();
    window.addEventListener("mouseup",onWindowMouseUp);
    startFeedPoll();
  });

  onDestroy(()=>{
    if (unlisten) unlisten();
    stopCamera(); stopRoiPoll(); stopFeedPoll(); _teardownAudio();
    window.removeEventListener("mouseup",onWindowMouseUp);
    if (trackerCameraPaused) send({type:"resume_camera"});
  });

  $: if (mainVideoEl) mainVideoEl.srcObject=setupComplete ? (videoStream??null) : null;
  $: if (wizVideoEl)  wizVideoEl.srcObject =videoStream??null;
  afterUpdate(()=>{ if (wizardOpen || view === "setup" || view === "edit") drawRoi(); });

  // ── Reactive computeds ────────────────────────────────────────────────────────
  $: currentScreenName  = SCREEN_NAMES[screenIdx]??"";;
  $: currentScreenLabel = SCREEN_LABELS[currentScreenName]??currentScreenName;
  $: currentScreenHint  = SCREEN_HINTS[currentScreenName]??"";
  $: selItem   = SELECTION_ROIS[selectionIdx];
  $: hudItem   = HUD_ROIS[hudIdx];
  $: cameraOk  = cameraStatus==="ok";
  $: pythonCameraOk = pythonCameraStatus==="ok"&&engineFrame!==null&&!trackerCameraPaused;
  $: bothCamerasOk  = cameraOk&&pythonCameraOk;
  $: assetItem = ASSET_ITEMS[templateCategory]?.[templateItemIdx];
  $: currentTell = tells.find(t=>t.screen===SCREEN_NAMES[screenIdx])??null;

  $: if (((wizardOpen || view === "setup")&&["screens","selection","hud","templates"].includes(wizardStep))
         || (view === "edit" && activeTab === "detection" && selectedNode)) {
    startRoiPoll();
  } else { stopRoiPoll(); }

  // Load the stored template + live crop whenever the selected region changes.
  $: if (view === "edit" && activeTab === "detection" && selectedNode && activeRegion && trackerConnected) {
    send({ type:"get_region_images", screen:selectedNode, group:activeRegion.group, region:activeRegion.region });
  }

  $: _=appLanguage;
  function tr(key) { return t(key,appLanguage); }

  function syncThreshToScreen() {
    if (view === "edit") {
      currentBinaryThresh = activeRegionObj?.thresh ?? 170;
      return;
    }
    if (wizardStep==="screens") {
      const _t=tells.find(t=>t.screen===SCREEN_NAMES[screenIdx]);
      if (!_t) { currentBinaryThresh=170; return; }
      if (activeRoiKey==="primary") currentBinaryThresh=_t.binary_thresh??170;
      else if (activeRoiKey==="alt") currentBinaryThresh=_t.alt_binary_thresh??170;
      else if (activeRoiKey.startsWith("and_"))
        currentBinaryThresh=_t.required_also?.[parseInt(activeRoiKey.slice(4))]?.thresh??170;
      else currentBinaryThresh=170;
    } else { currentBinaryThresh=170; }
  }

  $: if (wizardOpen&&wizardStep==="screens"&&trackerConnected) {
    templateImg=null; liveCropImg=null;
    send({type:"get_template_images",screen:SCREEN_NAMES[screenIdx],roi_key:activeRoiKey});
  }

  $: if (browserDevices.length>0&&configuredDevice) {
    const lower=configuredDevice.toLowerCase();
    const match=browserDevices.find(d=>d.label.toLowerCase().includes(lower));
    if (match&&match.deviceId!==selectedBrowserDeviceId) selectedBrowserDeviceId=match.deviceId;
  }

  $: sortedCandidates = Object.entries(candidateScores)
    .sort((a,b)=>b[1]-a[1]).slice(0,8);

  function confBar(v) { return Math.round((v||0)*100); }
  function scoreColor(v) {
    if (v>=0.8) return "#4caf50";
    if (v>=0.5) return "#f59e0b";
    return "#ef4444";
  }
</script>

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  MAIN LAYOUT                                                               -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<div class="app">

  <!-- ── Title bar ──────────────────────────────────────────────────────────── -->
  <header class="titlebar" data-tauri-drag-region>
    <div class="tb-brand" data-tauri-drag-region>
      <span class="brand-name">MKW Tracker</span>
      {#if version}<span class="brand-ver">v{version}</span>{/if}
    </div>

    <!-- Language badge -->
    <button class="lang-badge" on:click={openLangDialog} title="Change language">
      <span class="lang-app">{appLangName}</span>
      <span class="lang-sep">/</span>
      <span class="lang-sw2">{sw2LangName}</span>
    </button>

    <div class="tb-health" data-tauri-drag-region>
      <span class="hb-dot" style="background:{statusDot}"></span>
      {#if trackerConnected && backendAlive}
        <span class="hb-screen">{backendScreen}</span>
        <span class="hb-sep">·</span>
        <span class="hb-score" style="color:{scoreColor(liveScore)}">{liveScore.toFixed(3)}</span>
        <span class="hb-sep">·</span>
        <span class="hb-fps">{backendFps} fps</span>
      {:else if trackerConnected}
        <span class="hb-warn">backend stalled</span>
      {:else if trackerSpawned}
        <span class="hb-idle">engine starting…</span>
      {:else}
        <span class="hb-idle">launching…</span>
      {/if}
    </div>

    <div class="tb-actions" data-tauri-drag-region>
      {#if updateVersion}
        <div class="upd-strip">
          <span class="upd-label">{updateReady ? `v${updateVersion} ready` : `v${updateVersion} ${downloadPercent !== null ? `${downloadPercent}%` : "…"}`}</span>
          {#if !updateReady}
            <div class="upd-track"><div class="upd-fill" style="width:{downloadPercent??0}%"></div></div>
          {:else}
            <button class="btn-sm" on:click={applyUpdate}>Restart to apply</button>
          {/if}
        </div>
      {/if}
      {#if view === "main"}
        {#if wizardOpen}
          <button class="btn-hdr btn-close-wiz" on:click={closeWizard}>✕ Close Settings</button>
        {:else}
          <button class="btn-hdr btn-edit" on:click={()=>openNode(backendScreen && backendScreen!=="—" && backendScreen!=="UNKNOWN" ? backendScreen : "CHARACTER_SELECT")}>✎ Edit Screens</button>
          <button class="btn-hdr btn-setup" on:click={openSettings}>⚙ Settings</button>
        {/if}
      {/if}
      {#if view === "edit"}
        <button class="btn-hdr btn-close-wiz" on:click={closeEdit}>← Back</button>
      {/if}
    </div>

    <div class="win-controls">
      <button class="win-btn" on:click={winMinimize} title="Minimize">&#x2013;</button>
      <button class="win-btn" on:click={winToggleMaximize} title="Maximize">&#x25a1;</button>
      <button class="win-btn win-btn-close" on:click={winClose} title="Close">&#x2715;</button>
    </div>
  </header>

  <!-- ── View router ───────────────────────────────────────────────────────── -->
  {#if view === "main"}

  <!-- ── Main grid: feed | sidebar, with graph footer below ─────────────────── -->
  <div class="main-grid">

    <!-- Left: camera feed (browser getUserMedia — smooth 30/60fps) -->
    <div class="main-feed">
      <div class="feed-area">
        <video bind:this={mainVideoEl} autoplay playsinline muted
          class="feed-video"
          class:feed-hidden={!cameraOk || feedVideoHidden}></video>
        {#if !cameraOk || feedVideoHidden}
          <div class="feed-placeholder">
            {#if feedVideoHidden && cameraOk}
              <span class="feed-ph-text feed-ph-dim">Feed hidden</span>
            {:else if !trackerConnected}
              <span class="feed-ph-icon">◌</span>
              <span class="feed-ph-text">Connecting to engine…</span>
            {:else if cameraStatus === "requesting"}
              <span class="feed-ph-icon">◌</span>
              <span class="feed-ph-text">Opening camera…</span>
            {:else}
              <span class="feed-ph-icon">◌</span>
              <span class="feed-ph-text">Waiting for camera…</span>
            {/if}
          </div>
        {/if}
      </div>

      <!-- Feed controls: audio + video toggle -->
      <div class="feed-controls">
        {#if _hasAudio}
          <button class="fc-btn" title={feedMuted ? "Unmute" : "Mute"}
            on:click={() => feedMuted = !feedMuted}>
            {#if feedMuted}
              <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2zm4.5 2.5a6 6 0 010 7M11 5.5a4 4 0 010 5"/><line x1="1" y1="1" x2="15" y2="15" stroke-linecap="round"/></svg>
            {:else if feedVolume < 0.35}
              <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2z"/><path d="M11 6a2.5 2.5 0 010 4"/></svg>
            {:else}
              <svg viewBox="0 0 16 16" class="fc-icon"><path d="M8 2v12l-4-3H1V7h3L8 4V2z"/><path d="M11 5.5a4 4 0 010 5M13 3.5a7 7 0 010 9"/></svg>
            {/if}
          </button>
          <input type="range" min="0" max="1" step="0.01"
            bind:value={feedVolume}
            on:input={() => { if (feedVolume > 0) feedMuted = false; }}
            class="fc-slider" title="Volume" />
          <span class="fc-vol">{Math.round(feedVolume * 100)}%</span>
          <div class="fc-divider"></div>
        {:else if cameraOk}
          <span class="fc-no-audio">no audio</span>
          <div class="fc-divider"></div>
        {/if}
        <button class="fc-btn fc-vid-btn" title={feedVideoHidden ? "Show feed" : "Hide feed"}
          on:click={() => feedVideoHidden = !feedVideoHidden}>
          {#if feedVideoHidden}
            <svg viewBox="0 0 16 16" class="fc-icon"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/><line x1="2" y1="2" x2="14" y2="14" stroke-linecap="round"/></svg>
            <span class="fc-vid-label">Show</span>
          {:else}
            <svg viewBox="0 0 16 16" class="fc-icon"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>
            <span class="fc-vid-label">Hide</span>
          {/if}
        </button>
      </div>
    </div>

    <!-- Right: sidebar panels -->
    <aside class="sidebar">

      <!-- ── Panel: Detection ──────────────────────────────────────────── -->
      <div class="panel">
        <button class="panel-hdr" on:click={()=>panelOpen.detection=!panelOpen.detection}>
          <span class="panel-title">Detection</span>
          <span class="panel-chev">{panelOpen.detection?'▾':'▸'}</span>
        </button>
        {#if panelOpen.detection}
          <div class="panel-body">
            <div class="det-screen">
              <span class="det-screen-lbl">Screen</span>
              <span class="det-screen-val" class:det-active={backendAlive}>{backendScreen}</span>
            </div>
            <div class="det-score-row">
              <span class="det-lbl">Score</span>
              <div class="det-bar-wrap">
                <div class="det-bar" style="width:{confBar(liveScore)}%; background:{scoreColor(liveScore)}"></div>
              </div>
              <span class="det-val" style="color:{scoreColor(liveScore)}">{liveScore.toFixed(3)}</span>
            </div>
            {#if devices.length > 0}
              <div class="det-device-row">
                <label class="det-lbl" for="main-dev">Input</label>
                <select id="main-dev" class="det-select" disabled={deviceSwitching} on:change={handleDeviceChange}>
                  {#if !configuredDevice}
                    <option value="" disabled selected>— pick a device —</option>
                  {/if}
                  {#each devices as d}
                    <option value={d} selected={d===configuredDevice}>{d}</option>
                  {/each}
                </select>
                {#if deviceSwitching}
                  <span class="det-switching">switching…</span>
                {/if}
              </div>
            {/if}
            {#if audioDevices.length > 0}
              <div class="det-device-row">
                <label class="det-lbl" for="main-aud">Audio</label>
                <select id="main-aud" class="det-select" on:change={handleAudioDeviceChange}>
                  <option value="none" selected={!selectedAudioDeviceId||selectedAudioDeviceId==="none"}>— none —</option>
                  {#each audioDevices as d}
                    <option value={d.deviceId} selected={d.deviceId===selectedAudioDeviceId}>
                      {d.label || `Audio ${d.deviceId.slice(0,6)}…`}
                    </option>
                  {/each}
                </select>
              </div>
            {/if}
          </div>
        {/if}
      </div>

      <!-- ── Panel: Candidates ─────────────────────────────────────────── -->
      <div class="panel">
        <button class="panel-hdr" on:click={()=>panelOpen.candidates=!panelOpen.candidates}>
          <span class="panel-title">Candidates</span>
          <span class="panel-chev">{panelOpen.candidates?'▾':'▸'}</span>
        </button>
        {#if panelOpen.candidates}
          <div class="panel-body cand-body">
            {#if sortedCandidates.length > 0}
              {#each sortedCandidates as [scr, score]}
                {@const isActive = scr === backendScreen}
                <div class="cand-row" class:cand-active={isActive}>
                  <span class="cand-name" class:cand-name-active={isActive}>
                    {SCREEN_LABELS[scr]??scr}
                  </span>
                  <div class="cand-bar-wrap">
                    <div class="cand-bar" style="width:{confBar(score)}%; background:{scoreColor(score)}"></div>
                  </div>
                  <span class="cand-score" style="color:{scoreColor(score)}">{score.toFixed(3)}</span>
                </div>
              {/each}
            {:else}
              <span class="panel-empty">No candidate data yet</span>
            {/if}
          </div>
        {/if}
      </div>

      <!-- ── Panel: Selection ──────────────────────────────────────────── -->
      <div class="panel">
        <button class="panel-hdr" on:click={()=>panelOpen.selection=!panelOpen.selection}>
          <span class="panel-title">Selection</span>
          <span class="panel-chev">{panelOpen.selection?'▾':'▸'}</span>
        </button>
        {#if panelOpen.selection}
          <div class="panel-body">
            {#each [
              { label:"Character", val:selChar,   conf:selCharConf   },
              { label:"Costume",   val:selCostume, conf:selCostumeConf},
              { label:"Kart",      val:selKart,    conf:selKartConf   },
              { label:"Course",    val:selCourse,  conf:selCourseConf },
            ] as item}
              <div class="sel-row">
                <span class="sel-lbl">{item.label}</span>
                <div class="sel-right">
                  <span class="sel-val">{item.val ?? "—"}</span>
                  {#if item.val}
                    <div class="sel-bar-wrap">
                      <div class="sel-bar" style="width:{confBar(item.conf)}%; background:{scoreColor(item.conf)}"></div>
                    </div>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <!-- ── Panel: Race ───────────────────────────────────────────────── -->
      <div class="panel">
        <button class="panel-hdr" on:click={()=>panelOpen.hud=!panelOpen.hud}>
          <span class="panel-title">Race</span>
          <span class="panel-chev">{panelOpen.hud?'▾':'▸'}</span>
        </button>
        {#if panelOpen.hud}
          <div class="panel-body hud-body">
            <div class="hud-row">
              <span class="hud-lbl">Lap</span>
              <span class="hud-val">
                {#if curLap !== null}{curLap} / {totLap ?? "?"}{:else}—{/if}
              </span>
            </div>
            <div class="hud-row">
              <span class="hud-lbl">Coins</span>
              <span class="hud-val">{coins !== null ? coins : "—"}</span>
            </div>
            <div class="hud-row">
              <span class="hud-lbl">Mush</span>
              <span class="hud-val mush-val">
                {#if mushrooms > 0}{'🍄'.repeat(mushrooms)}{:else}—{/if}
              </span>
            </div>
            <div class="hud-divider"></div>
            {#each Array.from({length: totLap ?? 0}, (_, i) => i + 1) as lap}
              <div class="hud-row">
                <span class="hud-lbl split-lbl">Lap {lap}</span>
                <span class="hud-val split-val" class:split-pending={!raceSplits[lap]}>{raceSplits[lap] ?? "--:--.---"}</span>
              </div>
            {/each}
            <div class="hud-row hud-total-row">
              <span class="hud-lbl split-lbl">Total</span>
              <span class="hud-val hud-total" class:split-pending={!raceFinishTime}>{raceFinishTime ?? "--:--.---"}</span>
            </div>
          </div>
        {/if}
      </div>

      <!-- ── Panel: Log ────────────────────────────────────────────────── -->
      <div class="panel panel-log">
        <button class="panel-hdr" on:click={()=>panelOpen.log=!panelOpen.log}>
          <span class="panel-title">Event Log</span>
          <span class="panel-chev">{panelOpen.log?'▾':'▸'}</span>
        </button>
        {#if panelOpen.log}
          <div class="panel-body log-body" bind:this={logEl}>
            {#each logs as line}
              <div class="log-line">{line}</div>
            {/each}
            {#if logs.length === 0}
              {#if sidecarStartupError}
                <div class="log-empty log-error">
                  Tracker failed to start. Check that your antivirus isn't blocking
                  <code>bin\mkw-tracker-engine.exe</code> in the install folder,
                  then restart the app.
                </div>
              {:else}
                <div class="log-empty">Waiting for events…</div>
              {/if}
            {/if}
          </div>
        {/if}
      </div>

    </aside>

    <!-- Graph footer (spans col 1 only, row 2) -->
    <div class="graph-row">
      <button class="graph-toggle" on:click={()=>graphOpen=!graphOpen}>
        <span>Screen Graph</span>
        <span class="graph-chev">{graphOpen?'▾':'▸'}</span>
      </button>
      {#if graphOpen}
        <div class="graph-content">
          <svg viewBox="0 0 860 205" class="graph-svg" xmlns="http://www.w3.org/2000/svg">
            <!-- static edges -->
            {#each GRAPH_EDGES as [from, to]}
              {@const a=graphNodeMap[from]}
              {@const b=graphNodeMap[to]}
              {#if a && b}
                {@const involvesHome = from==="HOME" || to==="HOME"}
                {@const isConstant   = involvesHome && ((from==="HOME"||to==="HOME") && (from==="TITLE"||to==="TITLE"||from==="GALLERY"||to==="GALLERY"))}
                {@const onHomeCluster = backendScreen==="HOME" || backendScreen==="GALLERY"}
                {@const isPrevLink   = involvesHome && onHomeCluster && !!prevBackendScreen && (from===prevBackendScreen||to===prevBackendScreen)}
                {@const isCtxLink    = involvesHome && (homeContextScreens.has(from) || homeContextScreens.has(to))}
                {@const dimHome      = involvesHome && !isConstant && !isPrevLink && !isCtxLink}
                <line x1={a.x+NW/2} y1={a.y+NH/2} x2={b.x+NW/2} y2={b.y+NH/2}
                  stroke="#1a1a2e" stroke-width="1"
                  opacity={dimHome ? 0.12 : 1} />
              {/if}
            {/each}
            <!-- nodes -->
            {#each GRAPH_NODES as node}
              {@const isActive  = node.id === backendScreen}
              {@const isHome    = node.id === "HOME"}
              {@const isUnknown = node.id === "UNKNOWN"}
              {@const candScore = candidateScores[node.id]}
              {@const dimmed    = isUnknown}
              <g transform="translate({node.x},{node.y})" style="cursor:pointer"
                 role="button" tabindex="-1" on:click={()=>openNode(node.id)}>
                <rect
                  width={NW} height={NH} rx="3" ry="3"
                  fill={isActive ? "#0d1f40" : "#05050e"}
                  stroke={isActive ? "#7eb8f7" : (candScore ? "#2a3a5a" : "#111120")}
                  stroke-width={isActive ? 1.5 : 1}
                  opacity={dimmed ? 0.45 : 1}
                />
                <text x={NW/2} y={isActive && isHome && prevBackendScreen ? NH/2-3 : NH/2}
                  text-anchor="middle" dominant-baseline="central"
                  font-size="7" font-family="Consolas, monospace"
                  fill={isActive ? "#7eb8f7" : (candScore ? "#5a7a9a" : (dimmed ? "#222" : "#333"))}
                  opacity={dimmed ? 0.6 : 1}
                >
                  {node.label}
                </text>
                {#if isHome && prevBackendScreen}
                  <text x={NW/2} y={NH/2+4} text-anchor="middle" dominant-baseline="central"
                    font-size="5" font-family="Consolas, monospace"
                    fill={isActive ? "#4a7ab0" : "#1e1e30"} opacity={isActive ? 0.9 : 0.7}
                  >↩ {prevBackendScreen.replace(/_/g," ")}</text>
                {/if}
                {#if candScore}
                  <text x={NW-2} y="3" text-anchor="end" dominant-baseline="hanging"
                    font-size="5.5" font-family="Consolas, monospace"
                    fill={scoreColor(candScore)} opacity="0.9"
                  >{candScore.toFixed(2)}</text>
                {/if}
              </g>
            {/each}
          </svg>
        </div>
      {/if}
    </div>

  </div><!-- /main-grid -->

  {:else if view === "edit"}

  <!-- ── Edit Screens view (interactive graph + per-node editor) ─────────────── -->
  <div class="edit-view">
    <div class="edit-split">
      <!-- left: interactive screen graph (zoom = scroll, pan = drag) -->
      <div class="edit-graph">
        <div class="edit-graph-vp" bind:clientWidth={gWrapW} bind:clientHeight={gWrapH}>
        <svg class="graph-svg-zoom" class:panning={_gPanning} xmlns="http://www.w3.org/2000/svg"
             on:wheel={onGraphWheel} on:mousedown={onGraphDown}
             on:mousemove={onGraphMove} on:mouseup={onGraphUp} on:mouseleave={onGraphUp}>
          <g transform="translate({gPanX} {gPanY}) scale({gZoom})">
            {#each GRAPH_EDGES as [from, to]}
              {@const a=graphNodeMap[from]}
              {@const b=graphNodeMap[to]}
              {#if a && b}
                <line x1={a.x+NW/2} y1={a.y+NH/2} x2={b.x+NW/2} y2={b.y+NH/2}
                  stroke="#1a1a2e" stroke-width="1" opacity="0.5" />
              {/if}
            {/each}
            {#each GRAPH_NODES as node}
              {@const isSel    = node.id === selectedNode}
              {@const isActive = node.id === backendScreen}
              <g transform="translate({node.x},{node.y})" style="cursor:pointer"
                 role="button" tabindex="-1" on:click={()=>nodeClick(node.id)}>
                <rect width={NW} height={NH} rx="3" ry="3"
                  fill={isSel ? "#0d1f40" : "#05050e"}
                  stroke={isSel ? "#7eb8f7" : (isActive ? "#3a5a7a" : "#1a1a2e")}
                  stroke-width={isSel ? 1.5 : 1} />
                <text x={NW/2} y={NH/2} text-anchor="middle" dominant-baseline="central"
                  font-size="10" font-family="Consolas, monospace"
                  fill={isSel ? "#7eb8f7" : "#7a93a6"}>{node.label}</text>
              </g>
            {/each}
          </g>
        </svg>
        </div>
        <div class="edit-graph-foot">scroll = zoom · drag = pan</div>
      </div>

      <!-- right: per-node editor pane -->
      <div class="edit-pane">
        {#if selectedNode}
          {@const tabs = tabsForNode(selectedNode)}
          <div class="edit-pane-hd">
            <h3>{SCREEN_LABELS[selectedNode] ?? selectedNode}</h3>
            <span class="edit-screen-id">{selectedNode}</span>
          </div>
          {#if tabs.length > 1}
            <nav class="edit-tabs">
              {#each tabs as tabKey}
                <button class:active={activeTab===tabKey} on:click={()=>activeTab=tabKey}>{TAB_LABELS[tabKey]}</button>
              {/each}
            </nav>
          {/if}
          <div class="edit-tab-body">
            {#if activeTab === "detection"}
              <div class="det-editor">
                <div class="det-feed">
                  <div class="preview-wrapper det-feed-wrap" on:wheel={onFeedWheel}>
                    {#if cameraOk}
                      <div class="det-zoom" style="transform: translate({fPanX}px,{fPanY}px) scale({fZoom}); transform-origin:0 0;">
                        <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                      </div>
                      <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                        on:mousedown={onCanvasMouseDown} on:mousemove={onCanvasMouseMove}></canvas>
                      {#if fZoom > 1}
                        <button class="det-zoom-reset" on:click={resetFeedZoom}>reset {fZoom.toFixed(1)}×</button>
                      {/if}
                    {:else}
                      <div class="preview-placeholder"><span>Camera unavailable</span></div>
                    {/if}
                  </div>
                  <p class="preview-cap">Drag handles to move/resize · click another box to select · scroll = zoom, drag empty space = pan.</p>
                </div>

                <div class="det-tree">
                  {#if editTell}
                    <div class="tree-label">Detected when ALL groups match:</div>
                    {#each editTell.groups as group, gi}
                      {#if gi > 0}<div class="tree-and">— AND —</div>{/if}
                      <div class="tree-group">
                        <div class="tree-group-hd">Group {gi+1} · any of</div>
                        {#each group as region, ri}
                          <button class="tree-region" class:sel={activeRegion.group===gi && activeRegion.region===ri}
                                  on:click={()=>selectRegion(gi,ri)}>
                            <span class="treg-dot" style="background:{activeRegion.group===gi && activeRegion.region===ri ? '#7eb8f7' : (gi===activeRegion.group ? '#00ccff' : '#ffcc00')}"></span>
                            <span class="treg-name">{region.kind==="dark_loading" ? "dark-loading" : `image ${ri+1}`}</span>
                            {#if activeRegion.group===gi && activeRegion.region===ri && currentScore}
                              <span class="treg-score" style="color:{scoreColor(currentScore.score)}">{currentScore.score.toFixed(2)}</span>
                            {/if}
                          </button>
                        {/each}
                        <button class="tree-add" on:click={()=>addRegion(gi)}>+ OR alternative image</button>
                      </div>
                    {/each}
                    <button class="tree-add tree-add-and" on:click={addGroup}>+ AND condition group</button>

                    {#if activeRegionObj}
                      <div class="reg-controls">
                        <div class="reg-row">
                          <label class="reg-kind">Kind
                            <select value={activeRegionObj.kind} on:change={(e)=>onKindChange(e.target.value)}>
                              <option value="template">Template image</option>
                              <option value="dark_loading">Dark-loading</option>
                            </select>
                          </label>
                          {#if editTell.groups.length > 1 || (editTell.groups[activeRegion.group]?.length ?? 0) > 1}
                            <button class="reg-del" on:click={removeActiveRegion}>🗑 Delete region</button>
                          {/if}
                        </div>
                        {#if activeRegionObj.kind === "template"}
                          <div class="reg-thumbs">
                            <div class="reg-thumb"><span>live crop</span>{#if liveCropImg}<img src={liveCropImg} alt="live"/>{:else}<div class="reg-thumb-empty"></div>{/if}</div>
                            <div class="reg-thumb"><span>template</span>{#if templateImg}<img src={templateImg} alt="template"/>{:else}<div class="reg-thumb-empty"></div>{/if}</div>
                          </div>
                          <button class="btn-secondary reg-recap" on:click={recaptureRegion} disabled={capturingTemplate}>
                            {capturingTemplate ? "Capturing…" : "Recapture this region"}
                          </button>
                        {:else}
                          <p class="hint">Dark-loading detects a near-black region plus a bright icon. Drag the main ROI on the feed; the icon ROI uses its default position.</p>
                        {/if}
                      </div>
                    {/if}

                    <div class="det-reset">
                      {#if detResetPending}
                        <p class="det-reset-q">Reset <b>{selectedNode}</b>’s detection ROIs &amp; groups to defaults? This discards your custom regions for this screen.</p>
                        <div class="det-reset-row">
                          <button class="btn-reset-confirm" on:click={resetDetection}>Yes, reset</button>
                          <button class="btn-nav" on:click={()=>detResetPending=false}>Cancel</button>
                        </div>
                      {:else}
                        <button class="det-reset-btn" on:click={()=>detResetPending=true}>↺ Reset detection to defaults</button>
                      {/if}
                    </div>
                  {:else}
                    <p class="hint">Loading detection config…</p>
                  {/if}
                </div>
              </div>
            {:else}
              <p class="hint">Editor for “{TAB_LABELS[activeTab]}” is coming in the next increment.</p>
            {/if}
          </div>
        {:else}
          <div class="edit-pane-empty"><p class="hint">Select a screen node on the left to edit it.</p></div>
        {/if}
      </div>
    </div>
  </div>

  {:else if view === "setup"}

  <!-- ── First-time setup view (full screen, no modal) ──────────────────────── -->
  <div class="setup-view">
    <div class="setup-wiz">
      <nav class="wiz-tabs setup-wiz-tabs">
        {#each STEPS as s}
          <button class="wiz-tab" class:active={wizardStep===s} tabindex="-1" on:click={()=>goStep(s)}>
            {STEP_LABELS[s]}
          </button>
        {/each}
      </nav>

      <div class="wiz-body setup-wiz-body">
        <!-- language / camera / done steps — identical content to the modal wizard -->
        {#if wizardStep === "language"}
          <div class="step-centred">
            <h2>{tr("lang.title")}</h2>
            <p>{tr("lang.desc")}</p>
            <div class="lang-form">
              <div class="lang-row">
                <label for="sv-app-lang">{tr("lang.app_label")}</label>
                <select id="sv-app-lang" bind:value={appLanguage} on:change={onAppLanguageChange}>
                  {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
                </select>
              </div>
              <div class="lang-row">
                <label for="sv-sw2-lang">{tr("lang.sw2_label")}</label>
                <select id="sv-sw2-lang" bind:value={switch2Language} on:change={onSwitch2LanguageChange}>
                  {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
                </select>
                <p class="hint lang-hint">{tr("lang.sw2_hint")}</p>
              </div>
            </div>
            <button class="btn-primary btn-lg" on:click={()=>goStep("camera")}>{tr("lang.continue")}</button>
          </div>

        {:else if wizardStep === "camera"}
          <div class="cam-setup">
            <div class="cam-dual">
              <div class="cam-pane">
                <div class="cam-pane-label">Browser / App Input</div>
                <div class="preview-wrapper">
                  {#if cameraOk}
                    <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                  {:else if cameraStatus === "requesting"}
                    <div class="preview-placeholder"><span class="spin">◌</span><span>Opening…</span></div>
                  {:else if cameraStatus === "busy"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Blocked — device in exclusive use</span>
                    </div>
                  {:else if cameraStatus === "error"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span><span class="cam-pane-err-label">Camera error</span>
                    </div>
                  {:else if trackerCameraPaused}
                    <div class="preview-placeholder">
                      <span class="preview-icon" style="color:#888">○</span>
                      <span class="cam-pane-err-label">Camera released</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder"><span class="spin">◌</span><span>Waiting…</span></div>
                  {/if}
                </div>
                <div class="cam-pane-status" class:cam-status-ok={cameraOk} class:cam-status-err={cameraStatus==="busy"||cameraStatus==="error"} class:cam-status-warn={trackerCameraPaused&&!cameraOk}>
                  <span class="cam-dot"></span>
                  {cameraOk?"Connected":cameraStatus==="requesting"?"Opening…":cameraStatus==="busy"?"Blocked":cameraStatus==="error"?"Error":trackerCameraPaused?"Released":"Waiting"}
                </div>
              </div>

              <div class="cam-pane">
                <div class="cam-pane-label">Python Engine Input</div>
                <div class="preview-wrapper">
                  {#if engineFrame && !trackerCameraPaused}
                    <img src={engineFrame} alt="Engine feed" class="preview-video" style="object-fit:contain"/>
                  {:else if trackerCameraPaused}
                    <div class="preview-placeholder">
                      <span class="preview-icon" style="color:#888">○</span>
                      <span class="cam-pane-err-label">Camera released</span>
                    </div>
                  {:else if pythonCameraStatus === "error"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Can't access device{pythonCameraError?`: ${pythonCameraError}`:""}</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder">
                      <span class="spin">◌</span>
                      <span>{pythonCameraStatus==="opening"?"Opening and verifying…":!trackerConnected?"Connecting to engine…":"Waiting for camera…"}</span>
                    </div>
                  {/if}
                </div>
                <div class="cam-pane-status" class:cam-status-ok={pythonCameraOk} class:cam-status-err={pythonCameraStatus==="error"} class:cam-status-warn={trackerCameraPaused}>
                  <span class="cam-dot"></span>
                  {pythonCameraOk?"Connected":trackerCameraPaused?"Released":pythonCameraStatus==="error"?"Error":pythonCameraStatus==="opening"?"Opening…":"Waiting"}
                </div>
              </div>
            </div>

            <div class="cam-below">
              {#if browserDevices.length > 0}
                <div class="device-row">
                  <label for="sv-cam">Camera</label>
                  <select id="sv-cam" on:change={handleCameraDeviceChange}
                    disabled={pythonCameraStatus==="opening"||cameraStatus==="requesting"}>
                    {#each browserDevices as d}
                      <option value={d.deviceId} selected={d.deviceId===selectedBrowserDeviceId}>
                        {d.label||`Camera ${d.deviceId.slice(0,6)}…`}
                      </option>
                    {/each}
                  </select>
                  {#if pythonCameraStatus==="opening"||cameraStatus==="requesting"}
                    <span class="spin select-spin">◌</span>
                  {/if}
                  {#if restartNeeded}<button class="btn-sm" on:click={restartTracker}>Restart</button>{/if}
                </div>
              {/if}
              <div class="cam-prereq" class:cam-prereq-ok={bothCamerasOk}>
                {#if bothCamerasOk}
                  <span class="cam-prereq-title cam-prereq-title-ok">Camera sharing is working</span>
                  <p class="cam-prereq-body">Both feeds are connected to the same device. You're good to continue.</p>
                {:else}
                  <span class="cam-prereq-title">Required — enable Windows camera sharing</span>
                  <p class="cam-prereq-body">MKW Tracker needs simultaneous access to the same capture card as the app preview. Windows blocks this by default. Do this once before continuing:</p>
                  {#if trackerCameraPaused}
                    <div class="cam-release-bar cam-release-bar-released">
                      <span class="cam-release-dot"></span>
                      <span class="cam-release-msg">App feeds released — also close OBS, Discord, and any other apps currently using the camera before proceeding.</span>
                    </div>
                  {:else}
                    <div class="cam-release-bar">
                      <span class="cam-release-dot"></span>
                      <span class="cam-release-msg">Release this app's feeds and close OBS, Discord, and any other apps currently using the camera before changing this setting.</span>
                      <div style="display:flex;gap:.4rem;flex-shrink:0">
                        <button class="btn-sm" on:click={releaseForSettings}>Release feeds</button>
                        <button class="btn-sm" on:click={retryNow}>Retry</button>
                      </div>
                    </div>
                  {/if}
                  <ol class="cam-steps">
                    <li>Click <strong>Open Windows Camera Settings</strong> below</li>
                    <li>Find your capture card → <strong>Advanced camera options</strong> → <strong>Edit</strong></li>
                    <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                    <li>Return here, then <button class="btn-sm" on:click={retryNow}>Retry</button></li>
                  </ol>
                  <div class="cam-prereq-actions">
                    <button class="btn-primary" on:click={() => invoke("open_url",{url:"ms-settings:camera"}).catch(()=>{})}>Open Windows Camera Settings →</button>
                  </div>
                {/if}
              </div>

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>← Back</button>
                  <button class="btn-primary" disabled={!bothCamerasOk} on:click={()=>goStep("done")}>
                    Next →
                  </button>
                </div>
              </div>
            </div>
          </div>

        {:else if wizardStep === "done"}
          <div class="step-centred">
            <div class="done-check">✓</div>
            <h2>Setup Complete</h2>
            <p>Your templates are saved and ready.</p>
            <button class="btn-primary btn-lg" on:click={completeSetup}>Start Tracking →</button>
          </div>
        {/if}
      </div>
    </div>

    <!-- Engine log sidebar — always visible during first-time setup -->
    <div class="setup-log-side">
      <div class="setup-log-hdr">
        <span class="hb-dot" style="background:{statusDot}; flex-shrink:0"></span>
        {#if trackerConnected}
          <span class="setup-log-status">Engine ready</span>
        {:else if trackerSpawned}
          <span class="setup-log-status">Starting — Windows may be scanning files…</span>
        {:else}
          <span class="setup-log-status">Launching engine…</span>
        {/if}
      </div>
      <div class="setup-log-body" bind:this={logEl}>
        {#each logs as line}
          <div class="log-line">{line}</div>
        {/each}
        {#if logs.length === 0}
          <div class="log-empty">Waiting for events…</div>
        {/if}
      </div>
    </div>
  </div><!-- /setup-view -->

  {:else}

  <!-- ── Startup view: shown while waiting for first ready message ───────────── -->
  <div class="startup-view">
    <div class="startup-main">
      <div class="startup-status">
        <span class="hb-dot startup-dot" style="background:{statusDot}"></span>
        {#if trackerConnected}
          <span class="startup-status-text">Engine connected</span>
        {:else if trackerSpawned}
          <div>
            <span class="startup-status-text">Engine starting…</span>
            <div class="startup-status-sub">Windows may be scanning files on first run. This can take up to 2 minutes.</div>
          </div>
        {:else}
          <span class="startup-status-text">Launching engine…</span>
        {/if}
      </div>
      <p class="startup-note">On first launch, Windows Defender may scan the engine files before they can run. This is normal and should take under a few minutes.</p>
    </div>
    <div class="setup-log-side">
      <div class="setup-log-hdr">
        <span class="hb-dot" style="background:{statusDot}; flex-shrink:0"></span>
        {#if trackerConnected}
          <span class="setup-log-status">Engine ready</span>
        {:else if trackerSpawned}
          <span class="setup-log-status">Starting — Windows may be scanning files…</span>
        {:else}
          <span class="setup-log-status">Launching engine…</span>
        {/if}
      </div>
      <div class="setup-log-body" bind:this={logEl}>
        {#each logs as line}
          <div class="log-line">{line}</div>
        {/each}
        {#if sidecarStartupError}
          <div class="log-empty log-error">
            Engine failed to start after 2 minutes. Check that your antivirus isn't blocking
            <code>bin\mkw-tracker-engine.exe</code>, then restart the app.
          </div>
        {:else if logs.length === 0}
          <div class="log-empty">Waiting for events…</div>
        {/if}
      </div>
    </div>
  </div><!-- /startup-view -->

  {/if}<!-- /view router -->

</div><!-- /app -->


<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  WIZARD DIALOG (re-run setup: camera, screens, selection, hud, templates)  -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

{#if wizardOpen}
  <div class="modal-backdrop wiz-backdrop" on:click|self={setupComplete ? closeWizard : undefined}>
    <div class="wiz-dialog" class:wiz-dialog-narrow={wizardStep === "language"}>

      <!-- Wizard tabs -->
      <nav class="wiz-tabs">
        {#each STEPS as s}
          <button class="wiz-tab" class:active={wizardStep===s} on:click={()=>goStep(s)}>
            {STEP_LABELS[s]}
          </button>
        {/each}
        {#if setupComplete}
          <button class="wiz-tab-close" on:click={closeWizard} title="Close">✕</button>
        {/if}
      </nav>

      <div class="wiz-body">

        <!-- ── LANGUAGE step ────────────────────────────────────────────── -->
        {#if wizardStep === "language"}
          <div class="step-centred">
            <h2>{tr("lang.title")}</h2>
            <p>{tr("lang.desc")}</p>
            <div class="lang-form">
              <div class="lang-row">
                <label for="wiz-app-lang">{tr("lang.app_label")}</label>
                <select id="wiz-app-lang" bind:value={appLanguage} on:change={onAppLanguageChange}>
                  {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
                </select>
              </div>
              <div class="lang-row">
                <label for="wiz-sw2-lang">{tr("lang.sw2_label")}</label>
                <select id="wiz-sw2-lang" bind:value={switch2Language} on:change={onSwitch2LanguageChange}>
                  {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
                </select>
                <p class="hint lang-hint">{tr("lang.sw2_hint")}</p>
              </div>
            </div>
            <button class="btn-primary btn-lg" on:click={()=>goStep("camera")}>{tr("lang.continue")}</button>
          </div>

        <!-- ── CAMERA step ──────────────────────────────────────────────── -->
        {:else if wizardStep === "camera"}
          <div class="cam-setup">
            <div class="cam-dual">
              <div class="cam-pane">
                <div class="cam-pane-label">Browser / App Input</div>
                <div class="preview-wrapper">
                  {#if cameraOk}
                    <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                  {:else if cameraStatus === "requesting"}
                    <div class="preview-placeholder"><span class="spin">◌</span><span>Opening…</span></div>
                  {:else if cameraStatus === "busy"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Blocked — device in exclusive use</span>
                    </div>
                  {:else if cameraStatus === "error"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span><span class="cam-pane-err-label">Camera error</span>
                    </div>
                  {:else if trackerCameraPaused}
                    <div class="preview-placeholder">
                      <span class="preview-icon" style="color:#888">○</span>
                      <span class="cam-pane-err-label">Camera released</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder"><span class="spin">◌</span><span>Waiting…</span></div>
                  {/if}
                </div>
                <div class="cam-pane-status" class:cam-status-ok={cameraOk} class:cam-status-err={cameraStatus==="busy"||cameraStatus==="error"} class:cam-status-warn={trackerCameraPaused&&!cameraOk}>
                  <span class="cam-dot"></span>
                  {cameraOk?"Connected":cameraStatus==="requesting"?"Opening…":cameraStatus==="busy"?"Blocked":cameraStatus==="error"?"Error":trackerCameraPaused?"Released":"Waiting"}
                </div>
              </div>

              <div class="cam-pane">
                <div class="cam-pane-label">Python Engine Input</div>
                <div class="preview-wrapper">
                  {#if engineFrame && !trackerCameraPaused}
                    <img src={engineFrame} alt="Engine feed" class="preview-video" style="object-fit:contain"/>
                  {:else if trackerCameraPaused}
                    <div class="preview-placeholder">
                      <span class="preview-icon" style="color:#888">○</span>
                      <span class="cam-pane-err-label">Camera released</span>
                    </div>
                  {:else if pythonCameraStatus === "error"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Can't access device{pythonCameraError?`: ${pythonCameraError}`:""}</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder">
                      <span class="spin">◌</span>
                      <span>{pythonCameraStatus==="opening"?"Opening and verifying…":!trackerConnected?"Connecting to engine…":"Waiting for camera…"}</span>
                    </div>
                  {/if}
                </div>
                <div class="cam-pane-status" class:cam-status-ok={pythonCameraOk} class:cam-status-err={pythonCameraStatus==="error"} class:cam-status-warn={trackerCameraPaused}>
                  <span class="cam-dot"></span>
                  {pythonCameraOk?"Connected":trackerCameraPaused?"Released":pythonCameraStatus==="error"?"Error":pythonCameraStatus==="opening"?"Opening…":"Waiting"}
                </div>
              </div>
            </div>

            <div class="cam-below">
              {#if browserDevices.length > 0}
                <div class="device-row">
                  <label for="wiz-cam">Camera</label>
                  {#if pythonCameraStatus==="opening"||cameraStatus==="requesting"}
                    <div class="select-loading">
                      <span class="spin">◌</span>
                      <span>{browserDevices.find(d=>d.deviceId===selectedBrowserDeviceId)?.label||"Opening…"}</span>
                    </div>
                  {:else}
                    <select id="wiz-cam" on:change={handleCameraDeviceChange}>
                      {#each browserDevices as d}
                        <option value={d.deviceId} selected={d.deviceId===selectedBrowserDeviceId}>
                          {d.label||`Camera ${d.deviceId.slice(0,6)}…`}
                        </option>
                      {/each}
                    </select>
                  {/if}
                  {#if restartNeeded}<button class="btn-sm" on:click={restartTracker}>Restart</button>{/if}
                </div>
              {/if}

              {#if !setupComplete}
                <div class="cam-prereq" class:cam-prereq-ok={bothCamerasOk}>
                  {#if bothCamerasOk}
                    <span class="cam-prereq-title cam-prereq-title-ok">Camera sharing is working</span>
                    <p class="cam-prereq-body">Both feeds are connected to the same device. You're good to continue.</p>
                  {:else}
                    <span class="cam-prereq-title">Required — enable Windows camera sharing</span>
                    <p class="cam-prereq-body">MKW Tracker needs simultaneous access to the same capture card as the app preview. Windows blocks this by default. Do this once before continuing:</p>
                    {#if trackerCameraPaused}
                      <div class="cam-release-bar cam-release-bar-released">
                        <span class="cam-release-dot"></span>
                        <span class="cam-release-msg">App feeds released — also close OBS, Discord, and any other apps currently using the camera before proceeding.</span>
                      </div>
                    {:else}
                      <div class="cam-release-bar">
                        <span class="cam-release-dot"></span>
                        <span class="cam-release-msg">Release this app's feeds and close OBS, Discord, and any other apps currently using the camera before changing this setting.</span>
                        <div style="display:flex;gap:.4rem;flex-shrink:0">
                          <button class="btn-sm" on:click={releaseForSettings}>Release feeds</button>
                          <button class="btn-sm" on:click={retryNow}>Retry</button>
                        </div>
                      </div>
                    {/if}
                    <ol class="cam-steps">
                      <li>Click <strong>Open Windows Camera Settings</strong> below</li>
                      <li>Find your capture card → <strong>Advanced camera options</strong> → <strong>Edit</strong></li>
                      <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                      <li>Return here, then <button class="btn-sm" on:click={retryNow}>Retry</button></li>
                    </ol>
                    <div class="cam-prereq-actions">
                      <button class="btn-primary" on:click={() => invoke("open_url",{url:"ms-settings:camera"}).catch(()=>{})}>Open Windows Camera Settings →</button>
                    </div>
                  {/if}
                </div>
              {/if}

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>← Back</button>
                  <button class="btn-primary" on:click={closeWizard}>Done</button>
                </div>
              </div>
            </div>
          </div>

        <!-- ── SCREENS step ─────────────────────────────────────────────── -->
        {:else if wizardStep === "screens"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown} on:mousemove={onCanvasMouseMove}></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem" on:click={()=>goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to reposition ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{screenIdx+1} / {SCREEN_NAMES.length}</span>
                <h3>{currentScreenLabel}</h3>
              </div>
              <p class="hint">{currentScreenHint}</p>
              {#if TELL_GROUP_NOTES[SCREEN_NAMES[screenIdx]]}
                <p class="hint tell-group-note">{TELL_GROUP_NOTES[SCREEN_NAMES[screenIdx]]}</p>
              {/if}
              {#if currentTell}
                {@const allRois=getAllRoisForTell(currentTell)}
                <div class="roi-tabs">
                  {#each allRois as re}
                    <button class="roi-tab" class:active={activeRoiKey===re.key}
                      class:roi-tab-and={re.type==="and"} class:roi-tab-or={re.type==="or"}
                      on:click={()=>{activeRoiKey=re.key;syncThreshToScreen();drawRoi();}}>
                      {re.label}
                    </button>
                    {#if activeRoiKey===re.key && re.type!=="primary"}
                      <button class="roi-tab-remove" title="Remove"
                        on:click={()=>re.type==="and"?removeRequiredAlso(parseInt(re.key.slice(4))):removeAlt()}>×</button>
                    {/if}
                  {/each}
                  {#if !currentTell.required_also?.length}
                    <button class="roi-tab roi-tab-add roi-tab-and" on:click={addRequiredAlso}>+ AND</button>
                  {/if}
                  {#if !currentTell.alt_image_path}
                    <button class="roi-tab roi-tab-add roi-tab-or" on:click={addAlt}>+ OR Alt</button>
                  {/if}
                </div>
                {@const activeRoiEntry=allRois.find(r=>r.key===activeRoiKey)}
                {#if activeRoiEntry?.roi}
                  {@const r=activeRoiEntry.roi}
                  <div class="roi-chip">({r[0]},{r[1]})→({r[2]},{r[3]})<span class="roi-size">{r[2]-r[0]}×{r[3]-r[1]}px</span></div>
                {/if}
              {/if}
              {#if currentScore}
                <div class="score-box" class:good={currentScore.matched} class:bad={!currentScore.matched}>
                  <span class="score-icon">{currentScore.matched?"✓":"✗"}</span>
                  <span class="score-val">{currentScore.score.toFixed(3)}</span>
                  <span class="score-thr">/ {currentScore.threshold.toFixed(2)}</span>
                  <span class="score-lbl">{currentScore.matched?"Detected":"Not detected"}</span>
                </div>
              {:else if capturingTemplate}
                <p class="score-msg">Saving new template…</p>
              {:else}
                <p class="score-msg">Updating live score…</p>
              {/if}
              {#if activeRoiKey!=="primary"||currentTell?.binary_thresh!=null}
                <div class="thresh-row">
                  <label class="thresh-label">Binarize</label>
                  <input type="range" min="0" max="255" step="1" bind:value={currentBinaryThresh} on:input={onThreshChange} class="thresh-slider"/>
                  <span class="thresh-val">{currentBinaryThresh}</span>
                </div>
              {:else}
                <p class="hint" style="font-size:.65rem">Auto threshold (Otsu)</p>
              {/if}
              <div class="btn-row">
                <button class="btn-secondary" on:click={captureScreen} disabled={capturingTemplate}>
                  {capturingTemplate?"Saving…":"Capture New Template"}
                </button>
              </div>
              <p class="capture-note"><strong>Capture</strong> crops the current frame to this ROI and saves it as the new template.</p>
              <div class="tmpl-compare">
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">Saved Template</div>
                  {#if templateImg}<img src={templateImg} alt="Saved template" class="tmpl-img"/>
                  {:else}<div class="tmpl-empty">—</div>{/if}
                </div>
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">Live ROI Crop</div>
                  {#if liveCropImg}<img src={liveCropImg} alt="Live crop" class="tmpl-img"/>
                  {:else}<div class="tmpl-empty">Live…</div>{/if}
                </div>
              </div>
            </div>
          </div>

        <!-- ── SELECTION step ───────────────────────────────────────────── -->
        {:else if wizardStep === "selection"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown} on:mousemove={onCanvasMouseMove}></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem" on:click={()=>goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to adjust ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{selectionIdx+1} / {SELECTION_ROIS.length}</span>
                <h3>{selItem?.label}</h3>
              </div>
              <p class="hint">{selItem?.hint}</p>
              {#if rois[selItem?.key]}
                {@const r=rois[selItem.key]}
                <div class="roi-chip">({r[0]},{r[1]})→({r[2]},{r[3]})<span class="roi-size">{r[2]-r[0]}×{r[3]-r[1]}px</span></div>
              {/if}
              {#if SELECTION_ROIS[selectionIdx]?.key==="costume"}
                <p class="hint" style="font-size:.65rem">Edge detection (Canny) — background-agnostic</p>
              {:else}
                <div class="thresh-row">
                  <label class="thresh-label">Binarize</label>
                  <input type="range" min="0" max="255" step="1" bind:value={currentBinaryThresh} class="thresh-slider"/>
                  <span class="thresh-val">{currentBinaryThresh}</span>
                </div>
              {/if}
              <div class="tmpl-pane">
                <div class="tmpl-pane-label">Live Crop{SELECTION_ROIS[selectionIdx]?.key==="costume"?" (edges)":""}</div>
                {#if liveRoiCrop}<img src={liveRoiCrop} alt="Live ROI crop" class="tmpl-img"/>
                {:else}<div class="tmpl-empty">Live…</div>{/if}
              </div>
            </div>
          </div>

        <!-- ── HUD step ─────────────────────────────────────────────────── -->
        {:else if wizardStep === "hud"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={wizVideoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown} on:mousemove={onCanvasMouseMove}></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem" on:click={()=>goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to adjust ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{hudIdx+1} / {HUD_ROIS.length}</span>
                <h3>{hudItem?.label}</h3>
              </div>
              <p class="hint">{hudItem?.hint}</p>
              {#if rois[hudItem?.key]}
                {@const r=rois[hudItem.key]}
                <div class="roi-chip">({r[0]},{r[1]})→({r[2]},{r[3]})<span class="roi-size">{r[2]-r[0]}×{r[3]-r[1]}px</span></div>
              {/if}
              <div class="thresh-row">
                <label class="thresh-label">Binarize</label>
                <input type="range" min="0" max="255" step="1" bind:value={currentBinaryThresh} class="thresh-slider"/>
                <span class="thresh-val">{currentBinaryThresh}</span>
              </div>
              <div class="tmpl-pane">
                <div class="tmpl-pane-label">Live Crop</div>
                {#if liveRoiCrop}<img src={liveRoiCrop} alt="Live ROI crop" class="tmpl-img"/>
                {:else}<div class="tmpl-empty">Live…</div>{/if}
              </div>
            </div>
          </div>

        <!-- ── TEMPLATES step ───────────────────────────────────────────── -->
        {:else if wizardStep === "templates"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if engineFrame}
                  <img src={engineFrame} alt="Engine feed" class="preview-video" style="object-fit:contain"/>
                {:else}
                  <div class="preview-placeholder"><span class="spin">◌</span><span>Waiting for feed…</span></div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · ROI from {ASSET_ROI_KEYS[templateCategory]} step</p>
            </div>
            <div class="info-col">
              <div class="asset-cat-tabs">
                {#each ASSET_CATEGORIES as cat}
                  <button class="asset-cat-tab" class:active={templateCategory===cat.key}
                    on:click={()=>{templateCategory=cat.key;templateItemIdx=0;assetTemplateImg=null;assetLiveCrop=null;}}>
                    {cat.label}
                  </button>
                {/each}
              </div>
              {#if assetItem}
                <div class="item-header">
                  <span class="item-num">{templateItemIdx+1} / {ASSET_ITEMS[templateCategory].length}</span>
                  <h3>{assetItem.name}</h3>
                </div>
                <p class="hint">{ASSET_HINTS[templateCategory]?.(assetItem.name)}</p>
              {/if}
              <div class="btn-row">
                <button class="btn-secondary" on:click={captureAsset} disabled={capturingTemplate}>
                  {capturingTemplate?"Saving…":"Capture Template"}
                </button>
              </div>
              <p class="capture-note"><strong>Capture</strong> saves the live crop as the new template for {assetItem?.name}.</p>
              <div class="tmpl-compare">
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">No template</div>
                  {#if assetTemplateImg}<img src={assetTemplateImg} alt="Saved template" class="tmpl-img"/>
                  {:else}<div class="tmpl-empty">—</div>{/if}
                </div>
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">Live feed · ROI from {templateCategory} step</div>
                  {#if assetLiveCrop}<img src={assetLiveCrop} alt="Live crop" class="tmpl-img"/>
                  {:else}<div class="tmpl-empty">Live…</div>{/if}
                </div>
              </div>
            </div>
          </div>

        <!-- ── DONE step ────────────────────────────────────────────────── -->
        {:else if wizardStep === "done"}
          <div class="step-centred">
            <div class="done-check">✓</div>
            <h2>Setup Complete</h2>
            <p>Your templates are saved and ready. The tracker will use them for screen detection immediately.</p>
            <p>Re-run Setup anytime if detection quality degrades.</p>
            <button class="btn-primary btn-lg" on:click={completeSetup}>Close Setup</button>

            <div class="reset-defaults-section">
              {#if !resetConfirmPending}
                <button class="btn-reset-defaults" on:click={() => resetConfirmPending = true}>
                  Reset all ROIs &amp; thresholds to defaults
                </button>
              {:else}
                <p class="reset-confirm-label">This will clear all custom ROIs and thresholds. Are you sure?</p>
                <div class="reset-confirm-row">
                  <button class="btn-reset-confirm" on:click={() => { send({type:"reset_to_defaults"}); resetConfirmPending = false; }}>
                    Yes, reset to defaults
                  </button>
                  <button class="btn-nav" on:click={() => resetConfirmPending = false}>Cancel</button>
                </div>
              {/if}
            </div>
          </div>
        {/if}

      </div><!-- /wiz-body -->

      <!-- Wizard footer navigation (for screen-editing steps) -->
      {#if ["screens","selection","hud","templates"].includes(wizardStep)}
        <div class="wiz-footer">
          <button class="btn-nav" on:click={prevItem}>← Back</button>
          <div class="dot-row">
            {#if wizardStep === "screens"}
              {#each SCREEN_NAMES as sn, i}
                <span class="nav-dot nav-dot-sm" class:active={i===screenIdx}></span>
              {/each}
            {:else if wizardStep === "selection"}
              {#each SELECTION_ROIS as _, i}
                <span class="nav-dot nav-dot-lg" class:active={i===selectionIdx}></span>
              {/each}
            {:else if wizardStep === "hud"}
              {#each HUD_ROIS as _, i}
                <span class="nav-dot nav-dot-lg" class:active={i===hudIdx}></span>
              {/each}
            {/if}
          </div>
          <button class="btn-nav" on:click={nextItem}>Next →</button>
        </div>
      {/if}

    </div><!-- /wiz-dialog -->
  </div><!-- /wiz-backdrop -->
{/if}

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  LANGUAGE DIALOG                                                           -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<dialog bind:this={langDialogEl} class="lang-dialog">
  <h3 class="ldlg-title">Language Settings</h3>
  <div class="ldlg-form">
    <div class="ldlg-row">
      <label class="ldlg-label" for="ldlg-app">Application Language</label>
      <select id="ldlg-app" bind:value={langDlgApp}>
        {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
      </select>
    </div>
    <div class="ldlg-row">
      <label class="ldlg-label" for="ldlg-sw2">Switch 2 System Language</label>
      <select id="ldlg-sw2" bind:value={langDlgSw2}>
        {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
      </select>
      <p class="ldlg-hint">Determines which image templates are used for detection (characters, courses, menus, etc.).</p>
    </div>
  </div>
  <div class="ldlg-actions">
    <button class="btn-secondary" on:click={()=>langDialogEl?.close()}>Cancel</button>
    <button class="btn-primary" on:click={saveLangDialog}>Save</button>
  </div>
</dialog>

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  STYLES                                                                    -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<style>
  :global(*) { box-sizing: border-box; margin: 0; padding: 0; }
  :global(body) {
    background: #080810; color: #e8e8f0;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 13px; overflow: hidden;
  }

  /* ── App shell ────────────────────────────────────────────────── */
  .app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

  /* ── Title bar ────────────────────────────────────────────────── */
  .titlebar {
    display: flex; align-items: center; height: 40px; flex-shrink: 0;
    background: #04040a; border-bottom: 1px solid #111120;
    padding: 0 0 0 12px; gap: 8px;
    -webkit-app-region: drag; user-select: none;
  }
  .tb-brand { display: flex; align-items: baseline; gap: 5px; flex-shrink: 0; }
  .brand-name { font-size: .85rem; font-weight: bold; color: #7eb8f7; letter-spacing: .02em; }
  .brand-ver  { font-size: .65rem; color: #333; }

  .lang-badge {
    display: flex; align-items: center; gap: 4px;
    background: #06060e; border: 1px solid #1a1a2e; border-radius: 3px;
    padding: 2px 7px; font-family: inherit; font-size: .65rem; cursor: pointer;
    color: #888; transition: background .12s, border-color .12s;
    -webkit-app-region: no-drag; flex-shrink: 0;
  }
  .lang-badge:hover { background: #0d0d1a; border-color: #2a2a4a; color: #bbb; }
  .lang-app { color: #7eb8f7; }
  .lang-sep  { color: #333; }
  .lang-sw2  { color: #5a8ab0; }

  .tb-health { flex: 1; display: flex; align-items: center; gap: 6px; font-size: .7rem; min-width: 0; overflow: hidden; }
  .hb-dot    { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; transition: background .6s; }
  .hb-screen { color: #7eb8f7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hb-score  { font-size: .7rem; font-family: inherit; }
  .hb-fps    { color: #555; }
  .hb-sep    { color: #222; }
  .hb-warn   { color: #f59e0b; }
  .hb-idle   { color: #333; font-style: italic; }

  .tb-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; -webkit-app-region: no-drag; }
  .upd-strip  { display: flex; align-items: center; gap: 5px; font-size: .65rem; }
  .upd-label  { color: #4caf50; flex-shrink: 0; }
  .upd-track  { width: 60px; height: 3px; background: #111122; border-radius: 2px; overflow: hidden; }
  .upd-fill   { height: 100%; background: #4caf50; transition: width .2s; }

  .btn-hdr {
    background: #06060e; border-radius: 3px; padding: 3px 9px;
    font-family: inherit; font-size: .68rem; cursor: pointer; white-space: nowrap;
    transition: background .12s; -webkit-app-region: no-drag;
  }
  .btn-setup       { color: #7eb8f7; border: 1px solid #1e1e4a; }
  .btn-setup:hover { background: #0d0d22; }
  .btn-edit        { color: #7ef7b8; border: 1px solid #173a2a; }
  .btn-edit:hover  { background: #0a1a12; }
  .btn-close-wiz   { color: #666; border: 1px solid #1e1e2e; }
  .btn-close-wiz:hover { color: #bbb; background: #111120; }

  /* ── Edit Screens view ──────────────────────────────────────── */
  .edit-view { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 10px; }
  /* Graph is wide-and-short → keep it as a slim scrollable strip on top at native
     (legible) size; the editor pane below takes all remaining space and dominates. */
  .edit-split { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
  .edit-graph { flex: none; height: 248px; display: flex; flex-direction: column; background: #06060e; border: 1px solid #14142a; border-radius: 6px; overflow: hidden; }
  .edit-graph-vp { flex: 1; min-height: 0; overflow: hidden; }
  .graph-svg-zoom { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
  .graph-svg-zoom.panning { cursor: grabbing; }
  .edit-graph-foot { flex: none; border-top: 1px solid #14142a; padding: 3px 8px; font-size: .58rem; color: #566; text-align: right; }
  .edit-pane { flex: 1; min-height: 0; overflow: auto; background: #06060e; border: 1px solid #14142a; border-radius: 6px; padding: 10px 12px; }
  .edit-pane-hd { display: flex; align-items: baseline; gap: 8px; border-bottom: 1px solid #14142a; padding-bottom: 6px; margin-bottom: 8px; }
  .edit-pane-hd h3 { margin: 0; font-size: .9rem; color: #cde; }
  .edit-screen-id { font-family: Consolas, monospace; font-size: .62rem; color: #567; }
  .edit-tabs { display: flex; gap: 4px; margin-bottom: 10px; }
  .edit-tabs button {
    background: #08081200; border: none; border-bottom: 2px solid transparent;
    color: #5a7a9a; font-family: inherit; font-size: .72rem; padding: 4px 8px; cursor: pointer;
  }
  .edit-tabs button.active { color: #7eb8f7; border-bottom-color: #7eb8f7; }
  .edit-tabs button:hover:not(.active) { color: #9ab; }
  .edit-pane-empty { display: flex; align-items: center; justify-content: center; min-height: 240px; }

  /* Detection tab editor */
  .det-editor { display: flex; gap: 12px; align-items: flex-start; }
  .det-feed { flex: 1.7; min-width: 0; }
  .det-feed .preview-wrapper { position: relative; width: 100%; aspect-ratio: 16/9; background: #000; border: 1px solid #14142a; border-radius: 5px; overflow: hidden; }
  .det-zoom { position: absolute; inset: 0; will-change: transform; }
  .det-feed .preview-video { width: 100%; height: 100%; object-fit: contain; }
  .det-feed .roi-canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
  .det-zoom-reset { position: absolute; right: 6px; top: 6px; z-index: 2; background: #06060ecc; border: 1px solid #2a3a5a; color: #9cf; border-radius: 3px; font-family: Consolas, monospace; font-size: .6rem; padding: 2px 6px; cursor: pointer; }
  .det-zoom-reset:hover { background: #0d1f40; }
  .det-feed .preview-cap { margin: 5px 2px 0; font-size: .64rem; color: #566; }
  .det-tree { flex: 1; min-width: 250px; display: flex; flex-direction: column; gap: 6px; }
  .tree-label { font-size: .66rem; text-transform: uppercase; letter-spacing: .08em; color: #8aa; }
  .tree-and { text-align: center; font-size: .62rem; letter-spacing: .2em; color: #5a7a9a; margin: 1px 0; }
  .tree-group { border: 1px solid #1c2740; border-radius: 5px; padding: 6px; background: #080a14; }
  .tree-group-hd { font-size: .58rem; text-transform: uppercase; letter-spacing: .06em; color: #5a7a9a; margin-bottom: 4px; }
  .tree-region { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left; background: #0c0c18; border: 1px solid #1a1a2e; border-radius: 4px; padding: 4px 7px; margin-bottom: 3px; color: #aab; font-family: inherit; font-size: .72rem; cursor: pointer; }
  .tree-region:hover { border-color: #2a3a5a; }
  .tree-region.sel { border-color: #7eb8f7; background: #0d1f40; color: #cde; }
  .treg-dot { width: 9px; height: 9px; border-radius: 2px; flex: none; }
  .treg-name { flex: 1; }
  .treg-score { font-family: Consolas, monospace; font-size: .68rem; }
  .tree-add { width: 100%; background: none; border: 1px dashed #243; border-radius: 4px; color: #567; font-family: inherit; font-size: .64rem; padding: 3px; cursor: pointer; }
  .tree-add:hover { color: #8aa; border-color: #2a4a3a; }
  .tree-add-and { border-color: #2a3a5a; margin-top: 2px; }
  .reg-controls { border-top: 1px solid #14142a; margin-top: 4px; padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .reg-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .reg-kind { font-size: .66rem; color: #9ab; display: flex; align-items: center; gap: 5px; }
  .reg-kind select { background: #0c0c18; color: #cde; border: 1px solid #1e1e3a; border-radius: 3px; font-family: inherit; font-size: .7rem; padding: 2px 4px; }
  .reg-del { background: none; border: 1px solid #3a1e1e; color: #b66; border-radius: 3px; font-family: inherit; font-size: .64rem; padding: 3px 7px; cursor: pointer; }
  .reg-del:hover { background: #1a0d0d; }
  .reg-thumbs { display: flex; gap: 8px; }
  .reg-thumb { flex: 1; font-size: .58rem; color: #678; text-align: center; }
  .reg-thumb img, .reg-thumb-empty { display: block; width: 100%; height: 40px; object-fit: contain; background: #0c0c18; border: 1px solid #1a1a2e; border-radius: 3px; margin-top: 2px; image-rendering: pixelated; }
  .reg-thresh { font-size: .66rem; color: #9ab; display: block; }
  .reg-thresh input { width: 100%; }
  .reg-recap { font-size: .7rem; align-self: flex-start; }
  .det-reset { border-top: 1px solid #14142a; margin-top: 4px; padding-top: 8px; }
  .det-reset-btn { width: 100%; background: none; border: 1px solid #2a2a3e; border-radius: 4px; color: #889; font-family: inherit; font-size: .66rem; padding: 5px; cursor: pointer; }
  .det-reset-btn:hover { border-color: #4a2a2a; color: #c99; }
  .det-reset-q { font-size: .68rem; color: #c99; margin: 0 0 6px; }
  .det-reset-row { display: flex; gap: 8px; }

  .win-controls { display: flex; flex-shrink: 0; margin-left: 0; }
  .win-btn {
    background: transparent; border: none; color: #444;
    width: 46px; height: 40px; font-size: .78rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .1s, color .1s; flex-shrink: 0;
    -webkit-app-region: no-drag;
  }
  .win-btn:hover { background: #111120; color: #e8e8f0; }
  .win-btn-close:hover { background: #c42b1c; color: #fff; }

  /* ── Main grid ────────────────────────────────────────────────── */
  .main-grid {
    display: grid;
    grid-template-columns: 1fr 256px;
    grid-template-rows: 1fr auto;
    flex: 1; min-height: 0; overflow: hidden;
  }

  /* ── Feed (col 1, row 1) ─────────────────────────────────────── */
  .main-feed {
    grid-column: 1; grid-row: 1;
    display: flex; flex-direction: column;
    background: #000; overflow: hidden; min-height: 0;
  }
  .feed-area {
    flex: 1; min-height: 0; position: relative; overflow: hidden;
    background: #000;
  }
  .feed-video {
    width: 100%; height: 100%; object-fit: contain; display: block;
  }
  .feed-hidden { display: none; }
  .feed-placeholder {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: .5rem; color: #222; font-size: .8rem;
  }
  .feed-ph-dim { color: #1e1e2e; font-size: .68rem; }

  /* ── Feed controls ────────────────────────────────────────────── */
  .feed-controls {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 10px; flex-shrink: 0;
    background: #04040a; border-top: 1px solid #0d0d18;
    height: 28px;
  }
  .fc-btn {
    background: transparent; border: none; cursor: pointer; color: #555;
    display: flex; align-items: center; gap: 3px; padding: 2px 3px;
    border-radius: 3px; transition: color .1s, background .1s; flex-shrink: 0;
  }
  .fc-btn:hover { color: #999; background: #0d0d1a; }
  .fc-icon {
    width: 14px; height: 14px; fill: none;
    stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round;
    flex-shrink: 0;
  }
  .fc-slider {
    flex: 1; min-width: 60px; max-width: 120px;
    accent-color: #7eb8f7; cursor: pointer; height: 3px;
  }
  .fc-vol {
    font-size: .6rem; color: #555; min-width: 2.4em; text-align: right; flex-shrink: 0;
  }
  .fc-divider { width: 1px; height: 14px; background: #1a1a2e; flex-shrink: 0; margin: 0 2px; }
  .fc-no-audio { font-size: .58rem; color: #2a2a3a; flex-shrink: 0; }
  .fc-vid-btn  { gap: 4px; }
  .fc-vid-label { font-size: .62rem; }
  .feed-ph-icon { font-size: 1.8rem; animation: spin 1.4s linear infinite; opacity: .4; }
  .feed-ph-text { font-size: .72rem; color: #222; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Sidebar (col 2, rows 1-2) ──────────────────────────────── */
  .sidebar {
    grid-column: 2; grid-row: 1 / 3;
    display: flex; flex-direction: column;
    background: #06060e; border-left: 1px solid #111120;
    overflow-y: auto; overflow-x: hidden;
    scrollbar-width: thin; scrollbar-color: #1a1a2e #06060e;
    min-height: 0;
  }

  /* ── Graph row (col 1, row 2) ────────────────────────────────── */
  .graph-row {
    grid-column: 1; grid-row: 2;
    border-top: 1px solid #111120; background: #04040a;
  }
  .graph-toggle {
    display: flex; align-items: center; justify-content: space-between;
    width: 100%; background: transparent; border: none; color: #444;
    padding: 4px 10px; font-family: inherit; font-size: .65rem;
    cursor: pointer; transition: color .12s;
  }
  .graph-toggle:hover { color: #888; }
  .graph-chev { color: #333; font-size: .6rem; }
  .graph-content { padding: 4px 8px 8px; overflow-x: auto; }
  .graph-svg { width: 100%; min-width: 700px; height: 215px; display: block; }

  /* ── Panel ────────────────────────────────────────────────────── */
  .panel { border-bottom: 1px solid #111120; }
  .panel-hdr {
    display: flex; align-items: center; justify-content: space-between;
    width: 100%; background: transparent; border: none; color: #555;
    padding: 7px 10px; font-family: inherit; font-size: .68rem;
    cursor: pointer; text-align: left; transition: background .1s, color .1s;
    text-transform: uppercase; letter-spacing: .06em;
  }
  .panel-hdr:hover { background: #080818; color: #888; }
  .panel-title { flex: 1; }
  .panel-chev  { font-size: .6rem; color: #333; }
  .panel-body  { padding: 6px 10px 8px; display: flex; flex-direction: column; gap: 5px; }
  .panel-empty { font-size: .66rem; color: #333; font-style: italic; }
  .panel-log   { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .log-body {
    flex: 1; min-height: 0;
    overflow-y: auto; overflow-x: hidden;
    scrollbar-width: thin; scrollbar-color: #1a1a2e #04040a;
    background: #04040a;
  }
  .log-line  { font-size: .65rem; color: #5a8ab0; white-space: pre-wrap; word-break: break-all; line-height: 1.5; padding: 0 2px; }
  .log-empty { font-size: .65rem; color: #222; font-style: italic; padding: 4px 2px; }
  .log-error { color: #d9534f; font-style: normal; }

  /* Detection panel */
  .det-screen { display: flex; align-items: baseline; gap: 6px; }
  .det-screen-lbl { font-size: .63rem; color: #444; text-transform: uppercase; flex-shrink: 0; }
  .det-screen-val { font-size: .82rem; color: #444; font-weight: bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .det-active { color: #7eb8f7; }
  .det-score-row, .det-device-row { display: flex; align-items: center; gap: 5px; }
  .det-lbl { font-size: .63rem; color: #444; flex-shrink: 0; min-width: 36px; }
  .det-bar-wrap { flex: 1; height: 3px; background: #111120; border-radius: 2px; overflow: hidden; }
  .det-bar { height: 100%; border-radius: 2px; transition: width .15s, background .15s; }
  .det-val { font-size: .68rem; font-weight: bold; min-width: 3em; text-align: right; flex-shrink: 0; }
  .det-select { flex: 1; min-width: 0; }
  .btn-xs {
    background: #111122; color: #7eb8f7; border: 1px solid #1a1a3a; border-radius: 3px;
    padding: 2px 6px; font-family: inherit; font-size: .63rem; cursor: pointer; flex-shrink: 0;
  }
  .btn-xs:hover { background: #1a1a3a; }
  .btn-restart { white-space: nowrap; }
  .det-switching { font-size: 0.7rem; color: var(--c-muted); white-space: nowrap; }

  /* Candidates panel */
  .cand-body   { gap: 3px; }
  .cand-row    { display: flex; align-items: center; gap: 4px; }
  .cand-active { background: rgba(126,184,247,.04); border-radius: 3px; margin: 0 -4px; padding: 0 4px; }
  .cand-name        { font-size: .62rem; color: #333; min-width: 72px; max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cand-name-active { color: #7eb8f7; }
  .cand-bar-wrap { flex: 1; height: 2px; background: #111120; border-radius: 2px; overflow: hidden; }
  .cand-bar   { height: 100%; border-radius: 2px; transition: width .15s, background .15s; }
  .cand-score { font-size: .62rem; min-width: 3em; text-align: right; flex-shrink: 0; }

  /* Selection panel */
  .sel-row   { display: flex; align-items: center; gap: 5px; }
  .sel-lbl   { font-size: .62rem; color: #444; min-width: 52px; flex-shrink: 0; }
  .sel-right { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .sel-val   { font-size: .68rem; color: #7eb8f7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sel-bar-wrap { height: 2px; background: #111120; border-radius: 2px; overflow: hidden; }
  .sel-bar { height: 100%; border-radius: 2px; transition: width .15s, background .15s; }

  /* Race panel */
  .hud-body     { gap: 4px; }
  .hud-row      { display: flex; align-items: center; gap: 6px; }
  .hud-lbl      { font-size: .63rem; color: #444; min-width: 40px; }
  .hud-val      { font-size: .82rem; color: #7eb8f7; font-weight: bold; }
  .hud-divider  { border-top: 1px solid #111; margin: 3px 0; }
  .split-lbl    { color: #333; }
  .split-val    { font-size: .75rem; color: #5a8ab0; font-weight: normal; font-variant-numeric: tabular-nums; }
  .split-pending { color: #222 !important; }
  .hud-total-row { margin-top: 2px; }
  .hud-total    { font-size: .82rem; color: #a8d8a8; font-weight: bold; font-variant-numeric: tabular-nums; }
  .mush-val { font-size: .75rem; }

  /* ── Startup view ─────────────────────────────────────────────── */
  .startup-view {
    flex: 1; display: flex; flex-direction: row; min-height: 0; overflow: hidden;
  }
  .startup-main {
    flex: 1; display: flex; flex-direction: column; min-height: 0;
    padding: 2rem 2.5rem 1.5rem; justify-content: center;
  }
  .startup-status {
    display: flex; align-items: flex-start; gap: .6rem;
  }
  .startup-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 3px; }
  .startup-status-text { font-size: .85rem; color: #888; }
  .startup-status-sub  { font-size: .72rem; color: #555; margin-top: .2rem; }
  .startup-note { font-size: .7rem; color: #3a3a52; line-height: 1.6; max-width: 340px; padding-left: calc(8px + .6rem); }

  /* ── Setup view (first-time wizard, full-screen) ───────────────── */
  .setup-view {
    flex: 1; display: flex; flex-direction: row; min-height: 0; overflow: hidden;
  }
  .setup-wiz {
    flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden;
    border-right: 1px solid #111120;
  }
  /* Centre content vertically and horizontally; no scrollbar — content must fit */
  .setup-wiz-body {
    flex: 1; min-height: 0; padding: 1.25rem 1.5rem;
    display: flex; align-items: center; justify-content: center;
    overflow: hidden; scrollbar-gutter: stable both-edges;
  }
  /* Constrain camera step width — aspect-ratio on preview-wrapper does the rest */
  .setup-wiz-body .cam-setup { width: 100%; max-width: 560px; }
  /* Cover-crop in setup preview — source may not be 16:9, bars aren't useful here */
  .setup-wiz-body .preview-video { object-fit: cover; }
  /* Step indicator tabs are display-only in setup view — not keyboard or mouse navigable */
  .setup-wiz-tabs { pointer-events: none; }

  /* Log sidebar shared by setup and startup views */
  .setup-log-side {
    width: 300px; flex-shrink: 0; display: flex; flex-direction: column;
    border-left: 1px solid #111120; background: #04040a;
  }
  .setup-log-hdr {
    display: flex; align-items: center; gap: .45rem;
    padding: 5px 8px; background: #04040a; border-bottom: 1px solid #0d0d1c;
    flex-shrink: 0;
  }
  .setup-log-status { font-size: .65rem; color: #555; line-height: 1.3; min-width: 0; }
  .setup-log-body {
    flex: 1; overflow-y: auto; padding: 4px 8px; min-height: 0;
    scrollbar-width: thin; scrollbar-color: #1a1a2e #04040a;
  }

  /* ── Modal backdrop ───────────────────────────────────────────── */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .wiz-backdrop { align-items: stretch; padding: 32px; }


  /* ── Wizard dialog ────────────────────────────────────────────── */
  .wiz-dialog {
    background: #06060e; border: 1px solid #1e1e2e; border-radius: 6px;
    display: flex; flex-direction: column; overflow: hidden;
    width: 100%; max-width: 960px; max-height: 100%; align-self: center; margin: auto;
    transition: max-width .2s ease;
  }
  .wiz-dialog-narrow { max-width: 480px; }
  .wiz-tabs {
    display: flex; flex-shrink: 0; background: #04040a;
    border-bottom: 1px solid #111120; overflow-x: auto; scrollbar-width: none;
  }
  .wiz-tab {
    background: transparent; color: #444; border: none;
    border-right: 1px solid #111120;
    padding: 7px 14px; font-family: inherit; font-size: .7rem;
    cursor: pointer; white-space: nowrap; transition: color .12s, background .12s;
  }
  .wiz-tab:hover { background: #0a0a16; color: #888; }
  .wiz-tab.active { background: #080818; color: #7eb8f7; border-bottom: 2px solid #7eb8f7; margin-bottom: -1px; }
  .wiz-tab-close {
    margin-left: auto; background: transparent; color: #444; border: none;
    padding: 7px 14px; font-family: inherit; font-size: .78rem; cursor: pointer;
    transition: color .12s;
  }
  .wiz-tab-close:hover { color: #888; }
  .wiz-body { flex: 1; overflow: auto; padding: 1rem; min-height: 0; }

  /* Wizard footer */
  .wiz-footer {
    display: flex; align-items: center; padding: 6px 12px;
    background: #04040a; border-top: 1px solid #111120; flex-shrink: 0; gap: 8px;
  }
  .dot-row   { flex: 1; display: flex; flex-wrap: wrap; gap: 3px; justify-content: center; align-items: center; }
  .nav-dot   { border-radius: 50%; background: #1e1e2e; transition: background .2s; }
  .nav-dot-sm  { width: 4px; height: 4px; }
  .nav-dot-lg  { width: 6px; height: 6px; }
  .nav-dot.active { background: #7eb8f7; }

  /* Step: centred */
  .step-centred { max-width: 560px; margin: 0 auto; padding: .5rem 0; display: flex; flex-direction: column; gap: .75rem; }
  .step-centred h2 { color: #7eb8f7; font-size: 1.05rem; }
  .step-centred p  { font-size: .78rem; color: #777; line-height: 1.65; }
  .done-check { font-size: 2.2rem; color: #4caf50; }
  .reset-defaults-section { margin-top: .5rem; padding-top: .75rem; border-top: 1px solid #1a1a2e; display: flex; flex-direction: column; align-items: center; gap: .45rem; }
  .btn-reset-defaults { background: none; border: 1px solid #3a2020; color: #7a4040; font-size: .7rem; padding: .3rem .75rem; border-radius: 4px; cursor: pointer; transition: color .12s, border-color .12s; }
  .btn-reset-defaults:hover { color: #ef4444; border-color: rgba(239,68,68,.4); }
  .reset-confirm-label { font-size: .72rem; color: #999; margin: 0; }
  .reset-confirm-row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; justify-content: center; }
  .btn-reset-confirm { background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.35); color: #ef4444; font-size: .72rem; padding: .3rem .75rem; border-radius: 4px; cursor: pointer; }
  .btn-reset-confirm:hover { background: rgba(239,68,68,.2); }

  /* Step: two-column */
  .step-two-col { display: flex; gap: 1rem; align-items: flex-start; }
  .preview-col  { flex: 3; min-width: 0; display: flex; flex-direction: column; gap: .3rem; }
  .info-col     { flex: 2; min-width: 180px; display: flex; flex-direction: column; gap: .65rem; }
  .info-col h3  { margin: 0; font-size: .88rem; color: #7eb8f7; }
  .item-header  { display: flex; align-items: baseline; gap: .5rem; }
  .item-num     { font-size: .65rem; color: #333; flex-shrink: 0; }

  /* Preview wrapper */
  .preview-wrapper {
    position: relative; width: 100%; aspect-ratio: 16/9;
    background: #000; border: 1px solid #111120; border-radius: 4px; overflow: hidden;
  }
  .preview-video { width: 100%; height: 100%; display: block; object-fit: contain; }
  .preview-canvas { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
  .roi-canvas { pointer-events: auto; }
  .preview-placeholder {
    width: 100%; height: 100%; position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: .35rem; font-size: .75rem; color: #333;
    padding: 0 .75rem; box-sizing: border-box; text-align: center;
  }
  .preview-icon { font-size: 1.4rem; line-height: 1; }
  .spin { animation: spin 1.2s linear infinite; }
  .preview-cap { font-size: .6rem; color: #2a2a3a; margin: 0; }

  /* Camera step */
  .cam-setup { display: flex; flex-direction: column; gap: .9rem; }
  .cam-dual  { display: flex; gap: .75rem; }
  .cam-pane  { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: .3rem; }
  .cam-pane-label { font-size: .63rem; color: #444; text-transform: uppercase; letter-spacing: .06em; }
  .cam-pane-status { display: flex; align-items: center; gap: .3rem; font-size: .65rem; color: #333; }
  .cam-pane-status .cam-dot { width: 6px; height: 6px; border-radius: 50%; background: #222; flex-shrink: 0; }
  .cam-status-ok  { color: #4caf50; } .cam-status-ok .cam-dot  { background: #4caf50; }
  .cam-status-err { color: #ef4444; } .cam-status-err .cam-dot { background: #ef4444; }
  .cam-status-warn { color: #888; }  .cam-status-warn .cam-dot { background: #888; }
  .cam-pane-err-label { font-size: .72rem; color: #555; }
  .cam-below   { display: flex; flex-direction: column; gap: .65rem; }
  .cam-actions { display: flex; flex-direction: column; gap: .3rem; }
  .cam-nav     { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
  .cam-prereq {
    padding: .55rem .7rem; border-radius: 4px;
    background: rgba(126,184,247,.07); border: 1px solid rgba(126,184,247,.25);
    display: flex; flex-direction: column; gap: .3rem;
  }
  .cam-prereq-title        { font-size: .72rem; color: #7eb8f7; font-weight: 600; }
  .cam-prereq-title-ok     { color: #4caf50; }
  .cam-prereq-ok           { background: rgba(76,175,80,.05); border-color: rgba(76,175,80,.2); }
  .cam-prereq-body    { font-size: .68rem; color: #666; margin: 0; line-height: 1.55; }
  .cam-prereq-actions { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-top: .15rem; }
  .cam-troubleshoot {
    padding: .55rem .7rem; border-radius: 4px;
    background: rgba(239,68,68,.05); border: 1px solid rgba(239,68,68,.2);
    display: flex; flex-direction: column; gap: .3rem;
  }
  .cam-troubleshoot-neutral { background: rgba(126,184,247,.04); border-color: rgba(126,184,247,.15); }
  .cam-troubleshoot-title   { font-size: .72rem; color: #c8c8e0; }
  .cam-troubleshoot-body    { font-size: .68rem; color: #666; margin: 0; line-height: 1.55; }
  .cam-troubleshoot-actions { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-top: .15rem; }
  .cam-err-detail { display: block; font-size: .65rem; color: #555; margin-top: .2rem; font-style: italic; }
  .cam-release-bar {
    display: flex; align-items: center; gap: .55rem;
    padding: .38rem .55rem; border-radius: 3px;
    background: rgba(251,191,36,.05); border: 1px solid rgba(251,191,36,.18);
    transition: background .25s, border-color .25s;
  }
  .cam-release-bar-released {
    background: rgba(76,175,80,.05); border-color: rgba(76,175,80,.2);
  }
  .cam-release-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: #8a6a10; transition: background .25s;
  }
  .cam-release-bar-released .cam-release-dot { background: #4caf50; }
  .cam-release-msg { flex: 1; font-size: .66rem; color: #6a6040; line-height: 1.45; transition: color .25s; }
  .cam-release-bar-released .cam-release-msg { color: #4a6a4a; }
  .cam-release-bar-error {
    background: rgba(239,68,68,.05); border-color: rgba(239,68,68,.2);
  }
  .cam-release-bar-error .cam-release-dot { background: #ef4444; }
  .cam-release-bar-error .cam-release-msg { color: #7a3a3a; }
  .cam-steps { margin: .15rem 0 .05rem; padding-left: 1.2rem; font-size: .68rem; color: #666; line-height: 1.8; }
  .cam-steps strong { color: #9ab; }

  /* ROI tabs */
  .roi-tabs { display: flex; gap: .25rem; flex-wrap: wrap; margin-bottom: .2rem; }
  .roi-tab {
    background: #0a0a18; border: 1px solid #1e1e3a; border-radius: 3px;
    color: #666; padding: .16rem .45rem; font-family: inherit; font-size: .65rem;
    cursor: pointer; transition: background .1s, color .1s;
  }
  .roi-tab.active    { background: #1a1a3a; color: #fff; border-color: #4a4a7a; }
  .roi-tab-and.active { color: #ffcc00; border-color: #7a7a30; }
  .roi-tab-or.active  { color: #00ccff; border-color: #307a7a; }
  .roi-tab:hover:not(.active) { background: #0f0f22; color: #aaa; }
  .roi-tab-add { opacity: .5; } .roi-tab-add:hover { opacity: 1; }
  .roi-tab-remove {
    background: transparent; border: none; color: #773333; padding: 0 .2rem;
    font-size: .8rem; cursor: pointer; line-height: 1; margin-left: -.2rem;
    transition: color .1s;
  }
  .roi-tab-remove:hover { color: #ff6666; }

  /* ROI chip */
  .roi-chip {
    background: #040410; border: 1px solid #111120; border-radius: 3px;
    padding: .22rem .45rem; font-size: .67rem; color: #4a7a9a;
    display: flex; align-items: center; flex-wrap: wrap; gap: .2rem;
  }
  .roi-size { color: #333; }
  .tell-group-note { color: #6a6a40; font-style: italic; font-size: .63rem; }

  /* Score box */
  .score-box {
    display: flex; align-items: center; gap: .4rem;
    padding: .35rem .5rem; border-radius: 4px; border: 1px solid #1e1e2e; font-size: .75rem;
  }
  .score-box.good { border-color: rgba(76,175,80,.4); background: rgba(76,175,80,.06); }
  .score-box.bad  { border-color: rgba(239,68,68,.4); background: rgba(239,68,68,.06); }
  .score-icon { font-size: .9rem; }
  .good .score-icon { color: #4caf50; } .bad .score-icon { color: #ef4444; }
  .score-val { font-size: .95rem; font-weight: bold; color: #e8e8f0; }
  .score-thr { color: #333; font-size: .67rem; }
  .score-lbl { color: #666; font-size: .67rem; margin-left: auto; }
  .score-msg { font-size: .68rem; color: #333; font-style: italic; }

  /* Threshold slider */
  .thresh-row   { display: flex; align-items: center; gap: .4rem; flex-shrink: 0; }
  .thresh-label { font-size: .62rem; color: #444; flex-shrink: 0; }
  .thresh-slider { flex: 1; min-width: 0; accent-color: #7eb8f7; cursor: pointer; height: 3px; }
  .thresh-val   { font-size: .62rem; color: #7eb8f7; min-width: 2.2em; text-align: right; flex-shrink: 0; }

  /* Button row */
  .btn-row { display: flex; gap: .5rem; flex-wrap: wrap; }
  .capture-note { font-size: .64rem; color: #333; }

  /* Template compare */
  .tmpl-compare { display: flex; gap: .5rem; margin-top: .2rem; }
  .tmpl-pane { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: .2rem; }
  .tmpl-pane-label { font-size: .6rem; color: #333; text-transform: uppercase; letter-spacing: .04em; }
  .tmpl-img { display: block; width: 100%; height: auto; border: 1px solid #111120; border-radius: 3px; background: #000; image-rendering: pixelated; }
  .tmpl-empty { height: 3rem; border: 1px dashed #111120; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-size: .65rem; color: #222; font-style: italic; }

  /* Buttons */
  .btn-primary {
    background: #0d2040; color: #7eb8f7; border: 1px solid #1a3a7a; border-radius: 4px;
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; white-space: nowrap; transition: background .12s;
  }
  .btn-primary:hover:not(:disabled) { background: #162a5a; }
  .btn-primary:disabled { opacity: .35; cursor: default; }
  .btn-primary.btn-lg { padding: .45rem 1.1rem; font-size: .85rem; margin-top: .5rem; }
  .btn-secondary {
    background: #06060e; color: #666; border: 1px solid #1a1a2e; border-radius: 4px;
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; white-space: nowrap; transition: background .12s;
  }
  .btn-secondary:hover:not(:disabled) { background: #0d0d1a; color: #bbb; }
  .btn-secondary:disabled { opacity: .4; cursor: default; }
  .btn-nav {
    background: #0a0a18; color: #7eb8f7; border: 1px solid #1a1a3a; border-radius: 4px;
    padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s;
  }
  .btn-nav:hover { background: #141428; }
  .btn-sm {
    background: #0a0a18; color: #7eb8f7; border: 1px solid #1a1a3a; border-radius: 3px;
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem;
    cursor: pointer; flex-shrink: 0;
  }
  .btn-sm:hover { background: #141428; }

  /* Forms / select */
  select {
    background: #040410; color: #e8e8f0;
    border: 1px solid #1a1a2e; border-radius: 3px;
    padding: .18rem .3rem; font-family: inherit; font-size: .7rem;
  }
  select:disabled {
    opacity: .35; cursor: not-allowed; pointer-events: none;
  }
  .device-row { display: flex; align-items: center; gap: .4rem; font-size: .72rem; flex-shrink: 0; }
  .device-row label { color: #555; flex-shrink: 0; }
  .select-loading { display: flex; align-items: center; gap: .3rem; color: #888; font-size: .72rem; font-style: italic; }
  .select-spin { color: #888; font-size: .85rem; }

  .hint { font-size: .7rem; color: #666; margin: 0; line-height: 1.55; }
  .lang-form { display: flex; flex-direction: column; gap: 1rem; width: 100%; max-width: 400px; margin: .5rem auto; }
  .lang-row  { display: flex; flex-direction: column; gap: .3rem; }
  .lang-row label { font-size: .72rem; color: #777; }
  .lang-hint { font-size: .64rem; }

  /* Asset category tabs */
  .asset-cat-tabs { display: flex; flex-wrap: wrap; gap: .25rem; margin-bottom: .2rem; }
  .asset-cat-tab {
    background: #06060e; color: #444; border: 1px solid #111120; border-radius: 3px;
    padding: .18rem .45rem; font-family: inherit; font-size: .65rem;
    cursor: pointer; transition: color .1s, background .1s;
  }
  .asset-cat-tab:hover  { background: #0a0a18; color: #888; }
  .asset-cat-tab.active { background: #0a0a18; color: #7eb8f7; border-color: #1e1e4a; }

  /* ── Language dialog ──────────────────────────────────────────── */
  .lang-dialog {
    background: #06060e; color: #e8e8f0;
    border: 1px solid #1e1e2e; border-radius: 6px;
    padding: 1.2rem; min-width: 360px; max-width: 440px;
    font-family: Consolas, 'Courier New', monospace;
  }
  .lang-dialog::backdrop { background: rgba(0,0,0,.65); }
  .ldlg-title  { font-size: .9rem; color: #7eb8f7; margin-bottom: 1rem; }
  .ldlg-form   { display: flex; flex-direction: column; gap: .8rem; }
  .ldlg-row    { display: flex; flex-direction: column; gap: .3rem; }
  .ldlg-label  { font-size: .7rem; color: #888; }
  .ldlg-hint   { font-size: .64rem; color: #555; margin-top: 2px; }
  .ldlg-actions { display: flex; justify-content: flex-end; gap: .5rem; margin-top: 1rem; }

  /* ── First-time modal ─────────────────────────────────────────── */

  /* ── Calibration step ─────────────────────────────────────────── */
  .calib-step .info-col           { gap: .8rem; }
  .calib-section                  { background: #06060e; border: 1px solid #1a1a2e; border-radius: 4px; padding: .7rem .8rem; }
  .calib-section-title            { font-size: .72rem; color: #7eb8f7; margin: 0 0 .5rem; font-weight: 600; }
  .calib-instruct                 { font-size: .68rem; color: #888; margin: 0 0 .55rem; line-height: 1.5; }
  .calib-instruct strong          { color: #c8d8ea; font-weight: 500; }
  .calib-pills                    { display: flex; flex-wrap: wrap; gap: .35rem; margin-bottom: .55rem; }
  .calib-pill                     { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .15rem; min-width: 3rem; padding: .45rem .55rem; background: #08081a; border: 1px solid #1a1a2e; border-radius: 4px; color: #888; cursor: pointer; font-family: inherit; transition: border-color .2s, background .2s, color .2s; }
  .calib-pill:hover:not(:disabled){ border-color: #2a3a5a; color: #c8d8ea; }
  .calib-pill:disabled            { cursor: not-allowed; opacity: .45; }
  .calib-pill-ready               { border-color: #1e4030; background: #0a1612; color: #c8d8ea; }
  .calib-pill-ready:hover:not(:disabled) { border-color: #2e6048; }
  .calib-pill-num                 { font-size: .85rem; font-weight: 600; color: #7eb8f7; line-height: 1; }
  .calib-pill-ready .calib-pill-num { color: #7adea2; }
  .calib-pill-state               { font-size: .7rem; opacity: .8; line-height: 1; }
  .calib-checkbox                 { display: flex; gap: .45rem; align-items: flex-start; font-size: .7rem; color: #c8d8ea; margin: .35rem 0 .6rem; cursor: pointer; line-height: 1.35; }
  .calib-checkbox input           { margin-top: 3px; accent-color: #7eb8f7; cursor: pointer; }
  .calib-checkbox small           { display: block; font-size: .62rem; color: #666; margin-top: 2px; }
  .calib-result                   { font-size: .72rem; padding: .45rem .6rem; border-radius: 3px; margin-top: .55rem; border: 1px solid; }
  .calib-result.good              { background: #0b1a14; border-color: #1e4030; color: #7adea2; }
  .calib-result.warn              { background: #1c1808; border-color: #4a3a10; color: #e0c060; }
  .calib-result.bad               { background: #1a0a0a; border-color: #4a1818; color: #e07878; }
  .calib-result-tag               { color: inherit; opacity: .75; margin-left: .25rem; }
  .calib-manual                   { background: #06060e; border: 1px solid #1a1a2e; border-radius: 4px; padding: .55rem .7rem; }
  .calib-manual summary           { cursor: pointer; font-size: .7rem; color: #888; outline: none; user-select: none; }
  .calib-manual[open] summary     { color: #7eb8f7; margin-bottom: .55rem; }
  .calib-sliders                  { display: flex; flex-direction: column; gap: .3rem; }
  .calib-sliders .slider-row      { display: grid; grid-template-columns: 4.5rem 1fr 2.3rem; align-items: center; gap: .5rem; font-size: .68rem; color: #aaa; }
  .calib-sliders .slider-row label { color: #888; }
  .calib-sliders .slider-row input[type=range] { accent-color: #7eb8f7; cursor: pointer; height: 3px; }
  .calib-sliders .slider-val      { text-align: right; color: #c8d8ea; font-variant-numeric: tabular-nums; }
</style>
