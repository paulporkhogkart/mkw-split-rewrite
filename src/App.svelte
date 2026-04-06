<script>
  import { onMount, onDestroy, afterUpdate } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
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
  let setupComplete = true;
  let wizardOpen = false;
  let wizardStep = "language";
  let screenIdx = 0, selectionIdx = 0, hudIdx = 0;

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
  const FIRST_TIME_STEPS = ["language", "done"];
  const RERUN_STEPS      = ["language", "camera", "screens", "selection", "hud", "templates", "done"];
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
    { id:"UNKNOWN",            x:5,   y:218, label:"UNKNOWN"    },
    { id:"TITLE",              x:5,   y:5,   label:"TITLE"      },
    { id:"HOME",               x:5,   y:55,  label:"HOME"       },
    { id:"GALLERY",            x:5,   y:105, label:"GALLERY"    },
    { id:"MAIN_MENU",          x:115, y:5,   label:"MAIN MENU"  },
    { id:"SINGLEPLAYER_MENU",  x:225, y:5,   label:"SP MENU"    },
    { id:"TIME_TRIALS",        x:225, y:38,  label:"SP [TT SEL]"},
    { id:"CHARACTER_SELECT",   x:335, y:5,   label:"CHAR SEL"   },
    { id:"KART_SELECT",        x:445, y:5,   label:"KART SEL"   },
    { id:"COURSE_SELECT",      x:550, y:5,   label:"COURSE SEL" },
    { id:"START_TIME_TRIAL",   x:760, y:50,  label:"START TT"   },
    { id:"START_REPLAY",       x:760, y:100, label:"START RPY"  },
    { id:"RACING",             x:655, y:125, label:"RACING"     },
    { id:"GHOST",              x:760, y:150, label:"GHOST"      },
    { id:"UNKNOWN_RACE_ACTIVE",x:655, y:172, label:"UNK RACE"   },
    { id:"RACE_MENU",          x:550, y:125, label:"RACE MENU"  },
    { id:"REPLAY_MENU",        x:550, y:172, label:"REPLAY MENU"},
    { id:"RESET",              x:445, y:172, label:"RESET"      },
    { id:"GHOST_RESET",        x:445, y:218, label:"GHOST RST"  },
    { id:"UNKNOWN_RESET",      x:335, y:172, label:"UNK RESET"  },
    { id:"REPLAY_RACE_AGAINST",x:550, y:218, label:"REPLAY [RA]"},
    { id:"POST_TIME_TRIAL",    x:655, y:218, label:"POST TT"    },
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

  function handleMsg(msg) {
    switch (msg.type) {
      case "ready":
        trackerConnected = true;
        lastHeartbeatTs = Date.now();
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
        if (!msg.setup_complete) { setupComplete = false; openWizard(); }
        // Auto-start browser camera for the main feed
        if (cameraStatus === "idle")
          loadBrowserDevices().then(() => startCamera(selectedBrowserDeviceId || undefined));
        break;
      case "camera_status":
        pythonCameraStatus = msg.ok ? "ok" : "error";
        pythonCameraError  = msg.error ?? "";
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
            if (match && match.deviceId !== selectedBrowserDeviceId)
              selectedBrowserDeviceId = match.deviceId;
          }
          if (wizardStep === "camera" && cameraStatus === "idle")
            startCamera(selectedBrowserDeviceId || undefined);
        }
        break;
      case "frame_data":
        engineFrame = `data:image/jpeg;base64,${msg.data}`;
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
        if (msg.screen === currentScreenName && (msg.roi_key ?? "primary") === activeRoiKey) {
          templateImg = msg.template_img ? `data:image/png;base64,${msg.template_img}` : null;
          liveCropImg = msg.live_crop    ? `data:image/png;base64,${msg.live_crop}`    : null;
        }
        break;
      case "template_score":
        if ((msg.roi_key ?? "primary") === activeRoiKey) {
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
  async function loadBrowserDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      browserDevices = all.filter(d => d.kind === "videoinput");
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
      // Find the audio input that shares a groupId with the selected video device.
      // This ensures we grab the capture card's own audio, not a random microphone.
      let ac = false;
      if (deviceId) {
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          const vid = all.find(d => d.kind === "videoinput" && d.deviceId === deviceId);
          const aud = vid && all.find(d => d.kind === "audioinput" && d.groupId === vid.groupId);
          if (aud) ac = { deviceId:{ exact: aud.deviceId } };
        } catch { /* fall through to generic audio */ }
      }
      // Raw audio constraints — disable all browser processing so we get the
      // clean capture card signal without noise suppression / echo cancellation.
      const rawAudio = {
        ...(ac || {}),
        echoCancellation:  false,
        noiseSuppression:  false,
        autoGainControl:   false,
      };
      try {
        videoStream = await navigator.mediaDevices.getUserMedia({ video:vc, audio: rawAudio });
      } catch {
        videoStream = await navigator.mediaDevices.getUserMedia({ video:vc });
      }
      cameraStatus = "ok";
      await loadBrowserDevices();
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
    if (!rect.width || !rect.height) return null;
    const pyw = pythonFrameW||1920, pyh = pythonFrameH||1080;
    const eAR = rect.width/rect.height, vAR = pyw/pyh;
    let rendW, rendH, ox, oy;
    if (vAR > eAR) { rendW=rect.width; rendH=rect.width/vAR; ox=0; oy=(rect.height-rendH)/2; }
    else            { rendH=rect.height; rendW=rect.height*vAR; ox=(rect.width-rendW)/2; oy=0; }
    return { ox, oy, sx:rendW/pyw, sy:rendH/pyh, rect };
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
    const cx=clientX-t.rect.left, cy=clientY-t.rect.top;
    for (const h of getHandlePositions(roi)) {
      if (Math.hypot(cx-(t.ox+h.fx*t.sx), cy-(t.oy+h.fy*t.sy)) <= HANDLE_HIT_RADIUS)
        return { handle:h.id, cursor:h.cursor };
    }
    const cx1=t.ox+roi[0]*t.sx, cy1=t.oy+roi[1]*t.sy;
    const cx2=t.ox+roi[2]*t.sx, cy2=t.oy+roi[3]*t.sy;
    if (cx>=cx1&&cx<=cx2&&cy>=cy1&&cy<=cy2) return { handle:"move", cursor:"move" };
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
      const t=getTransform();
      dragging=true; dragHandle=hit.handle; dragStartRoi=[...roi];
      dragStartMouse={x:(e.clientX-t.rect.left-t.ox)/t.sx, y:(e.clientY-t.rect.top-t.oy)/t.sy};
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
    const roi=getCurrentRoi();
    if (!dragging) {
      const hit=roi?hitTest(e.clientX,e.clientY,roi):null;
      const nh=hit?.handle??null;
      if (nh!==hoveredHandle) { hoveredHandle=nh; drawRoi(); }
      if (canvasEl) canvasEl.style.cursor=hit?.cursor??"default";
      return;
    }
    const t=getTransform(); if (!t) return;
    const dx=(e.clientX-t.rect.left-t.ox)/t.sx-dragStartMouse.x;
    const dy=(e.clientY-t.rect.top-t.oy)/t.sy-dragStartMouse.y;
    updateCurrentRoi(applyDrag(dragStartRoi,dragHandle,dx,dy)); drawRoi();
  }

  function onWindowMouseUp() {
    if (!dragging) return;
    dragging=false;
    const roi=getCurrentRoi(); if (roi) saveCurrentRoi(roi);
    dragHandle=null; dragStartRoi=null; dragStartMouse=null;
  }

  const ROI_COLORS={primary:"#ffffff",and:"#ffcc00",or:"#00ccff"};

  function _drawOneRoi(ctx,t,roi,color,showHandles) {
    if (!roi||roi.length<4) return;
    const cx1=t.ox+roi[0]*t.sx, cy1=t.oy+roi[1]*t.sy;
    const cw=(roi[2]-roi[0])*t.sx, ch=(roi[3]-roi[1])*t.sy;
    ctx.strokeStyle="rgba(0,0,0,0.7)"; ctx.lineWidth=4; ctx.setLineDash([]);
    ctx.strokeRect(cx1,cy1,cw,ch);
    ctx.strokeStyle=color; ctx.lineWidth=2; ctx.setLineDash([7,4]);
    ctx.strokeRect(cx1,cy1,cw,ch); ctx.setLineDash([]);
    if (showHandles) {
      for (const h of getHandlePositions(roi)) {
        const hcx=t.ox+h.fx*t.sx, hcy=t.oy+h.fy*t.sy, r=5;
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
    canvasEl.width=t.rect.width; canvasEl.height=t.rect.height;
    const ctx=canvasEl.getContext("2d");
    ctx.clearRect(0,0,canvasEl.width,canvasEl.height);
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

  async function retryNow() {
    stopCamera(); pythonCameraStatus="opening"; engineFrame=null;
    send({type:"open_camera"});
  }

  // ── ROI preview poll ──────────────────────────────────────────────────────────
  function startRoiPoll() {
    if (_roiPollTimer) return;
    _roiPollTimer=setInterval(()=>{
      if (!trackerConnected||!wizardOpen) return;
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
    wizardOpen=false;
  }
  function completeSetup() {
    send({type:"mark_setup_complete"}); setupComplete=true; closeWizard();
  }
  function goStep(step) {
    wizardStep=step; screenIdx=0; selectionIdx=0; hudIdx=0;
    templateCategory="characters"; templateItemIdx=0;
    currentScore=null; templateImg=null; liveCropImg=null;
    liveRoiCrop=null; assetTemplateImg=null; assetLiveCrop=null;
    hoveredHandle=null; activeRoiKey="primary"; syncThreshToScreen();
    if (step==="camera") {
      // Ask Python to open its camera if not already open
      if (pythonCameraStatus!=="ok") {
        pythonCameraStatus="idle"; engineFrame=null; send({type:"open_camera"});
      }
      // Browser camera is already running from auto-start; only start if it somehow stopped
      if (cameraStatus==="idle") startCamera(selectedBrowserDeviceId||undefined);
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
    trackerConnected = false;
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
    selectedBrowserDeviceId=e.target.value;
    const chosen=browserDevices.find(d=>d.deviceId===selectedBrowserDeviceId);
    if (chosen&&devices.length>0) {
      const cleanLabel=chosen.label.replace(/\s*\([0-9a-f:]+\)\s*$/i,"").trim();
      const match=devices.find(d=>d.toLowerCase()===cleanLabel.toLowerCase()||
        d.toLowerCase().includes(cleanLabel.toLowerCase())||cleanLabel.toLowerCase().includes(d.toLowerCase()));
      const pythonDevice=match??cleanLabel;
      if (pythonDevice!==configuredDevice) {
        configuredDevice=pythonDevice;
        send({type:"update_config",key:"camera_device",value:pythonDevice});
      }
    }
    if (!setupComplete&&wizardStep==="camera") {
      stopCamera(); pythonCameraStatus="idle"; engineFrame=null; send({type:"open_camera"});
    } else {
      await startCamera(selectedBrowserDeviceId);
    }
  }
  async function restartTracker() {
    restartNeeded=false; devices=[]; trackerConnected=false; await invoke("restart_tracker");
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
  onMount(async () => {
    appWindow=getCurrentWindow();
    version=await getVersion();
    await invoke("start_tracker");
    unlisten=await listen("tracker-event", ev=>{
      try { handleMsg(JSON.parse(ev.payload)); }
      catch { pushLog(String(ev.payload)); }
    });
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

  $: if (mainVideoEl) mainVideoEl.srcObject=videoStream??null;
  $: if (wizVideoEl)  wizVideoEl.srcObject =videoStream??null;
  afterUpdate(()=>{ if (wizardOpen) drawRoi(); });

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

  $: if (wizardOpen&&["screens","selection","hud","templates"].includes(wizardStep)) {
    startRoiPoll();
  } else { stopRoiPoll(); }

  $: _=appLanguage;
  function tr(key) { return t(key,appLanguage); }

  function syncThreshToScreen() {
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
      {:else}
        <span class="hb-idle">connecting…</span>
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
      {#if wizardOpen && setupComplete}
        <button class="btn-hdr btn-close-wiz" on:click={closeWizard}>✕ Close Setup</button>
      {:else if !wizardOpen}
        <button class="btn-hdr btn-setup" on:click={openWizard}>⚙ Setup</button>
      {/if}
    </div>

    <div class="win-controls">
      <button class="win-btn" on:click={winMinimize} title="Minimize">&#x2013;</button>
      <button class="win-btn" on:click={winToggleMaximize} title="Maximize">&#x25a1;</button>
      <button class="win-btn win-btn-close" on:click={winClose} title="Close">&#x2715;</button>
    </div>
  </header>

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
              <div class="log-empty">Waiting for events…</div>
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
          <svg viewBox="0 0 860 248" class="graph-svg" xmlns="http://www.w3.org/2000/svg">
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
              <g transform="translate({node.x},{node.y})">
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
</div><!-- /app -->

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  FIRST-TIME SETUP MODAL (simplified: language only)                        -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

{#if !setupComplete && !wizardOpen}
  <div class="modal-backdrop">
    <div class="ftmodal">
      <div class="ftm-icon">🏁</div>
      <h2 class="ftm-title">Welcome to MKW Tracker</h2>
      <p class="ftm-desc">Choose your languages before we begin. You can change these anytime via Setup.</p>

      <div class="ftm-form">
        <div class="ftm-row">
          <label class="ftm-label" for="ft-app-lang">Application Language</label>
          <select id="ft-app-lang" bind:value={appLanguage} on:change={onAppLanguageChange}>
            {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
          </select>
        </div>
        <div class="ftm-row">
          <label class="ftm-label" for="ft-sw2-lang">Switch 2 System Language</label>
          <select id="ft-sw2-lang" bind:value={switch2Language} on:change={onSwitch2LanguageChange}>
            {#each LANGUAGES as l}<option value={l.id}>{l.name}</option>{/each}
          </select>
          <p class="ftm-hint">Determines which image templates are used for detection.</p>
        </div>
      </div>

      <button class="btn-primary btn-lg ftm-continue" on:click={() => { send({type:"mark_setup_complete"}); setupComplete=true; }}>
        Continue →
      </button>
    </div>
  </div>
{/if}

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  WIZARD DIALOG (re-run setup: camera, screens, selection, hud, templates)  -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

{#if wizardOpen}
  <div class="modal-backdrop wiz-backdrop" on:click|self={setupComplete ? closeWizard : undefined}>
    <div class="wiz-dialog">

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
            {#if setupComplete}
              <button class="btn-primary btn-lg" on:click={()=>goStep("camera")}>{tr("lang.continue")}</button>
            {:else}
              <button class="btn-primary btn-lg" on:click={()=>goStep("done")}>{tr("lang.continue")}</button>
            {/if}
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
                  {:else}
                    <div class="preview-placeholder"><span class="spin">◌</span><span>Waiting…</span></div>
                  {/if}
                </div>
                <div class="cam-pane-status" class:cam-status-ok={cameraOk} class:cam-status-err={cameraStatus==="busy"||cameraStatus==="error"}>
                  <span class="cam-dot"></span>
                  {cameraOk?"Connected":cameraStatus==="requesting"?"Opening…":cameraStatus==="busy"?"Blocked":cameraStatus==="error"?"Error":"Waiting"}
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
                  <select id="wiz-cam" on:change={handleCameraDeviceChange}>
                    {#each browserDevices as d}
                      <option value={d.deviceId} selected={d.deviceId===selectedBrowserDeviceId}>
                        {d.label||`Camera ${d.deviceId.slice(0,6)}…`}
                      </option>
                    {/each}
                  </select>
                  {#if restartNeeded}<button class="btn-sm" on:click={restartTracker}>Restart</button>{/if}
                </div>
              {/if}

              {#if pythonCameraOk && cameraStatus === "busy"}
                <div class="cam-troubleshoot">
                  <span class="cam-troubleshoot-title">Your capture card is blocking simultaneous access</span>
                  <p class="cam-troubleshoot-body">The engine feed confirms the device works. Windows is preventing the app from opening it at the same time. One-time fix:</p>
                  <ol class="cam-steps">
                    <li>Click <strong>Release engine &amp; open settings →</strong> below</li>
                    <li>Find your capture card → <strong>Advanced camera options</strong> → <strong>Edit</strong></li>
                    <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                    <li>Return here and click <strong>Retry</strong></li>
                  </ol>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={releaseAndOpenSettings}>Release engine &amp; open settings →</button>
                  </div>
                </div>
              {:else if trackerCameraPaused}
                <div class="cam-troubleshoot cam-troubleshoot-neutral">
                  <span class="cam-troubleshoot-title">Engine camera released</span>
                  <p class="cam-troubleshoot-body">Change the Windows setting if you haven't yet, then click <strong>Retry</strong>.</p>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={retryNow}>Retry</button>
                  </div>
                </div>
              {:else if pythonCameraStatus === "error" || cameraStatus === "error"}
                <div class="cam-troubleshoot">
                  <span class="cam-troubleshoot-title">Can't access capture card</span>
                  <p class="cam-troubleshoot-body">Check that your capture card is connected and not in use by another app.{#if pythonCameraError} <span class="cam-err-detail">{pythonCameraError}</span>{/if}</p>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={retryNow}>Retry</button>
                  </div>
                </div>
              {/if}

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>← Back</button>
                  <button class="btn-primary" disabled={!bothCamerasOk} on:click={()=>goStep("screens")}>
                    Next: Screen Detection →
                  </button>
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
          </div>
        {/if}

      </div><!-- /wiz-body -->

      <!-- Wizard footer navigation (for calibration steps) -->
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
  .btn-close-wiz   { color: #666; border: 1px solid #1e1e2e; }
  .btn-close-wiz:hover { color: #bbb; background: #111120; }

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
  .graph-svg { width: 100%; min-width: 700px; height: 258px; display: block; }

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

  /* ── Modal backdrop ───────────────────────────────────────────── */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
  }
  .wiz-backdrop { align-items: stretch; padding: 32px; }

  /* ── First-time modal ─────────────────────────────────────────── */
  .ftmodal {
    background: #06060e; border: 1px solid #1e1e2e; border-radius: 8px;
    padding: 2rem; max-width: 460px; width: 100%; text-align: center;
    display: flex; flex-direction: column; align-items: center; gap: 1rem;
  }
  .ftm-icon  { font-size: 2.5rem; }
  .ftm-title { font-size: 1.1rem; color: #7eb8f7; }
  .ftm-desc  { font-size: .78rem; color: #777; line-height: 1.6; }
  .ftm-form  { width: 100%; display: flex; flex-direction: column; gap: .9rem; text-align: left; }
  .ftm-row   { display: flex; flex-direction: column; gap: .3rem; }
  .ftm-label { font-size: .72rem; color: #888; }
  .ftm-hint  { font-size: .65rem; color: #555; margin-top: 2px; }
  .ftm-continue { margin-top: .5rem; }

  /* ── Wizard dialog ────────────────────────────────────────────── */
  .wiz-dialog {
    background: #06060e; border: 1px solid #1e1e2e; border-radius: 6px;
    display: flex; flex-direction: column; overflow: hidden;
    width: 100%; max-width: 960px; max-height: 100%; align-self: center; margin: auto;
  }
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
  .cam-steps { margin: .2rem 0 .1rem; padding-left: 1.2rem; font-size: .68rem; color: #666; line-height: 1.8; }
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
  .device-row { display: flex; align-items: center; gap: .4rem; font-size: .72rem; flex-shrink: 0; }
  .device-row label { color: #555; flex-shrink: 0; }

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
</style>
