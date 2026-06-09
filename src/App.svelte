<script>
  import { onMount, onDestroy } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { attachLogger } from "@tauri-apps/plugin-log";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { t } from "./translations.js";
  import { C } from "./lib/palette.js";
  import { send } from "./lib/ipc.js";
  import { screenLabel } from "./lib/format.js";
  import TitleBar from "./components/TitleBar.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import Rail from "./components/Rail.svelte";
  import FeedOverlay from "./components/FeedOverlay.svelte";
  import EditMode from "./components/EditMode.svelte";
  import SourceCheck from "./components/SourceCheck.svelte";
  import DeviceSelectors from "./components/DeviceSelectors.svelte";
  import LanguageSelectors from "./components/LanguageSelectors.svelte";
  import SettingsModal from "./components/SettingsModal.svelte";
  import RunReviewModal from "./components/RunReviewModal.svelte";
  import PlayerPanel from "./components/PlayerPanel.svelte";
  import { screen as screenStore, liveScore as liveScoreStore,
           candidates as candidatesStore, selection as selectionStore,
           race as raceStore, logs as logsStore,
           tells as tellsStore, rois as roisStore,
           view as viewStore,
           minimap as minimapStore, sample as sampleStore } from "./lib/stores.js";
  import { pbSplits as pbSplitsStore, pbTotalMs as pbTotalStore, friendsPbs as friendsPbsStore,
           trailRuns as trailRunsStore, trailLegend as trailLegendStore } from "./lib/stores.js";
  import { get } from "svelte/store";
  import { trailSettings, roster as rosterStore, cacheRoster,
           activeConfig, buildTrailRuns, trailLegendRows } from "./lib/trailSettings.js";
  import { initDiscordPresence } from "./lib/discord.js";
  import { initPresence } from "./lib/presence.js";
  import { initSync } from "./lib/sync.js";

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
  let backendScreen = "-";
  let prevBackendScreen = null;
  let lastHeartbeatTs = 0;
  let liveScore = 0.0;
  let candidateScores = {};
  let selectionCandidates = { char: [], kart: [], course: [], costume: [] };
  let _fetchedThisRace = false;
  let _tick = 0;
  $: backendAlive = trackerConnected && _tick >= 0 && (Date.now() - lastHeartbeatTs) < 4000;
  $: statusDot = !trackerConnected ? C.idle : backendAlive ? C.ok : C.warn;
  // Lifecycle view (startup → setup → main). The monitor↔edit switch within "main"
  // lives in the `view` store ($viewStore: "monitor" | "edit").
  $: appView = setupComplete === null ? "startup"
          : setupComplete === false ? "setup"
          : "main";
  // Editing a screen = the main lifecycle view is active AND the edit view is open.
  $: editingNode = appView === "main" && $viewStore === "edit";
  // Force back to the monitor view whenever we leave the main lifecycle view.
  $: if (appView !== "main" && $viewStore === "edit") closeNodeEditor();

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

  // ── Run review queue ────────────────────────────────────────────────────────
  // Each entry: { attemptId, run, isPb }. The modal renders the head; submit/discard
  // dequeues. Fed live by run_needs_review and on launch by sync_list_pending.
  let reviewQueue = [];
  $: reviewHead = reviewQueue[0] ?? null;
  // Canonical dropdown options from the engine option_lists event.
  let optionLists = { courses: [], characters: [], karts: [], costumes: [], costumesByCharacter: {} };

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
  // Edit-mode graph node thumbnails: { SCREEN_NAME: dataURL }, fetched from the backend.
  let screenThumbs = {};
  let _thumbsLang  = null;   // Switch language the loaded thumbs were fetched for
  $: appLangName  = LANGUAGES.find(l => l.id === appLanguage)?.name     ?? appLanguage;
  $: sw2LangName  = LANGUAGES.find(l => l.id === switch2Language)?.name ?? switch2Language;

  // ── Wizard state ──────────────────────────────────────────────────────────────
  let setupComplete = null;  // null = unknown (waiting for ready), false = needs setup, true = done
  let wizardOpen = false;
  let wizardStep = "language";
  let resetConfirmPending = false;

  // ── Edit Screens model ──────────────────────────────────────────────────────────
  let selectedNode = null;                      // Screen name currently open in the editor
  let sidebarOpen = true;                        // collapsible right-hand status sidebar
  let activeTab = "detection";                  // "detection" | "selection" | "hud" | "templates"
  let activeRegion = { group: 0, region: 0 };   // Detection tab: selected region
  let detResetPending = false;                  // confirm gate for "reset detection to defaults"
  let activeRoiName = null;                      // Selection/HUD tab: selected config-ROI key
  let roiResetPending = false;                   // confirm gate for "reset this ROI"
  // (ROI canvas zoom/pan + drag now live inside RoiCanvas.svelte.)

  // Which extra tabs each node owns (beyond the always-present Detection tab).
  const TAB_LABELS = { detection:"Detection", selection:"Selection", hud:"HUD", templates:"Templates" };
  const NODE_SELECTION = { CHARACTER_SELECT:["char_name","costume"], KART_SELECT:["kart_name"], COURSE_SELECT:["course_name"] };
  const NODE_HUD       = { RACING:["lap_current","lap_total","coin_left","coin_right","mushroom"] };  /* "finish" disabled */
  // Each selection/HUD ROI that has a per-item template library to capture.
  const ROI_TEMPLATE_CAT = { char_name:"characters", costume:"costumes", kart_name:"karts", course_name:"courses", mushroom:"mushrooms" };
  function tabsForNode(n) {
    const t = ["detection"];
    if (NODE_SELECTION[n]) t.push("selection");
    if (NODE_HUD[n])       t.push("hud");
    return t;
  }
  function openNode(screenName) {
    selectedNode = screenName;
    activeTab = "detection";
    activeRegion = { group: 0, region: 0 };
    detResetPending = false;
    editCanvas?.resetCanvas();
    send({ type: "list_tells" });
    send({ type: "list_rois" });
  }
  function closeNodeEditor() { selectedNode = null; stopRoiPoll(); }
  function openSettings() { openWizard(); }   // modal wizard, now limited to Language + Camera

  // Toggle between the monitor and edit views. Entering edit defaults the selected
  // screen to the live backend screen when it's editable; exiting clears the editor.
  let editCanvas = null;   // bound EditMode instance (fit graph / reset canvas on enter)
  function toggleView() {
    if ($viewStore === "edit") {
      viewStore.set("monitor");
      closeNodeEditor();
    } else {
      // Default to the current live screen if it's a known editable node; otherwise
      // keep whatever was last open (may be null → graph shows with no selection).
      if (!selectedNode && SCREEN_NAMES.includes(backendScreen)) {
        openNode(backendScreen);
      } else {
        send({ type: "list_tells" });
        send({ type: "list_rois" });
      }
      viewStore.set("edit");
    }
  }

  // ── Edit-view graph ───────────────────────────────────────────────────────────
  // Pan/zoom + node-click handling now live in the ScreenGraph component
  // (src/components/ScreenGraph.svelte), driven by src/lib/graph.js.

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
                 color: active ? C.accent : (sameGrp ? C.roiCtx : C.warn) });
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

  // ── Selection / HUD tab helpers ─────────────────────────────────────────────────
  function roiMeta(key) {
    return SELECTION_ROIS.find(r=>r.key===key) || HUD_ROIS.find(r=>r.key===key) || { label:key, hint:"" };
  }
  function setTab(tab) {
    activeTab = tab; roiResetPending = false; detResetPending = false;
    if (tab === "selection") { const k = NODE_SELECTION[selectedNode]?.[0]; k ? selectRoiName(k) : (activeRoiName = null); }
    else if (tab === "hud")  { const k = NODE_HUD[selectedNode]?.[0];       k ? selectRoiName(k) : (activeRoiName = null); }
  }
  function catLabel(c) { return ASSET_CATEGORIES.find(x=>x.key===c)?.label ?? c; }
  function selectTplItem(i) {
    templateItemIdx = i; assetTemplateImg = null; assetLiveCrop = null;
    const item = ASSET_ITEMS[templateCategory]?.[i];
    if (item) send({ type:"get_asset_template", category:templateCategory, item_name:item.file });
  }
  function selectRoiName(k) {
    activeRoiName = k; roiResetPending = false;
    const cat = ROI_TEMPLATE_CAT[k];
    if (cat) { templateCategory = cat; templateItemIdx = 0; assetTemplateImg = null; assetLiveCrop = null; }
  }
  function editTabRois() {
    const keys = activeTab==="selection" ? (NODE_SELECTION[selectedNode]||[]) : (NODE_HUD[selectedNode]||[]);
    return keys.map(k => ({ k, roi: rois[k], active: k===activeRoiName, color: k===activeRoiName ? C.accent : C.warn }));
  }
  function _activeRoiConfigKey() {
    return SELECTION_ROI_CONFIG_KEYS[activeRoiName] || HUD_ROI_CONFIG_KEYS[activeRoiName] || null;
  }
  function resetActiveRoi() {
    const ck = _activeRoiConfigKey();
    if (ck) send({ type:"reset_roi", key:ck });
    roiResetPending = false;
  }

  // ── RoiCanvas adapters ────────────────────────────────────────────────────────
  // Map the editor's ROI lists onto the RoiCanvas prop shape ({ box, role, ... }).
  // role: 'active' (selected), 'sibling' (same group, detection only), 'other'.
  $: canvasRois = !editingNode ? []
    : activeTab === "detection"
      ? editRois().map(re => ({
          box: re.roi,
          role: re.active ? "active" : (re.gi === activeRegion.group ? "sibling" : "other"),
          gi: re.gi, ri: re.ri,
        }))
      : editTabRois().map(re => ({
          box: re.roi,
          role: re.active ? "active" : "other",
          k: re.k,
        }));
  // The editable ROI that receives drag handles.
  $: activeEditBox = !editingNode ? null
    : activeTab === "detection" ? (activeRegionObj?.roi ?? null)
    : (activeRoiName ? (rois[activeRoiName] ?? null) : null);

  // RoiCanvas committed a drag/resize → persist locally (so the box stays) + IPC.
  function onCanvasChange(box) {
    updateCurrentRoi(box);
    saveCurrentRoi(box);
  }
  // RoiCanvas click on an inactive box → switch the active region/ROI.
  function onCanvasSelect(re) {
    if (!re) return;
    if (activeTab === "detection") {
      if (re.gi != null && re.ri != null) selectRegion(re.gi, re.ri);
    } else if (re.k) {
      selectRoiName(re.k);
    }
  }

  // ── ToolsPanel bundles ────────────────────────────────────────────────────────
  // ToolsPanel exposes two tabs: "detection" | "readout". The editor's internal
  // activeTab is "detection" | "selection" | "hud" - map between them. A screen has
  // at most one readout tab (selection XOR hud), so "readout" resolves to whichever.
  $: readoutEnabled = !!(NODE_SELECTION[selectedNode] || NODE_HUD[selectedNode]);
  $: toolsActiveTab = activeTab === "detection" ? "detection" : "readout";
  function setToolsTab(tab) {
    if (tab === "detection") setTab("detection");
    else setTab(NODE_SELECTION[selectedNode] ? "selection" : "hud");
  }

  // Detection bundle for ToolsPanel (DetectionTree + RegionInspector).
  $: detectionBundle = {
    tree: {
      groups:       editTell?.groups ?? [],
      active:       activeRegion,
      resetPending: detResetPending,
      currentScore,
      screenName:   selectedNode ?? "",
    },
    inspector: {
      liveCrop:  liveCropImg,
      template:  templateImg,
      score:     currentScore,
      isCostume: false,
      capturing: capturingTemplate,
    },
  };

  // Readout bundle for ToolsPanel (ReadoutRoiEditor).
  $: readoutKeys = activeTab === "hud"
    ? (NODE_HUD[selectedNode] || [])
    : (NODE_SELECTION[selectedNode] || []);
  $: readoutMetas = Object.fromEntries(readoutKeys.map(k => [k, roiMeta(k)]));
  $: readoutCat   = activeRoiName ? (ROI_TEMPLATE_CAT[activeRoiName] ?? null) : null;
  $: readoutItems = readoutCat ? (ASSET_ITEMS[readoutCat] ?? []) : [];
  $: readoutActiveItem = readoutItems[templateItemIdx] ?? null;
  $: readoutBundle = {
    roiKeys:         readoutKeys,
    roiMetas:        readoutMetas,
    activeRoiName,
    tabKind:         activeTab === "hud" ? "hud" : "selection",
    templateCategory: readoutCat,
    categoryLabel:   readoutCat ? catLabel(readoutCat) : null,
    items:           readoutItems,
    activeItemIdx:   templateItemIdx,
    activeItemHint:  (readoutCat && readoutActiveItem) ? (ASSET_HINTS[readoutCat]?.(readoutActiveItem.name) ?? null) : null,
    assetTemplate:   assetTemplateImg,
    assetLiveCrop,
    capturing:       capturingTemplate,
    resetPending:    roiResetPending,
  };

  // ToolsPanel "capture" maps to the Detection recapture or the asset capture.
  function onToolsCapture() {
    if (activeTab === "detection") recaptureRegion();
    else captureAsset();
  }
  // (The region match score is now driven live by startRoiPoll - no manual test.)

  let tells = [];
  let rois = {};
  let currentScore = null;
  let capturingTemplate = false;
  let templateImg = null;
  let liveCropImg = null;

  // ── ROI editing state ─────────────────────────────────────────────────────────
  let liveRoiCrop = null;
  let assetTemplateImg = null, assetLiveCrop = null;
  let templateCategory = "characters", templateItemIdx = 0;
  let _roiPollTimer = null;
  let currentBinaryThresh = 170;

  // ── Camera ────────────────────────────────────────────────────────────────────
  let wizVideoEl = null, videoStream = null;
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
  // First-run finishes straight into the live app from the camera step - no
  // separate "done" confirmation screen (the running app is its own confirmation).
  const FIRST_TIME_STEPS = ["language", "camera"];
  // Post-setup, the ⚙ modal is a slim Settings panel: Language + Camera only.
  // Screen/tell/HUD/template editing now lives in the Edit Screens view.
  const RERUN_STEPS      = ["language", "camera", "discord", "sync", "trails"];
  const STEP_LABELS = {
    language: "Language", camera: "Video", discord: "Discord", sync: "Sync", trails: "Trails", screens: "Screens",
    selection: "Selection", hud: "HUD", templates: "Templates",
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
      "standard_kart","stellar_sled","tune_thumper","w_twin_chopper","zoom_buggy",
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
      "aero","all_terrain","aristocrat","aurora","aviator","biker","biker_jr",
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
    TIME_TRIALS:"Time trials mode menu - character and course selection.",
    CHARACTER_SELECT:"The character/driver selection screen.",
    KART_SELECT:"The kart body, tires, and glider selection screen.",
    COURSE_SELECT:"The track/course selection grid.",
    START_TIME_TRIAL:"The 3-2-1 countdown before a time trial race begins.",
    START_REPLAY:"The 3-2-1 countdown before a ghost race begins.",
    RACING:"Active racing - coin counter and flag icon visible bottom-left. Covers all race types.",
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
    { key:"lap_current", label:"Lap Counter (current)", hint:"Current lap digit - bottom-left race HUD." },
    { key:"lap_total",   label:"Lap Counter (total)",   hint:"Total laps digit next to current lap." },
    { key:"coin_left",   label:"Coin Digit (tens)",     hint:"Left/tens coin counter digit." },
    { key:"coin_right",  label:"Coin Digit (units)",    hint:"Right/units coin counter digit." },
    // { key:"finish",   label:"Finish Position",       hint:"1st / 2nd / 3rd finish overlay, top-right area." },  // finish disabled

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

  // The screen-graph layout, edges, and pan/zoom now live in src/lib/graph.js and
  // are rendered by src/components/ScreenGraph.svelte (used inside EditMode).

  // ── Helpers ───────────────────────────────────────────────────────────────────
  // send() lives in lib/ipc.js

  function pushLog(line) {
    logs = [...logs.slice(-299), line];
    logsStore.update(a => { const n = [...a, line]; return n.length > 500 ? n.slice(-500) : n; });
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

  // Pull a course's reads (PB splits + own/friends trails + friends PBs) from the Rust
  // read-back/cache and fan them into the stores. Trails are selected + coloured + faded
  // per the user's Trails settings (per player); pb_splits feeds the Discord delta;
  // friends_pbs is data-only.
  async function loadCourseReads(course) {
    try {
      const settings = get(trailSettings);
      const rosterList = get(rosterStore);
      const r = JSON.parse(await invoke("sync_course_reads", { course, config: activeConfig(settings, rosterList) }));
      pbSplitsStore.set(r.pb_splits?.splits ?? null);
      pbTotalStore.set(r.pb_splits?.total_ms ?? null);
      trailRunsStore.set(buildTrailRuns(r, settings, rosterList));
      trailLegendStore.set(trailLegendRows(settings, rosterList));
      friendsPbsStore.set(r.friends_pbs ?? []);
    } catch (_) { /* offline / unconfigured: leave stores as-is */ }
  }

  function handleMsg(msg) {
    switch (msg.type) {
      case "stderr":
        pushLog(`[err] ${msg.line}`);
        break;
      case "spawned":
        trackerSpawned = true;
        pushLog(`[app] engine process launched ${_elapsed()} - waiting for Python to initialise (Windows may be scanning files)`);
        break;
      case "ready":
        trackerSpawned = true;
        trackerConnected = true;
        lastHeartbeatTs = Date.now();
        pushLog(`[app] tracker connected ${_elapsed()} - setup_complete=${msg.setup_complete}`);
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
              // Python opened a different device - force the browser to match it
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
        // Discard frames that arrive while Python is mid-switch - they belong to the old camera.
        if (pythonCameraStatus !== "opening") engineFrame = `data:image/jpeg;base64,${msg.data}`;
        break;
      case "heartbeat":
        backendFps      = msg.fps    ?? 0;
        backendScreen   = msg.screen ?? "-";
        lastHeartbeatTs = Date.now();
        liveScore       = msg.current_score ?? 0;
        candidateScores = msg.candidate_scores ?? {};
        selectionCandidates = msg.selection_candidates ?? { char: [], kart: [], course: [], costume: [] };
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
        // cluster - leaving them back to HOME should not overwrite the game-state context.
        const _homeCluster = new Set(["UNKNOWN","HOME","GALLERY"]);
        if (msg.from && !_homeCluster.has(msg.from)) {
          prevBackendScreen = msg.from;
        }
        backendScreen = msg.to ?? backendScreen;
        // Fetch replay trail + minimap sample once each time we enter RACING (so a
        // second race on the same course refreshes stale data).  Reset the guard
        // whenever we leave RACING so the next entry re-fetches.
        if (msg.to === "RACING") {
          // PB splits + own/friends trails + friends PBs now come from the server via
          // the Rust read-back/cache (Phase 2), not the engine's local race store.
          if (selCourse) loadCourseReads(selCourse);
          if (!_fetchedThisRace && selCourse) {
            _fetchedThisRace = true;
            send({ type: "get_minimap_sample", course: selCourse });   // seed-derived, still engine
          }
        } else if (msg.from === "RACING") {
          _fetchedThisRace = false;
        }
        break;
      case "selection_update":
        selChar    = msg.character ?? null; selCharConf    = msg.char_conf    ?? 0;
        selCostume = msg.costume   ?? null; selCostumeConf = msg.costume_conf ?? 0;
        selKart    = msg.kart      ?? null; selKartConf    = msg.kart_conf    ?? 0;
        selCourse  = msg.course    ?? null; selCourseConf  = msg.course_conf  ?? 0;
        pushLog(`[sel] ${msg.character ?? "-"} / ${msg.kart ?? "-"} / ${msg.course ?? "-"}${msg.costume ? ` / ${msg.costume}` : ""}`);
        break;
      case "lap_update":
        // Reset splits when lap 1 starts - marks the beginning of a fresh race
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
        pushLog(`[finish] ${msg.result}  ${msg.total_time ?? "-"}`);
        break;
      case "pb_achieved":
        pushLog(`[pb] ${msg.course}  ${msg.time}`);
        break;
      case "run_needs_review":
        pushLog(`[review] ${msg.run?.course ?? "?"} ${msg.run?.status ?? ""} - missing: ${(msg.missing ?? []).join(", ") || "none"}`);
        // Replace any existing entry for this attempt (idempotent), else append.
        reviewQueue = [
          ...reviewQueue.filter((e) => e.attemptId !== msg.attempt_id),
          { attemptId: msg.attempt_id, run: msg.run, isPb: !!msg.is_pb, live: true },
        ];
        break;
      case "option_lists":
        optionLists = {
          courses:    msg.courses    ?? [],
          characters: msg.characters ?? [],
          karts:      msg.karts      ?? [],
          costumes:   msg.costumes   ?? [],
          costumesByCharacter: msg.costumes_by_character ?? {},
        };
        break;
      case "error":  pushLog(`[ERR] ${msg.message}`); break;
      case "minimap_update":
        minimapStore.set({ cx: msg.cx, cy: msg.cy, radius: msg.radius,
                           trackState: msg.track_state, roi: msg.roi ?? null });
        break;
      case "minimap_sample":
        sampleStore.set(msg.png_b64 ?? null);
        break;
      case "screen_thumbs": {
        const _t = msg.thumbs ?? {};
        screenThumbs = Object.fromEntries(
          Object.entries(_t).map(([k, v]) => [k, `data:image/png;base64,${v}`])
        );
        break;
      }
    }
  }

  // ── Run review actions ──────────────────────────────────────────────────────
  function _dequeue(attemptId) {
    reviewQueue = reviewQueue.filter((e) => e.attemptId !== attemptId);
  }
  // Live PB lookup for the review popup: the cached best ms for a course (or null),
  // from the Rust pb_cache. Lets the popup recognise a PB once the course is picked.
  async function pbBestLookup(course) {
    if (!course) return null;
    try { return await invoke("sync_pb_best", { course }); }
    catch { return null; }
  }
  function onReviewSubmit(e) {
    const { attempt_id, ...filled } = e.detail;   // attempt_id travels separately
    const entry = reviewQueue.find((x) => x.attemptId === attempt_id);
    // The resolve returns a pb_achieved event when the now-complete run is a PB (e.g.
    // the course was only entered here); route it like any tracker event so a reviewed
    // PB notifies just like an auto-detected one.
    invoke("sync_resolve_pending", { attemptId: attempt_id, filled })
      .then((ev) => { if (ev) handleMsg(JSON.parse(ev)); })
      .catch(() => {});
    // For a just-finished run (not a resurfaced one), correct the engine's live
    // selection state so a retry inherits the values the user just confirmed.
    if (entry?.live) {
      send({ type: "set_selection", course: filled.course, character: filled.character,
             kart: filled.kart, costume: filled.costume });
    }
    pushLog(`[review] submitted ${attempt_id}`);
    _dequeue(attempt_id);
  }
  function onReviewDiscard(e) {
    const { attempt_id } = e.detail;
    invoke("sync_discard_pending", { attemptId: attempt_id }).catch(() => {});
    pushLog(`[review] discarded ${attempt_id}`);
    _dequeue(attempt_id);
  }

  // ── Camera ────────────────────────────────────────────────────────────────────
  // Open the browser camera and Python camera on the same physical device.
  // Called once on every normal (post-setup) launch after devices_list arrives.
  async function _openMatchedCameras() {
    await loadBrowserDevices();
    // Only auto-select a browser device when none is explicitly chosen yet.
    // If selectedBrowserDeviceId is already set (e.g. user picked from dropdown),
    // honour it - don't override with a configuredDevice name match.
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
      //   1. "none" sentinel - user explicitly wants video-only, skip all audio logic
      //   2. Specific device ID chosen by user this session
      //   3. Audio input sharing a non-empty groupId with the chosen video device
      //   4. Video-only fallback - never grab the default mic
      const audioExplicitNone = selectedAudioDeviceId === "none";
      let resolvedAudioId = (!audioExplicitNone && selectedAudioDeviceId) ? selectedAudioDeviceId : null;
      if (!resolvedAudioId && !audioExplicitNone && deviceId) {
        try {
          const all = await navigator.mediaDevices.enumerateDevices();
          const vid = all.find(d => d.kind === "videoinput" && d.deviceId === deviceId);
          // groupId is "" when permissions haven't been granted yet - skip in that case
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
        // No paired audio found - video only. Never grab a random mic.
        videoStream = await navigator.mediaDevices.getUserMedia({ video:vc });
      }
      cameraStatus = "ok";
      await loadBrowserDevices();
      // If audio labels are blank (mic permission not yet granted), make a brief
      // audio-only request purely to unlock enumerateDevices labels, then stop it.
      // This never keeps a mic stream open - it's discarded immediately.
      if (audioDevices.some(d => !d.label)) {
        try {
          const tmp = await navigator.mediaDevices.getUserMedia({ audio: true });
          tmp.getTracks().forEach(t => t.stop());
          await loadBrowserDevices();
        } catch { /* mic denied - labels stay blank */ }
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

  // Engine feed poll - runs continuously at 100ms so main view always has a fresh frame
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
    if (!editingNode) return null;
    if (activeTab === "detection") return activeRegionObj?.roi ?? null;
    if (activeTab === "selection" || activeTab === "hud")
      return activeRoiName ? (rois[activeRoiName] ?? null) : null;
    return null;
  }

  function updateCurrentRoi(roi) {
    if (!editingNode) return;
    if (activeTab === "detection" && selectedNode) {
      const g = activeRegion.group, r = activeRegion.region, sn = selectedNode;
      tells = tells.map(t => t.screen !== sn ? t : { ...t,
        groups: t.groups.map((grp, gi) => gi !== g ? grp
          : grp.map((reg, ri) => ri !== r ? reg : { ...reg, roi })) });
    } else if ((activeTab === "selection" || activeTab === "hud") && activeRoiName) {
      rois = { ...rois, [activeRoiName]: roi };
    }
  }

  function saveCurrentRoi(roi) {
    if (!editingNode) return;
    if (activeTab === "detection" && selectedNode)
      send({ type:"update_region", screen:selectedNode, group:activeRegion.group, region:activeRegion.region, roi });
    else if (activeTab === "selection" || activeTab === "hud") {
      const ck = _activeRoiConfigKey();
      if (ck) send({ type:"update_config", key:ck, value:roi });
    }
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
    // Start both simultaneously - same pattern as the initial camera step open.
    // Don't wait for Python's camera_status to trigger the browser restart;
    // if Python fails the browser would be stuck idle indefinitely.
    startCamera(selectedBrowserDeviceId || undefined);
    send({type:"open_camera"});
  }

  // ── ROI preview poll ──────────────────────────────────────────────────────────
  // Detection tab: re-score the selected region against the live feed at ~3 Hz so
  // the RegionInspector match number + live crop track the moving camera image
  // (replaces the old one-shot "Test" button). test_region returns score + crop +
  // template in one message, so this single poll keeps the whole inspector live.
  // The heavier asset template fetch (selection/HUD tab) stays ~1 Hz.
  let _roiPollTick = 0;
  function startRoiPoll() {
    if (_roiPollTimer) return;
    _roiPollTimer=setInterval(()=>{
      if (!trackerConnected) return;
      if (!editingNode) return;
      _roiPollTick++;
      if (activeTab === "detection" && selectedNode)
        send({type:"test_region",screen:selectedNode,group:activeRegion.group,region:activeRegion.region});
      else if ((activeTab === "selection" || activeTab === "hud") && ROI_TEMPLATE_CAT[activeRoiName]) {
        if (_roiPollTick % 3 !== 0) return;
        const item=ASSET_ITEMS[templateCategory]?.[templateItemIdx];
        if (item) send({type:"get_asset_template",category:templateCategory,item_name:item.file});
      }
    },320);
  }
  function stopRoiPoll() {
    if (_roiPollTimer) { clearInterval(_roiPollTimer); _roiPollTimer=null; }
    liveRoiCrop=null;
  }

  function onThreshChange() {
    if (!editingNode) return;
    if (activeTab === "detection" && selectedNode && trackerConnected) {
      const g = activeRegion.group, r = activeRegion.region, sn = selectedNode;
      tells = tells.map(t => t.screen !== sn ? t : { ...t,
        groups: t.groups.map((grp, gi) => gi !== g ? grp
          : grp.map((reg, ri) => ri !== r ? reg : { ...reg, thresh: currentBinaryThresh })) });
      send({ type:"update_region", screen:sn, group:g, region:r, thresh:currentBinaryThresh });
      send({ type:"test_region", screen:sn, group:g, region:r });
    }
  }

  // ── Wizard controls ───────────────────────────────────────────────────────────
  async function openWizard() {
    wizardOpen=true; wizardStep="language";
    currentScore=null;
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
    // groupId pairing fell back to video-only. Now permission exists - restart the
    // stream so grouped audio is picked up automatically, matching reboot behaviour.
    if (!selectedAudioDeviceId) startCamera(selectedBrowserDeviceId || undefined);
  }
  function goStep(step) {
    wizardStep=step;
    templateCategory="characters"; templateItemIdx=0;
    currentScore=null; templateImg=null; liveCropImg=null;
    liveRoiCrop=null; assetTemplateImg=null; assetLiveCrop=null;
    resetConfirmPending=false; syncThreshToScreen();
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

  function captureAsset() {
    const item=ASSET_ITEMS[templateCategory]?.[templateItemIdx]; if (!item) return;
    capturingTemplate=true; assetTemplateImg=null;
    send({type:"capture_asset_template",category:templateCategory,item_name:item.file});
  }

  // ── Language handlers ─────────────────────────────────────────────────────────
  function onAppLanguageChange()   { send({type:"update_config",key:"app_language",   value:appLanguage}); }
  function onSwitch2LanguageChange(){ send({type:"update_config",key:"switch2_language",value:switch2Language}); }

  // ── Device / update ───────────────────────────────────────────────────────────

  async function handleCameraDeviceChange(e) {
    // Guard against rapid switches during an in-progress open. The `disabled`
    // attribute handles the normal case but has a Svelte reactivity timing gap;
    // this synchronous check is the true gate.
    if (pythonCameraStatus==="opening" || cameraStatus==="requesting") {
      e.target.value = selectedBrowserDeviceId; // snap visual selection back
      return;
    }
    selectedBrowserDeviceId=e.target.value;
    // Reset audio to auto - new device may have different associated audio.
    selectedAudioDeviceId = "";
    // Clear stale frame and show "opening" immediately.
    engineFrame = null; pythonCameraStatus = "opening";
    if (!setupComplete&&wizardStep==="camera") {
      // First-time setup: user explicitly chose a device - sync Python to match it
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
    // Persist the choice by label (not ID - IDs change between sessions).
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
    // Pre-populate browser device lists now - Tauri grants camera+mic permissions
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
        pushLog(`[app] engine process has not spawned yet… (${secs}s elapsed) - antivirus may be blocking launch`);
      } else {
        pushLog(`[app] engine launched but Python not ready yet… (${secs}s elapsed) - Windows Defender may still be scanning DLLs`);
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
    startFeedPoll();
    initDiscordPresence();
    initSync();
    initPresence();   // stream this player's live status to the server, mirror the roster's into the `presence` store
    // Load the roster so trail config can resolve player ids (cached for offline use).
    try {
      const list = JSON.parse(await invoke("sync_roster"));
      if (Array.isArray(list) && list.length) cacheRoster(list);
    } catch (_) { /* keep the cached roster */ }
    // Resurface any runs held for review from a previous session.
    try {
      const pending = JSON.parse(await invoke("sync_list_pending"));
      if (Array.isArray(pending) && pending.length) {
        reviewQueue = [
          ...reviewQueue,
          ...pending.map((p) => ({ attemptId: p.attempt_id, run: p.run, isPb: !!p.is_pb, live: false })),
        ];
        pushLog(`[review] ${pending.length} run(s) awaiting review from a previous session`);
      }
    } catch (_) { /* no outbox / not ready - ignore */ }
  });

  onDestroy(()=>{
    if (unlisten) unlisten();
    stopCamera(); stopRoiPoll(); stopFeedPoll(); _teardownAudio();
    if (trackerCameraPaused) send({type:"resume_camera"});
  });

  $: if (wizVideoEl)  wizVideoEl.srcObject =videoStream??null;
  // (ROI canvas redraw/drag/pan + its window mouseup listener now live inside
  // RoiCanvas.svelte; the editor view just feeds it props.)

  // ── Reactive computeds ────────────────────────────────────────────────────────
  $: cameraOk  = cameraStatus==="ok";
  $: pythonCameraOk = pythonCameraStatus==="ok"&&engineFrame!==null&&!trackerCameraPaused;
  $: bothCamerasOk  = cameraOk&&pythonCameraOk;
  $: assetItem = ASSET_ITEMS[templateCategory]?.[templateItemIdx];

  $: if (((wizardOpen || appView === "setup")&&["screens","selection","hud","templates"].includes(wizardStep))
         || (editingNode && selectedNode && (
              activeTab === "detection"
              || ((activeTab === "selection" || activeTab === "hud") && ROI_TEMPLATE_CAT[activeRoiName])))) {
    startRoiPoll();
  } else { stopRoiPoll(); }

  // The engine-frame poll (100ms capture_frame) feeds both the setup camera step
  // and the RoiCanvas background in edit view, so keep it running whenever the
  // tracker is connected. (The monitor view shows the browser stream directly.)
  $: if ($viewStore === "edit" || trackerConnected) startFeedPoll();
     else stopFeedPoll();

  // Load the stored template + live crop whenever the selected region changes.
  $: if (editingNode && activeTab === "detection" && selectedNode && activeRegion && trackerConnected) {
    send({ type:"get_region_images", screen:selectedNode, group:activeRegion.group, region:activeRegion.region });
  }

  // Fetch edit-mode graph thumbnails when the edit view opens; refetch if the
  // Switch language changes (screenshots are per-language).
  $: if ($viewStore === "edit" && trackerConnected && _thumbsLang !== switch2Language) {
    _thumbsLang = switch2Language;
    send({ type: "get_screen_thumbs", lang: switch2Language });
  }

  $: _=appLanguage;
  function tr(key) { return t(key,appLanguage); }

  function syncThreshToScreen() {
    currentBinaryThresh = editingNode ? (activeRegionObj?.thresh ?? 170) : 170;
  }

  $: if (browserDevices.length>0&&configuredDevice) {
    const lower=configuredDevice.toLowerCase();
    const match=browserDevices.find(d=>d.label.toLowerCase().includes(lower));
    if (match&&match.deviceId!==selectedBrowserDeviceId) selectedBrowserDeviceId=match.deviceId;
  }

  // scoreColor() lives in lib/format.js

  // ── Store mirrors (keep local vars unchanged; Rail/RaceSection/EventLog read stores) ──
  function rankedFrom(obj) {
    return Object.entries(obj).map(([name, score]) => ({ name, score }))
      .sort((a, b) => b.score - a.score).slice(0, 5);
  }
  $: screenStore.set(backendScreen);
  $: liveScoreStore.set(liveScore);
  $: candidatesStore.set({ screen: rankedFrom(candidateScores).map(c => ({ ...c, name: screenLabel(c.name) })),
                           char: selectionCandidates.char ?? [],
                           kart: selectionCandidates.kart ?? [],
                           course: selectionCandidates.course ?? [],
                           costume: selectionCandidates.costume ?? [] });
  $: selectionStore.set({ char: selChar, charConf: selCharConf,
                          costume: selCostume, costumeConf: selCostumeConf,
                          kart: selKart, kartConf: selKartConf,
                          course: selCourse, courseConf: selCourseConf });
  $: raceStore.set({ curLap, totLap, coins, mushrooms,
                     splits: raceSplits, finishTime: raceFinishTime });
  $: tellsStore.set(tells);
  $: roisStore.set(rois);

  // Replay trail + minimap sample are fetched on RACING entry (see screen_change handler).
  // The _fetchedThisRace guard ensures exactly one fetch per race, even for repeat
  // races on the same course.
</script>

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  MAIN LAYOUT                                                               -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<div class="app">

  <!-- ── Title bar ──────────────────────────────────────────────────────────── -->
  <TitleBar
    version={version}
    view={appView === "main" ? $viewStore : "monitor"}
    editingScreen={selectedNode}
    onToggleView={toggleView}
    onMinimize={winMinimize}
    onToggleMaximize={winToggleMaximize}
    onClose={winClose}
  >
    <!-- Update strip: logic stays here; slot is rendered inside TitleBar's .tb-actions -->
    <svelte:fragment slot="update">
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
    </svelte:fragment>
    <!-- Settings button: view- and wizard-state-conditional logic stays here -->
    <svelte:fragment slot="settings">
      {#if appView === "main"}
        {#if wizardOpen}
          <button class="btn-hdr btn-close-wiz" on:click={closeWizard}>✕ Close Settings</button>
        {:else}
          <button class="btn-hdr btn-setup" on:click={openSettings}>⚙ Settings</button>
        {/if}
      {/if}
    </svelte:fragment>
  </TitleBar>

  <!-- ── View router ───────────────────────────────────────────────────────── -->
  {#if appView === "main"}

    {#if $viewStore === "edit"}

    <!-- ── Edit view: screen graph + ROI canvas + tools panel ───────────────── -->
    <EditMode
      bind:this={editCanvas}
      currentScreen={backendScreen}
      selected={selectedNode}
      stream={setupComplete ? (videoStream ?? null) : null}
      frame={engineFrame}
      thumbs={screenThumbs}
      rois={canvasRois}
      activeBox={activeEditBox}
      frameW={pythonFrameW}
      frameH={pythonFrameH}
      activeTab={toolsActiveTab}
      {readoutEnabled}
      detection={detectionBundle}
      readout={readoutBundle}
      on:selectScreen={(e)=>openNode(e.detail)}
      on:tabChange={(e)=>setToolsTab(e.detail)}
      on:change={(e)=>onCanvasChange(e.detail)}
      on:selectBox={(e)=>onCanvasSelect(e.detail)}
      on:selectRegion={(e)=>selectRegion(e.detail.group, e.detail.region)}
      on:addRegion={(e)=>addRegion(e.detail)}
      on:addGroup={addGroup}
      on:removeRegion={removeActiveRegion}
      on:kindChange={(e)=>onKindChange(e.detail)}
      on:requestReset={()=>{ if (activeTab==="detection") detResetPending=true; else roiResetPending=true; }}
      on:cancelReset={()=>{ detResetPending=false; roiResetPending=false; }}
      on:resetDetection={resetDetection}
      on:capture={onToolsCapture}
      on:selectRoi={(e)=>selectRoiName(e.detail)}
      on:selectItem={(e)=>selectTplItem(e.detail)}
      on:resetRoi={resetActiveRoi}
    />

    {:else}

    <!-- ── Monitor view: camera feed + status sidebar ───────────────────────── -->
    <div class="main-grid" class:sidebar-collapsed={!sidebarOpen}>

      <!-- Left: camera feed -->
      <div class="main-feed">
        <div class="feed-area">
          <FeedOverlay
            stream={setupComplete ? (videoStream ?? null) : null}
            muted={feedMuted}
            volume={feedVolume}
            hidden={!cameraOk || feedVideoHidden}
          />
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

        <!-- Live player panel (sub-project #3) -->
        <div class="player-band">
          <PlayerPanel />
        </div>
      </div>

      <!-- Right: sidebar panels (collapsible) -->
      <aside class="sidebar" class:sidebar-collapsed={!sidebarOpen}>
        <button class="sidebar-toggle" on:click={()=>sidebarOpen=!sidebarOpen}
          title={sidebarOpen ? "Collapse panels" : "Expand panels"}>{sidebarOpen ? "▸" : "◂"}</button>
        {#if sidebarOpen}

        <Rail />

        {/if}
      </aside>

    </div><!-- /main-grid -->

    {/if}

  {:else if appView === "setup"}

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
        <!-- language / camera steps -->
        {#if wizardStep === "language"}
          <div class="step-centred">
            <h2>{tr("lang.title")}</h2>
            <p>{tr("lang.desc")}</p>
            <LanguageSelectors
              {LANGUAGES}
              bind:appLanguage
              bind:switch2Language
              {onAppLanguageChange}
              {onSwitch2LanguageChange}
              idPrefix="sv"
            />
            <button class="btn-primary btn-lg" on:click={()=>goStep("camera")}>{tr("lang.continue")}</button>
          </div>

        {:else if wizardStep === "camera"}
          <div class="cam-setup">
            <SourceCheck
              bind:videoEl={wizVideoEl}
              {cameraOk}
              {cameraStatus}
              {trackerCameraPaused}
              {engineFrame}
              {pythonCameraOk}
              {pythonCameraStatus}
              {pythonCameraError}
              {trackerConnected}
            />

            <div class="cam-below">
              <DeviceSelectors
                {browserDevices}
                {selectedBrowserDeviceId}
                {audioDevices}
                {selectedAudioDeviceId}
                {pythonCameraStatus}
                {cameraStatus}
                {restartNeeded}
                onCameraDeviceChange={handleCameraDeviceChange}
                onAudioDeviceChange={handleAudioDeviceChange}
                onRestartTracker={restartTracker}
              />
              <div class="cam-prereq" class:cam-prereq-ok={bothCamerasOk}>
                {#if bothCamerasOk}
                  <span class="cam-prereq-title cam-prereq-title-ok">Camera sharing enabled</span>
                  <p class="cam-prereq-body">Both feeds are reading the same device.</p>
                {:else}
                  <span class="cam-prereq-title">Camera sharing · required</span>
                  <p class="cam-prereq-body">pbenguin needs simultaneous access to the same capture card as the app preview. Windows blocks this by default. Set it once before continuing:</p>
                  {#if trackerCameraPaused}
                    <div class="cam-release-bar cam-release-bar-released">
                      <span class="cam-release-dot"></span>
                      <span class="cam-release-msg">App feeds released - also close OBS, Discord, and any other apps currently using the camera before proceeding.</span>
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
                    <li>Click <strong>Open Windows camera settings</strong> below</li>
                    <li>Find your capture card, open <strong>Advanced camera options</strong>, then <strong>Edit</strong></li>
                    <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                    <li>Return here, then <button class="btn-sm" on:click={retryNow}>Retry</button></li>
                  </ol>
                  <div class="cam-prereq-actions">
                    <button class="btn-primary" on:click={() => invoke("open_url",{url:"ms-settings:camera"}).catch(()=>{})}>Open Windows camera settings</button>
                  </div>
                {/if}
              </div>

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <div class="cam-nav">
                  <button class="btn-nav" on:click={()=>goStep("language")}>Back</button>
                  <button class="btn-primary" disabled={!bothCamerasOk} on:click={completeSetup}>
                    Finish setup
                  </button>
                </div>
              </div>
            </div>
          </div>

        {/if}
      </div>
    </div>

    <!-- Engine log sidebar - always visible during first-time setup -->
    <div class="setup-log-side">
      <div class="setup-log-hdr">
        <span class="hb-dot" style="background:{statusDot}; flex-shrink:0"></span>
        {#if trackerConnected}
          <span class="setup-log-status">Engine ready</span>
        {:else if trackerSpawned}
          <span class="setup-log-status">Starting - Windows may be scanning files…</span>
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
          <span class="setup-log-status">Starting - Windows may be scanning files…</span>
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

  <!-- ── Bottom status bar (single home for live engine status) ─────────────── -->
  <StatusBar
    connected={trackerConnected}
    alive={backendAlive}
    spawned={trackerSpawned}
    screenName={backendScreen}
    score={liveScore}
    fps={backendFps}
    frameW={pythonFrameW}
    frameH={pythonFrameH}
  />

</div><!-- /app -->


<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  SETTINGS / WIZARD MODAL                                                   -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<SettingsModal
  {wizardOpen}
  {setupComplete}
  {wizardStep}
  {STEPS}
  {STEP_LABELS}
  onGoStep={goStep}
  onClose={closeWizard}
  bind:wizVideoEl
  {cameraOk}
  {cameraStatus}
  {trackerCameraPaused}
  {engineFrame}
  {pythonCameraOk}
  {pythonCameraStatus}
  {pythonCameraError}
  {trackerConnected}
  {bothCamerasOk}
  {browserDevices}
  {selectedBrowserDeviceId}
  {audioDevices}
  {selectedAudioDeviceId}
  {restartNeeded}
  {LANGUAGES}
  bind:appLanguage
  bind:switch2Language
  onCameraDeviceChange={handleCameraDeviceChange}
  onAudioDeviceChange={handleAudioDeviceChange}
  onRestartTracker={restartTracker}
  onReleaseForSettings={releaseForSettings}
  onRetryNow={retryNow}
  onAppLanguageChange={onAppLanguageChange}
  onSwitch2LanguageChange={onSwitch2LanguageChange}
/>

{#if reviewHead}
  <RunReviewModal
    run={reviewHead.run}
    isPb={reviewHead.isPb}
    pbBest={pbBestLookup}
    options={optionLists}
    queueIndex={0}
    queueCount={reviewQueue.length}
    on:submit={onReviewSubmit}
    on:discard={onReviewDiscard}
  />
{/if}

<!-- ═══════════════════════════════════════════════════════════════════════════ -->
<!--  STYLES                                                                    -->
<!-- ═══════════════════════════════════════════════════════════════════════════ -->

<style>
  /* ── Global ──────────────────────────────────────────────────────────────── */
  /* Universal reset, html/body base, #app, and scrollbar styling live in src/theme.css */

  /* ── App shell ────────────────────────────────────────────────── */
  .app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

  /* ── Title bar (component: src/components/TitleBar.svelte) ───────── */
  /* Update-strip markup is slotted from App.svelte so its styles stay here */
  .upd-strip  { display: flex; align-items: center; gap: 5px; font-size: .65rem; }
  .upd-label  { color: var(--ok); flex-shrink: 0; font-family: var(--mono); }
  .upd-track  { width: 60px; height: 3px; background: var(--track); border-radius: var(--r-sm); overflow: hidden; }
  .upd-fill   { height: 100%; background: var(--ok); transition: width .2s; }

  /* Settings / close-wizard buttons are slotted from App.svelte so their styles stay here */
  .btn-hdr {
    background: var(--panel); border-radius: var(--r); padding: 3px 9px;
    font-family: inherit; font-size: .68rem; cursor: pointer; white-space: nowrap;
    transition: background .12s; -webkit-app-region: no-drag;
  }
  .btn-setup       { color: var(--tx-mut); border: 1px solid var(--bd); }
  .btn-setup:hover { background: var(--raised); }
  .btn-close-wiz   { color: var(--tx-dim); border: 1px solid var(--bd); }
  .btn-close-wiz:hover { color: var(--tx-mut); background: var(--bd); }

  /* ── Status bar styles live in src/components/StatusBar.svelte ───── */
  /* (Screen-graph strip + per-screen editor now live in EditMode.svelte and its
     child components: ScreenGraph / RoiCanvas / ToolsPanel.) */

  /* ── Main grid (monitor view: feed | sidebar) ─────────────────── */
  .main-grid {
    display: grid;
    grid-template-columns: 1fr 256px;
    grid-template-rows: 1fr;
    flex: 1; min-height: 0; overflow: hidden;
  }
  .main-grid.sidebar-collapsed { grid-template-columns: 1fr 22px; }

  /* Collapsible sidebar */
  .sidebar-toggle {
    position: sticky; top: 0; z-index: 3; width: 100%; height: 22px; box-sizing: border-box;
    display: flex; align-items: center; justify-content: flex-end;
    background: var(--panel); border: none; border-bottom: 1px solid var(--bd);
    color: var(--tx-mut); cursor: pointer; font-size: .62rem; line-height: 1; padding: 0 8px;
  }
  .sidebar-toggle:hover { color: var(--tx-mut); }
  .sidebar.sidebar-collapsed { overflow: hidden; }
  .sidebar.sidebar-collapsed .sidebar-toggle { justify-content: center; padding: 0; }

  /* ── Feed (col 1, row 1) ─────────────────────────────────────── */
  .main-feed {
    grid-column: 1; grid-row: 1;
    display: flex; flex-direction: column;
    background: var(--feed-bg); overflow: hidden; min-height: 0;
  }
  .feed-area {
    /* Video anchored to the top at its native 16:9 - no top/bottom letterbox bars. Shrinks
       (flex) only when the window is too short, so the player band keeps its minimum. */
    flex: 0 1 auto; width: 100%; aspect-ratio: 16 / 9; min-height: 0;
    position: relative; overflow: hidden; background: var(--feed-bg);
  }
  .player-band {
    /* Live player panel (sub-project #3): fills the space below the feed + controls. */
    flex: 1 0 0; min-height: 130px; overflow: hidden;
    border-top: 1px solid var(--bd); background: var(--bg);
  }
  .feed-placeholder {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: .5rem; color: var(--tx-dim); font-size: .8rem;
  }
  .feed-ph-dim { color: var(--bd); font-size: .68rem; }

  /* ── Feed controls ────────────────────────────────────────────── */
  .feed-controls {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 10px; flex-shrink: 0;
    background: var(--panel); border-top: 1px solid var(--raised);
    height: 28px;
  }
  .fc-btn {
    background: transparent; border: none; cursor: pointer; color: var(--tx-dim);
    display: flex; align-items: center; gap: 3px; padding: 2px 3px;
    border-radius: var(--r); transition: color .1s, background .1s; flex-shrink: 0;
  }
  .fc-btn:hover { color: var(--tx-mut); background: var(--raised); }
  .fc-icon {
    width: 14px; height: 14px; fill: none;
    stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round;
    flex-shrink: 0;
  }
  .fc-slider {
    flex: 1; min-width: 60px; max-width: 120px;
    accent-color: var(--accent); cursor: pointer; height: 3px;
  }
  .fc-vol {
    font-size: .6rem; color: var(--tx-dim); min-width: 2.4em; text-align: right; flex-shrink: 0;
  }
  .fc-divider { width: 1px; height: 14px; background: var(--bd); flex-shrink: 0; margin: 0 2px; }
  .fc-no-audio { font-size: .58rem; color: var(--tx-dim); flex-shrink: 0; }
  .fc-vid-btn  { gap: 4px; }
  .fc-vid-label { font-size: .62rem; }
  .feed-ph-icon { font-size: 1.8rem; animation: spin 1.4s linear infinite; opacity: .4; }
  .feed-ph-text { font-size: .72rem; color: var(--tx-dim); }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Sidebar (col 2) ─────────────────────────────────────────── */
  .sidebar {
    grid-column: 2; grid-row: 1;
    display: flex; flex-direction: column;
    background: var(--bg); border-left: 1px solid var(--bd);
    overflow-y: auto; overflow-x: hidden;
    min-height: 0;
  }

  .log-line  { font-size: .65rem; color: var(--tx-mut); white-space: pre-wrap; word-break: break-all; line-height: 1.5; padding: 0 2px; font-family: var(--mono); }
  .log-empty { font-size: .65rem; color: var(--tx-dim); font-style: italic; padding: 4px 2px; }
  .log-error { color: var(--err); font-style: normal; }

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
  .startup-status-text { font-size: .85rem; color: var(--tx-mut); }
  .startup-status-sub  { font-size: .72rem; color: var(--tx-dim); margin-top: .2rem; }
  .startup-note { font-size: .7rem; color: var(--bd); line-height: 1.6; max-width: 340px; padding-left: calc(8px + .6rem); }

  /* ── Setup view (first-time wizard, full-screen) ───────────────── */
  .setup-view {
    flex: 1; display: flex; flex-direction: row; min-height: 0; overflow: hidden;
  }
  .setup-wiz {
    flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden;
    border-right: 1px solid var(--bd);
  }
  /* Centre content vertically and horizontally; no scrollbar - content must fit */
  .setup-wiz-body {
    flex: 1; min-height: 0; padding: 1.25rem 1.5rem;
    display: flex; align-items: center; justify-content: center;
    overflow: hidden; scrollbar-gutter: stable both-edges;
  }
  /* Constrain camera step width in setup view */
  .setup-wiz-body .cam-setup { width: 100%; max-width: 560px; }
  /* Step indicator tabs are display-only in setup view - not keyboard or mouse navigable */
  .setup-wiz-tabs { pointer-events: none; }

  /* Log sidebar shared by setup and startup views */
  .setup-log-side {
    width: 300px; flex-shrink: 0; display: flex; flex-direction: column;
    border-left: 1px solid var(--bd); background: var(--panel);
  }
  .setup-log-hdr {
    display: flex; align-items: center; gap: .45rem;
    padding: 5px 8px; background: var(--panel); border-bottom: 1px solid var(--raised);
    flex-shrink: 0;
  }
  .setup-log-status { font-size: .65rem; color: var(--tx-dim); line-height: 1.3; min-width: 0; }
  .setup-log-body {
    flex: 1; overflow-y: auto; padding: 4px 8px; min-height: 0;
  }

  /* ── Wizard tab bar (used in setup-view) ─────────────────────── */
  /* Modal shell (modal-backdrop, wiz-dialog, wiz-tab-close) is in SettingsModal.svelte */
  .wiz-tabs {
    display: flex; flex-shrink: 0; background: var(--panel);
    border-bottom: 1px solid var(--bd); overflow-x: auto; scrollbar-width: none;
  }
  .wiz-tabs::-webkit-scrollbar { display: none; }   /* keep this strip's bar hidden on Chromium < 121 too */
  .wiz-tab {
    background: transparent; color: var(--tx-dim); border: none;
    border-right: 1px solid var(--bd);
    padding: 7px 14px; font-family: inherit; font-size: .7rem;
    cursor: pointer; white-space: nowrap; transition: color .12s, background .12s;
  }
  .wiz-tab:hover { background: var(--panel-2); color: var(--tx-mut); }
  /* Active step: neutral text + thin accent underline (matches ToolsPanel .tab.on),
     not blue text. The accent is only ever a sliver in this app. */
  .wiz-tab.active { color: var(--tx); box-shadow: inset 0 -2px 0 var(--accent); }
  .wiz-body { flex: 1; overflow: auto; padding: 1rem; min-height: 0; }

  /* Step: centred */
  .step-centred { max-width: 560px; margin: 0 auto; padding: .5rem 0; display: flex; flex-direction: column; gap: .75rem; }
  .step-centred h2 { color: var(--tx); font-size: .95rem; font-weight: 600; letter-spacing: .01em; }
  .step-centred p  { font-size: .78rem; color: var(--tx-mut); line-height: 1.65; }

  /* Camera step (preview panes live in SourceCheck.svelte) */
  .cam-setup { display: flex; flex-direction: column; gap: .9rem; }
  .cam-below   { display: flex; flex-direction: column; gap: .65rem; }
  .cam-actions { display: flex; flex-direction: column; gap: .3rem; }
  .cam-nav     { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
  .cam-prereq {
    padding: .55rem .7rem; border-radius: var(--r);
    background: var(--panel-2); border: 1px solid var(--bd);
    display: flex; flex-direction: column; gap: .3rem;
  }
  .cam-prereq-title        { font-size: .63rem; color: var(--tx-mut); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
  .cam-prereq-title-ok     { color: var(--ok); }
  .cam-prereq-ok           { background: rgba(90,168,106,.05); border-color: rgba(90,168,106,.2); }
  .cam-prereq-body    { font-size: .68rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }
  .cam-prereq-actions { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-top: .15rem; }
  .cam-release-bar {
    display: flex; align-items: center; gap: .55rem;
    padding: .38rem .55rem; border-radius: var(--r);
    background: rgba(200,154,62,.05); border: 1px solid rgba(200,154,62,.18);
    transition: background .25s, border-color .25s;
  }
  .cam-release-bar-released {
    background: rgba(90,168,106,.05); border-color: rgba(90,168,106,.2);
  }
  .cam-release-dot {
    width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    background: var(--warn); transition: background .25s;
  }
  .cam-release-bar-released .cam-release-dot { background: var(--ok); }
  .cam-release-msg { flex: 1; font-size: .66rem; color: var(--warn); line-height: 1.45; transition: color .25s; }
  .cam-release-bar-released .cam-release-msg { color: var(--ok); }
  .cam-steps { margin: .15rem 0 .05rem; padding-left: 1.2rem; font-size: .68rem; color: var(--tx-dim); line-height: 1.8; }
  .cam-steps strong { color: var(--tx-mut); }


  /* Buttons */
  /* Primary action: neutral text on a subtle accent-tint fill + accent border
     (the app's .reg.sel idiom), never blue text. */
  .btn-primary {
    background: var(--accent-bg); color: var(--tx); border: 1px solid var(--accent); border-radius: var(--r);
    padding: .28rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; white-space: nowrap; transition: background .12s, border-color .12s;
  }
  .btn-primary:hover:not(:disabled) { background: var(--raised); }
  .btn-primary:disabled { opacity: .35; cursor: default; }
  .btn-primary.btn-lg { padding: .45rem 1.1rem; font-size: .85rem; margin-top: .5rem; }
  .btn-nav {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .24rem .7rem; font-family: inherit; font-size: .72rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-nav:hover { background: var(--raised); color: var(--tx); }
  .btn-sm {
    background: var(--panel-2); color: var(--tx-mut); border: 1px solid var(--bd); border-radius: var(--r);
    padding: .16rem .45rem; font-family: inherit; font-size: .68rem;
    cursor: pointer; flex-shrink: 0; transition: background .12s, color .12s;
  }
  .btn-sm:hover { background: var(--raised); color: var(--tx); }

  .hint { font-size: .7rem; color: var(--tx-dim); margin: 0; line-height: 1.55; }
</style>
