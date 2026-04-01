<script>
  import { onMount, onDestroy, afterUpdate } from "svelte";
  import { check } from "@tauri-apps/plugin-updater";
  import { listen } from "@tauri-apps/api/event";
  import { getVersion } from "@tauri-apps/api/app";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";

  let appWindow = null;
  function winMinimize()       { appWindow?.minimize(); }
  function winToggleMaximize() { appWindow?.toggleMaximize(); }
  function winClose()          { appWindow?.close(); }

  // ── Core state ──────────────────────────────────────────────────────────────
  let version = "";
  let trackerConnected = false;
  let logs = [];
  let logEl;

  // ── Backend health (heartbeat) ───────────────────────────────────────────────
  let backendFps = 0;
  let backendScreen = "—";
  let lastHeartbeatTs = 0;
  let _tick = 0;
  $: backendAlive = trackerConnected && _tick >= 0 && (Date.now() - lastHeartbeatTs) < 4000;
  $: statusDot = !trackerConnected ? "#444" : backendAlive ? "#4caf50" : "#f59e0b";

  // ── Device selector ──────────────────────────────────────────────────────────
  let devices = [];
  let configuredDevice = "";
  let restartNeeded = false;

  // ── Updater ──────────────────────────────────────────────────────────────────
  let pendingUpdate = null;
  let updateVersion = "";
  let downloadTotal = 0;
  let downloadReceived = 0;
  let updateReady = false;
  $: downloadPercent = downloadTotal > 0
    ? Math.min(100, Math.round(downloadReceived / downloadTotal * 100))
    : null;

  // ── Setup wizard ─────────────────────────────────────────────────────────────
  let setupComplete = true;   // assumed true until Python says otherwise
  let wizardOpen = false;
  let wizardStep = "welcome";
  let screenIdx = 0;
  let selectionIdx = 0;
  let hudIdx = 0;

  let tells = [];
  let rois = {};
  let currentScore = null;
  let capturingTemplate = false;
  let templateImg = null;   // stored template for current screen (base64 PNG)
  let liveCropImg = null;   // binarised live crop after Test is pressed (base64 PNG)

  // ── ROI drag editing ──────────────────────────────────────────────────────
  let dragging          = false;
  let dragHandle        = null;   // 'tl'|'tr'|'bl'|'br'|'t'|'b'|'l'|'r'|'move'
  let dragStartMouse    = null;   // {x, y} in frame coords at drag start
  let dragStartRoi      = null;   // roi snapshot at drag start
  let hoveredHandle     = null;
  let liveRoiCrop       = null;   // live crop for selection/hud steps
  let _roiPollTimer     = null;
  let currentBinaryThresh = 170;
  let activeRoiKey      = "primary";  // which ROI is selected for editing in the screens step

  // ── Camera preview (getUserMedia — permission silently granted by Rust) ───────
  let videoEl = null;
  let canvasEl = null;
  let videoStream = null;
  // 'idle' | 'requesting' | 'ok' | 'busy' | 'pausing' | 'error'
  let cameraStatus = "idle";
  let trackerCameraPaused = false;   // true while Python released its camera hold
  let browserDevices = [];
  let selectedBrowserDeviceId = "";

  // ── Python engine camera feed ────────────────────────────────────────────
  // 'idle' | 'opening' | 'ok' | 'error'
  let pythonCameraStatus = "idle";
  let pythonCameraError = "";
  let engineFrame = null;         // data URL for the latest engine JPEG
  let _enginePollTimer = null;
  let pythonFrameW = 1920;        // actual Python capture width (from camera_status)
  let pythonFrameH = 1080;        // actual Python capture height

  const STEPS = ["welcome", "camera", "screens", "selection", "hud", "done"];
  const STEP_LABELS = {
    welcome: "Welcome", camera: "Camera", screens: "Screens",
    selection: "Selection", hud: "HUD", done: "Done",
  };

  // Only canonical screens are shown in the wizard. GHOST, UNKNOWN_RACE_ACTIVE,
  // GHOST_RESET, and UNKNOWN_RESET share the same tell as their canonical screen
  // and are configured automatically via TELL_GROUP_ALIASES propagation.
  const SCREEN_NAMES = [
    "TITLE", "HOME", "MAIN_MENU", "SINGLEPLAYER_MENU", "TIME_TRIALS",
    "CHARACTER_SELECT", "KART_SELECT", "COURSE_SELECT",
    "START_TIME_TRIAL", "START_REPLAY",
    "RACING",           // covers GHOST + UNKNOWN_RACE_ACTIVE
    "RACE_MENU", "REPLAY_MENU", "REPLAY_RACE_AGAINST",
    "RESET",            // covers GHOST_RESET + UNKNOWN_RESET
    "POST_TIME_TRIAL", "GALLERY",
  ];

  // Aliases that share the same tell as their canonical screen.
  // Saving ROI/thresh for the canonical auto-propagates to these on the Python side.
  const TELL_GROUP_ALIASES = {
    RACING: ["GHOST", "UNKNOWN_RACE_ACTIVE"],
    RESET:  ["GHOST_RESET", "UNKNOWN_RESET"],
  };
  const TELL_GROUP_NOTES = {
    RACING: "This ROI setup also applies to Ghost Race and Unknown Race states — they share the same detection tell.",
    RESET:  "This ROI setup also applies to Ghost Reset and Unknown Reset states — they share the same detection tell.",
  };

  const SCREEN_LABELS = {
    TITLE:                "Title Screen",
    HOME:                 "Home / Profile Select",
    MAIN_MENU:            "Main Menu",
    SINGLEPLAYER_MENU:    "Single Player Mode Menu",
    TIME_TRIALS:          "Time Trials Menu",
    CHARACTER_SELECT:     "Character Selection",
    KART_SELECT:          "Kart & Parts Selection",
    COURSE_SELECT:        "Course Selection",
    START_TIME_TRIAL:     "Race Countdown (Time Trial)",
    START_REPLAY:         "Ghost Race Countdown",
    RACING:               "In Race",
    GHOST:                "Ghost Race",
    UNKNOWN_RACE_ACTIVE:  "Active Race (Unknown Type)",
    RACE_MENU:            "Race Pause Menu",
    REPLAY_MENU:          "Ghost Replay Menu",
    REPLAY_RACE_AGAINST:  "Race Against Ghost",
    RESET:                "Reset / Retry Screen",
    GHOST_RESET:          "Ghost Reset Screen",
    POST_TIME_TRIAL:      "Post-Race Results",
    GALLERY:              "Gallery Browser",
  };

  const SCREEN_HINTS = {
    TITLE:               "The startup title/logo screen.",
    HOME:                "The player profile selection screen shown after title.",
    MAIN_MENU:           "Main menu with single player, multiplayer, etc.",
    SINGLEPLAYER_MENU:   "Single player mode selector (Time Trials, Grand Prix…).",
    TIME_TRIALS:         "Time trials mode menu — character and course selection.",
    CHARACTER_SELECT:    "The character/driver selection screen.",
    KART_SELECT:         "The kart body, tires, and glider selection screen.",
    COURSE_SELECT:       "The track/course selection grid.",
    START_TIME_TRIAL:    "The 3-2-1 countdown before a time trial race begins.",
    START_REPLAY:        "The 3-2-1 countdown before a ghost race begins.",
    RACING:              "Active racing — coin counter and flag icon visible bottom-left. Covers all race types.",
    GHOST:               "Racing against a ghost replay.",
    UNKNOWN_RACE_ACTIVE: "An active race detected without clear type identification.",
    RACE_MENU:           "The in-race pause menu (press + / pause).",
    REPLAY_MENU:         "The ghost replay options menu after a ghost race.",
    REPLAY_RACE_AGAINST: "The 'Race Against Ghost' options menu.",
    RESET:               "The reset/retry confirmation screen. Covers both time trial and ghost race resets.",
    GHOST_RESET:         "The ghost race reset screen.",
    POST_TIME_TRIAL:     "The results screen displayed after finishing a time trial.",
    GALLERY:             "The replay gallery / save data browser.",
  };

  const SELECTION_ROIS = [
    { key: "char_name",   label: "Character Name",  hint: "Character name text, bottom-right panel on character select screen." },
    { key: "costume",     label: "Costume Name",     hint: "Costume/variant text below character name." },
    { key: "kart_name",   label: "Kart Name",        hint: "Kart body name text on kart selection screen." },
    { key: "course_name", label: "Course Name",      hint: "Course name displayed in the course selection screen." },
  ];

  const HUD_ROIS = [
    { key: "lap_current",  label: "Lap Counter (current)", hint: "Current lap digit — bottom-left race HUD." },
    { key: "lap_total",    label: "Lap Counter (total)",   hint: "Total laps digit next to current lap." },
    { key: "coin_left",    label: "Coin Digit (tens)",     hint: "Left/tens coin counter digit." },
    { key: "coin_right",   label: "Coin Digit (units)",    hint: "Right/units coin counter digit." },
    { key: "finish",       label: "Finish Position",       hint: "1st / 2nd / 3rd finish overlay, top-right area." },
    { key: "mushroom",     label: "Mushroom Count",        hint: "Mushroom stack indicator, top-left area." },
  ];

  const HUD_ROI_CONFIG_KEYS = {
    lap_current: "lap_current_roi", lap_total:  "lap_total_roi",
    coin_left:   "coin_left_roi",   coin_right: "coin_right_roi",
    finish:      "finish_roi",      mushroom:   "mushroom_roi",
  };
  const SELECTION_ROI_CONFIG_KEYS = {
    char_name: "char_name_roi", costume:     "costume_roi",
    kart_name: "kart_name_roi", course_name: "course_name_roi",
  };
  const HANDLE_HIT_RADIUS = 9; // screen pixels

  let unlisten;

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function send(msg) {
    invoke("send_to_tracker", { message: JSON.stringify(msg) }).catch(() => {});
  }

  function pushLog(line) {
    logs = [...logs.slice(-199), line];
    setTimeout(() => { if (logEl) logEl.scrollTop = logEl.scrollHeight; }, 0);
  }

  function handleMsg(msg) {
    switch (msg.type) {
      case "ready":
        trackerConnected = true;
        lastHeartbeatTs = Date.now();   // prevent "backend stalled" flash before first heartbeat
        send({ type: "list_devices" });
        send({ type: "list_tells" });
        send({ type: "list_rois" });
        if (!msg.setup_complete) {
          setupComplete = false;
          openWizard();
        }
        break;
      case "camera_status":
        pythonCameraStatus = msg.ok ? "ok" : "error";
        pythonCameraError  = msg.error ?? "";
        if (msg.ok) {
          if (msg.width  > 0) pythonFrameW = msg.width;
          if (msg.height > 0) pythonFrameH = msg.height;
          trackerCameraPaused = false;   // clear "released" state
          // Lock the browser camera to the same device Python opened.
          // Match by stripping the vendor:product suffix browsers append to labels.
          if (msg.device) {
            const pyDev = msg.device.toLowerCase();
            const match = browserDevices.find(d => {
              const clean = d.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim().toLowerCase();
              return clean === pyDev || clean.includes(pyDev) || pyDev.includes(clean);
            });
            if (match && match.deviceId !== selectedBrowserDeviceId) {
              selectedBrowserDeviceId = match.deviceId;
            }
          }
          // On camera step: Python confirmed access, now open browser on the matched device.
          if (wizardStep === "camera" && cameraStatus === "idle") {
            startCamera(selectedBrowserDeviceId || undefined);
          }
        }
        break;
      case "frame_data":
        engineFrame = `data:image/jpeg;base64,${msg.data}`;
        break;
      case "heartbeat":
        backendFps    = msg.fps    ?? 0;
        backendScreen = msg.screen ?? "—";
        lastHeartbeatTs = Date.now();
        break;
      case "camera_paused":
        _pauseIntent = "";
        // Python released its camera. Wait for the user to click Retry.
        break;
      case "camera_resumed":
        trackerCameraPaused = false;
        break;
      case "template_images":
        if (msg.screen === currentScreenName &&
            (msg.roi_key ?? "primary") === activeRoiKey) {
          templateImg = msg.template_img ? `data:image/png;base64,${msg.template_img}` : null;
          liveCropImg = msg.live_crop    ? `data:image/png;base64,${msg.live_crop}`    : null;
        }
        break;
      case "template_score":
        if ((msg.roi_key ?? "primary") === activeRoiKey) {
          currentScore = { screen: msg.screen, score: msg.score,
                           threshold: msg.threshold, matched: msg.matched };
          if (msg.template_img) templateImg = `data:image/png;base64,${msg.template_img}`;
          if (msg.live_crop)    liveCropImg  = `data:image/png;base64,${msg.live_crop}`;
        }
        capturingTemplate = false;
        break;
      case "template_saved":
        currentScore = { screen: msg.screen, score: msg.score,
                         threshold: msg.threshold, matched: msg.matched };
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
      case "devices_list":
        devices = msg.devices ?? [];
        configuredDevice = msg.configured ?? "";
        break;
      case "screen_change":
        pushLog(`[screen] ${msg.from} → ${msg.to}`);
        break;
      case "selection_update":
        pushLog(`[sel] ${msg.character ?? "—"} / ${msg.kart ?? "—"} / ${msg.course ?? "—"} / ${msg.costume ?? "—"}`);
        break;
      case "lap_update":
        pushLog(`[lap] ${msg.current}/${msg.total}${msg.split ? `  ${msg.split}` : ""}`);
        break;
      case "coin_update":  pushLog(`[coins] ${msg.coins}`); break;
      case "mush_update":  pushLog(`[mush] ${msg.count}`);  break;
      case "finish":
        pushLog(`[finish] ${msg.result}  ${msg.total_time}`);
        break;
      case "error": pushLog(`[ERR] ${msg.message}`); break;
    }
  }

  // ── Camera (getUserMedia — no permission popup, Rust auto-grants it) ──────────
  async function loadBrowserDevices() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      browserDevices = all.filter(d => d.kind === "videoinput");
      if (!selectedBrowserDeviceId && browserDevices.length > 0) {
        selectedBrowserDeviceId = browserDevices[0].deviceId;
      }
    } catch { /* ignore */ }
  }

  async function startCamera(deviceId) {
    stopCamera();
    cameraStatus = "requesting";
    const constraint = deviceId
      ? { video: { deviceId: { exact: deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } } }
      : { video: { width: { ideal: 1920 }, height: { ideal: 1080 } } };
    try {
      videoStream = await navigator.mediaDevices.getUserMedia(constraint);
      cameraStatus = "ok";
      await loadBrowserDevices();  // re-enumerate now labels are available
    } catch (err) {
      videoStream = null;
      if (err.name === "NotReadableError" || err.name === "TrackStartError") {
        cameraStatus = "busy";
      } else {
        cameraStatus = "error";
      }
    }
  }

  function stopCamera() {
    if (videoStream) {
      videoStream.getTracks().forEach(t => t.stop());
      videoStream = null;
    }
    cameraStatus = "idle";
  }

  // ── ROI overlay helpers ───────────────────────────────────────────────────────

  function getAllRoisForTell(tell) {
    if (!tell) return [];
    const result = [];
    if (tell.roi) result.push({ key: "primary", roi: tell.roi, type: "primary", label: "Primary" });
    if (tell.required_also) {
      tell.required_also.forEach((ra, i) => {
        if (ra.roi) result.push({ key: `and_${i}`, roi: ra.roi, type: "and", label: `AND ${i + 1}` });
      });
    }
    if (tell.alt_roi && tell.alt_image_path) {
      result.push({ key: "alt", roi: tell.alt_roi, type: "or", label: "OR Alt" });
    }
    return result;
  }

  function getCurrentRoi() {
    if (wizardStep === "screens") {
      const tell = tells.find(t => t.screen === SCREEN_NAMES[screenIdx]);
      if (!tell) return null;
      if (activeRoiKey === "primary") return tell.roi ?? null;
      if (activeRoiKey === "alt")     return tell.alt_roi ?? null;
      if (activeRoiKey.startsWith("and_")) {
        const idx = parseInt(activeRoiKey.slice(4));
        return tell.required_also?.[idx]?.roi ?? null;
      }
      return tell.roi ?? null;
    } else if (wizardStep === "selection") {
      return rois[SELECTION_ROIS[selectionIdx]?.key] ?? null;
    } else if (wizardStep === "hud") {
      return rois[HUD_ROIS[hudIdx]?.key] ?? null;
    }
    return null;
  }

  // Compute the letterbox/pillarbox transform for the canvas overlay.
  function getTransform() {
    if (!canvasEl) return null;
    const rect = canvasEl.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const pyw = pythonFrameW || 1920;
    const pyh = pythonFrameH || 1080;
    const eAR = rect.width / rect.height;
    const vAR = pyw / pyh;
    let rendW, rendH, ox, oy;
    if (vAR > eAR) {
      rendW = rect.width; rendH = rect.width / vAR;
      ox = 0;             oy = (rect.height - rendH) / 2;
    } else {
      rendH = rect.height; rendW = rect.height * vAR;
      ox = (rect.width - rendW) / 2; oy = 0;
    }
    return { ox, oy, sx: rendW / pyw, sy: rendH / pyh, rect };
  }

  function getHandlePositions(roi) {
    if (!roi || roi.length < 4) return [];
    const [x1, y1, x2, y2] = roi;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return [
      { id: "tl", fx: x1, fy: y1, cursor: "nw-resize" },
      { id: "tr", fx: x2, fy: y1, cursor: "ne-resize" },
      { id: "bl", fx: x1, fy: y2, cursor: "sw-resize" },
      { id: "br", fx: x2, fy: y2, cursor: "se-resize" },
      { id: "t",  fx: mx, fy: y1, cursor: "n-resize"  },
      { id: "b",  fx: mx, fy: y2, cursor: "s-resize"  },
      { id: "l",  fx: x1, fy: my, cursor: "w-resize"  },
      { id: "r",  fx: x2, fy: my, cursor: "e-resize"  },
    ];
  }

  function hitTest(clientX, clientY, roi) {
    const t = getTransform();
    if (!t || !roi || roi.length < 4) return null;
    const cx = clientX - t.rect.left;
    const cy = clientY - t.rect.top;
    for (const h of getHandlePositions(roi)) {
      const hcx = t.ox + h.fx * t.sx;
      const hcy = t.oy + h.fy * t.sy;
      if (Math.hypot(cx - hcx, cy - hcy) <= HANDLE_HIT_RADIUS) {
        return { handle: h.id, cursor: h.cursor };
      }
    }
    // Interior → move
    const cx1 = t.ox + roi[0] * t.sx, cy1 = t.oy + roi[1] * t.sy;
    const cx2 = t.ox + roi[2] * t.sx, cy2 = t.oy + roi[3] * t.sy;
    if (cx >= cx1 && cx <= cx2 && cy >= cy1 && cy <= cy2)
      return { handle: "move", cursor: "move" };
    return null;
  }

  function applyDrag(roi, handle, dx, dy) {
    let [x1, y1, x2, y2] = roi;
    const MIN = 4, W = pythonFrameW || 1920, H = pythonFrameH || 1080;
    if      (handle === "tl")   { x1 += dx; y1 += dy; }
    else if (handle === "tr")   { x2 += dx; y1 += dy; }
    else if (handle === "bl")   { x1 += dx; y2 += dy; }
    else if (handle === "br")   { x2 += dx; y2 += dy; }
    else if (handle === "t")    { y1 += dy; }
    else if (handle === "b")    { y2 += dy; }
    else if (handle === "l")    { x1 += dx; }
    else if (handle === "r")    { x2 += dx; }
    else if (handle === "move") { x1 += dx; x2 += dx; y1 += dy; y2 += dy; }
    x1 = Math.max(0, Math.min(x1, W - MIN));
    x2 = Math.max(x1 + MIN, Math.min(x2, W));
    y1 = Math.max(0, Math.min(y1, H - MIN));
    y2 = Math.max(y1 + MIN, Math.min(y2, H));
    return [Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)];
  }

  function updateCurrentRoi(roi) {
    if (wizardStep === "screens") {
      const sn = SCREEN_NAMES[screenIdx];
      if (activeRoiKey === "primary") {
        tells = tells.map(t => t.screen === sn ? { ...t, roi } : t);
      } else if (activeRoiKey === "alt") {
        tells = tells.map(t => t.screen === sn ? { ...t, alt_roi: roi } : t);
      } else if (activeRoiKey.startsWith("and_")) {
        const idx = parseInt(activeRoiKey.slice(4));
        tells = tells.map(t => {
          if (t.screen !== sn) return t;
          const newRA = (t.required_also ?? []).map((ra, i) => i === idx ? { ...ra, roi } : ra);
          return { ...t, required_also: newRA };
        });
      }
    } else if (wizardStep === "selection") {
      const k = SELECTION_ROIS[selectionIdx]?.key;
      if (k) rois = { ...rois, [k]: roi };
    } else if (wizardStep === "hud") {
      const k = HUD_ROIS[hudIdx]?.key;
      if (k) rois = { ...rois, [k]: roi };
    }
  }

  function saveCurrentRoi(roi) {
    if (wizardStep === "screens") {
      const sn = SCREEN_NAMES[screenIdx];  // canonical screen; Python propagates to aliases
      if (activeRoiKey === "primary") {
        send({ type: "update_tell", screen: sn, roi });
      } else if (activeRoiKey === "alt") {
        send({ type: "update_tell", screen: sn, alt_roi: roi });
      } else if (activeRoiKey.startsWith("and_")) {
        const idx = parseInt(activeRoiKey.slice(4));
        const tell = tells.find(t => t.screen === sn);
        const requiredAlsoRois = (tell?.required_also ?? []).map((ra, i) => i === idx ? roi : ra.roi);
        send({ type: "update_tell", screen: sn, required_also_rois: requiredAlsoRois });
      }
    } else if (wizardStep === "selection") {
      const k   = SELECTION_ROIS[selectionIdx]?.key;
      const cfk = SELECTION_ROI_CONFIG_KEYS[k];
      if (cfk) send({ type: "update_config", key: cfk, value: roi });
    } else if (wizardStep === "hud") {
      const k   = HUD_ROIS[hudIdx]?.key;
      const cfk = HUD_ROI_CONFIG_KEYS[k];
      if (cfk) send({ type: "update_config", key: cfk, value: roi });
    }
  }

  // ── Canvas event handlers ─────────────────────────────────────────────────────
  function onCanvasMouseDown(e) {
    const roi = getCurrentRoi();
    const hit = roi ? hitTest(e.clientX, e.clientY, roi) : null;
    if (hit) {
      const t = getTransform();
      dragging       = true;
      dragHandle     = hit.handle;
      dragStartRoi   = [...roi];
      dragStartMouse = {
        x: (e.clientX - t.rect.left - t.ox) / t.sx,
        y: (e.clientY - t.rect.top  - t.oy) / t.sy,
      };
      e.preventDefault();
      return;
    }
    // Click on a non-active ROI to select it (screens step only)
    if (wizardStep === "screens") {
      const tell = tells.find(t => t.screen === SCREEN_NAMES[screenIdx]);
      for (const roiEntry of getAllRoisForTell(tell)) {
        if (roiEntry.key === activeRoiKey || !roiEntry.roi) continue;
        if (hitTest(e.clientX, e.clientY, roiEntry.roi)) {
          activeRoiKey = roiEntry.key;
          syncThreshToScreen();
          hoveredHandle = null;
          drawRoi();
          e.preventDefault();
          return;
        }
      }
    }
  }

  function onCanvasMouseMove(e) {
    const roi = getCurrentRoi();
    if (!dragging) {
      const hit = roi ? hitTest(e.clientX, e.clientY, roi) : null;
      const nh  = hit?.handle ?? null;
      if (nh !== hoveredHandle) { hoveredHandle = nh; drawRoi(); }
      if (canvasEl) canvasEl.style.cursor = hit?.cursor ?? "default";
      return;
    }
    const t = getTransform();
    if (!t) return;
    const dx = (e.clientX - t.rect.left - t.ox) / t.sx - dragStartMouse.x;
    const dy = (e.clientY - t.rect.top  - t.oy) / t.sy - dragStartMouse.y;
    const nr = applyDrag(dragStartRoi, dragHandle, dx, dy);
    updateCurrentRoi(nr);
    drawRoi();
  }

  function onWindowMouseUp() {
    if (!dragging) return;
    dragging = false;
    const roi = getCurrentRoi();
    if (roi) saveCurrentRoi(roi);
    dragHandle = null; dragStartRoi = null; dragStartMouse = null;
  }

  function _drawOneRoi(ctx, t, roi, color, showHandles) {
    if (!roi || roi.length < 4) return;
    const cx1 = t.ox + roi[0] * t.sx, cy1 = t.oy + roi[1] * t.sy;
    const cw  = (roi[2] - roi[0]) * t.sx, ch = (roi[3] - roi[1]) * t.sy;
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.lineWidth   = 4;
    ctx.setLineDash([]);
    ctx.strokeRect(cx1, cy1, cw, ch);
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2;
    ctx.setLineDash([7, 4]);
    ctx.strokeRect(cx1, cy1, cw, ch);
    ctx.setLineDash([]);
    if (showHandles) {
      for (const h of getHandlePositions(roi)) {
        const hcx = t.ox + h.fx * t.sx;
        const hcy = t.oy + h.fy * t.sy;
        const r   = 5;
        const active = hoveredHandle === h.id || (dragging && dragHandle === h.id);
        ctx.fillStyle   = active ? "#7eb8f7" : color;
        ctx.strokeStyle = "rgba(0,0,0,0.85)";
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.rect(hcx - r, hcy - r, r * 2, r * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
  }

  const ROI_COLORS = { primary: "#ffffff", and: "#ffcc00", or: "#00ccff" };

  function drawRoi() {
    if (!canvasEl) return;
    const t = getTransform();
    if (!t) return;
    canvasEl.width  = t.rect.width;
    canvasEl.height = t.rect.height;
    const ctx = canvasEl.getContext("2d");
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);

    if (wizardStep === "screens") {
      const tell = tells.find(tell => tell.screen === SCREEN_NAMES[screenIdx]);
      const allRois = getAllRoisForTell(tell);
      // Draw inactive ROIs first (no handles), then active on top (with handles)
      for (const roiEntry of allRois) {
        if (roiEntry.key === activeRoiKey) continue;
        _drawOneRoi(ctx, t, roiEntry.roi, ROI_COLORS[roiEntry.type] ?? "#ffffff", false);
      }
      const activeEntry = allRois.find(r => r.key === activeRoiKey);
      if (activeEntry) {
        _drawOneRoi(ctx, t, activeEntry.roi, ROI_COLORS[activeEntry.type] ?? "#ffffff", true);
      }
      return;
    }

    const roi = getCurrentRoi();
    _drawOneRoi(ctx, t, roi, "#ffffff", true);
  }

  let _pauseIntent = "";

  // Release the Python camera and open Windows camera settings.
  // Used when browser gets "busy" — Python keeps the camera until the user
  // explicitly releases it so they can see the Python feed working first.
  function releaseAndOpenSettings() {
    _pauseIntent = "open_settings";
    trackerCameraPaused = true;
    send({ type: "pause_camera" });
    invoke("open_url", { url: "ms-settings:camera" }).catch(() => {});
  }

  // Retry: always Python-first. Stop browser, ask Python to reopen and verify
  // frames, then browser tries once Python confirms.
  async function retryNow() {
    stopCamera();
    pythonCameraStatus = "opening";
    engineFrame = null;
    send({ type: "open_camera" });
    // Browser will start via the camera_status handler once Python confirms ok.
  }

  // ── Engine poll (Python camera feed on camera step) ──────────────────────────
  function startEnginePoll() {
    if (_enginePollTimer) return;
    _enginePollTimer = setInterval(() => {
      if (trackerConnected) send({ type: "capture_frame", scale: 0.5 });
    }, 500);
  }

  function stopEnginePoll() {
    if (_enginePollTimer) { clearInterval(_enginePollTimer); _enginePollTimer = null; }
    engineFrame = null;
  }

  // ── ROI preview poll (screens/selection/hud steps) ────────────────────────────
  function startRoiPoll() {
    if (_roiPollTimer) return;
    _roiPollTimer = setInterval(() => {
      if (!trackerConnected || !wizardOpen) return;
      if (wizardStep === "screens") {
        send({ type: "test_template", screen: SCREEN_NAMES[screenIdx], roi_key: activeRoiKey });
      } else {
        const roi = getCurrentRoi();
        if (roi) send({ type: "get_roi_preview", roi, binary_thresh: currentBinaryThresh });
      }
    }, 1000);
  }

  function stopRoiPoll() {
    if (_roiPollTimer) { clearInterval(_roiPollTimer); _roiPollTimer = null; }
    liveRoiCrop = null;
  }

  function onThreshChange() {
    if (wizardStep === "screens" && trackerConnected) {
      const sn = SCREEN_NAMES[screenIdx];
      if (activeRoiKey === "primary") {
        tells = tells.map(t => t.screen === sn ? { ...t, binary_thresh: currentBinaryThresh } : t);
        send({ type: "update_tell", screen: sn, binary_thresh: currentBinaryThresh });
      } else if (activeRoiKey === "alt") {
        tells = tells.map(t => t.screen === sn ? { ...t, alt_binary_thresh: currentBinaryThresh } : t);
        send({ type: "update_tell", screen: sn, alt_binary_thresh: currentBinaryThresh });
      } else if (activeRoiKey.startsWith("and_")) {
        const idx = parseInt(activeRoiKey.slice(4));
        tells = tells.map(t => {
          if (t.screen !== sn) return t;
          const newRA = (t.required_also ?? []).map((ra, i) =>
            i === idx ? { ...ra, thresh: currentBinaryThresh } : ra
          );
          return { ...t, required_also: newRA };
        });
        const tell = tells.find(t => t.screen === sn);
        const requiredAlsoThresh = (tell?.required_also ?? []).map(ra => ra.thresh ?? 170);
        send({ type: "update_tell", screen: sn, required_also_thresh: requiredAlsoThresh });
      }
      send({ type: "test_template", screen: sn, roi_key: activeRoiKey });
    }
    // For selection/hud the next poll will pick up currentBinaryThresh automatically
  }

  // ── Wizard controls ───────────────────────────────────────────────────────────
  async function openWizard() {
    wizardOpen   = true;
    wizardStep   = "welcome";
    screenIdx    = 0; selectionIdx = 0; hudIdx = 0;
    currentScore = null;
    await loadBrowserDevices();
    if (setupComplete) {
      // Re-run setup: Python already has camera open from startup — start browser too.
      await startCamera(selectedBrowserDeviceId || undefined);
    }
    // First-time: don't touch the camera yet; Python opens first on the Camera step.
  }

  function closeWizard() {
    stopCamera();
    stopEnginePoll();
    stopRoiPoll();
    if (trackerCameraPaused) {
      send({ type: "resume_camera" });
      trackerCameraPaused = false;
    }
    wizardOpen = false;
  }

  function completeSetup() {
    send({ type: "mark_setup_complete" });
    setupComplete = true;
    closeWizard();
  }

  function goStep(step) {
    wizardStep    = step;
    screenIdx     = 0; selectionIdx = 0; hudIdx = 0;
    currentScore  = null;
    templateImg   = null;
    liveCropImg   = null;
    liveRoiCrop   = null;
    hoveredHandle = null;
    activeRoiKey  = "primary";
    syncThreshToScreen();
    if (step === "camera") {
      if (!setupComplete) {
        // First-time: ask Python to open camera first; browser opens after camera_status ok.
        pythonCameraStatus = "idle";
        engineFrame = null;
        send({ type: "open_camera" });
      } else if (cameraStatus === "idle") {
        // Re-run setup: Python already has camera; open browser directly.
        startCamera(selectedBrowserDeviceId || undefined);
      }
    }
  }

  function addRequiredAlso() {
    const sn  = SCREEN_NAMES[screenIdx];
    const roi = [935, 515, 985, 565];   // center of 1920×1080, 50×50
    send({ type: "add_required_also", screen: sn, roi });
    activeRoiKey = "and_0";
    syncThreshToScreen();
  }

  function removeRequiredAlso(index) {
    send({ type: "remove_required_also", screen: SCREEN_NAMES[screenIdx], index });
    activeRoiKey = "primary";
  }

  function addAlt() {
    const sn  = SCREEN_NAMES[screenIdx];
    const roi = [935, 515, 985, 565];   // center of 1920×1080, 50×50
    send({ type: "add_alt", screen: sn, roi });
    activeRoiKey = "alt";
    syncThreshToScreen();
  }

  function removeAlt() {
    send({ type: "remove_alt", screen: SCREEN_NAMES[screenIdx] });
    activeRoiKey = "primary";
  }

  function testScreen() {
    currentScore = null;
    liveCropImg  = null;
    send({ type: "test_template", screen: SCREEN_NAMES[screenIdx], roi_key: activeRoiKey });
  }

  function captureScreen() {
    capturingTemplate = true;
    currentScore = null;
    send({ type: "capture_template", screen: SCREEN_NAMES[screenIdx], roi_key: activeRoiKey });
  }

  function prevItem() {
    currentScore = null; liveCropImg = null; liveRoiCrop = null;
    hoveredHandle = null; activeRoiKey = "primary";
    if (wizardStep === "screens") {
      if (screenIdx > 0) screenIdx--; else goStep("camera");
    } else if (wizardStep === "selection") {
      if (selectionIdx > 0) selectionIdx--; else goStep("screens");
    } else if (wizardStep === "hud") {
      if (hudIdx > 0) hudIdx--; else goStep("selection");
    }
    syncThreshToScreen();
  }

  function nextItem() {
    currentScore = null; liveCropImg = null; liveRoiCrop = null;
    hoveredHandle = null; activeRoiKey = "primary";
    if (wizardStep === "screens") {
      if (screenIdx < SCREEN_NAMES.length - 1) screenIdx++; else goStep("selection");
    } else if (wizardStep === "selection") {
      if (selectionIdx < SELECTION_ROIS.length - 1) selectionIdx++; else goStep("hud");
    } else if (wizardStep === "hud") {
      if (hudIdx < HUD_ROIS.length - 1) hudIdx++; else goStep("done");
    }
    syncThreshToScreen();
  }

  // ── Device / update ───────────────────────────────────────────────────────────
  function handleDeviceChange(e) {
    const v = e.target.value;
    send({ type: "update_config", key: "camera_device", value: v });
    configuredDevice = v;
    restartNeeded = true;
  }

  async function handleCameraDeviceChange(e) {
    selectedBrowserDeviceId = e.target.value;
    // Derive the Python device name from the browser label (strip vendor:product suffix)
    const chosen = browserDevices.find(d => d.deviceId === selectedBrowserDeviceId);
    if (chosen && devices.length > 0) {
      const cleanLabel = chosen.label.replace(/\s*\([0-9a-f:]+\)\s*$/i, "").trim();
      const match = devices.find(d =>
        d.toLowerCase() === cleanLabel.toLowerCase() ||
        d.toLowerCase().includes(cleanLabel.toLowerCase()) ||
        cleanLabel.toLowerCase().includes(d.toLowerCase())
      );
      const pythonDevice = match ?? cleanLabel;
      if (pythonDevice !== configuredDevice) {
        configuredDevice = pythonDevice;
        send({ type: "update_config", key: "camera_device", value: pythonDevice });
        restartNeeded = true;
      }
    }
    if (!setupComplete && wizardStep === "camera") {
      // First-time: stop browser, let Python reopen with new device first.
      stopCamera();
      pythonCameraStatus = "idle";
      engineFrame = null;
      send({ type: "open_camera" });
    } else {
      await startCamera(selectedBrowserDeviceId);
    }
  }

  async function restartTracker() {
    restartNeeded    = false;
    devices          = [];
    trackerConnected = false;
    await invoke("restart_tracker");
  }

  async function applyUpdate() {
    if (pendingUpdate) await pendingUpdate.install();
  }

  async function checkForUpdate() {
    try {
      const u = await check();
      if (!u) return;
      pendingUpdate = u; updateVersion = u.version;
      await u.download(ev => {
        if (ev.event === "Started")        { downloadTotal = ev.data.contentLength ?? 0; downloadReceived = 0; }
        else if (ev.event === "Progress")  { downloadReceived += ev.data.chunkLength; }
        else if (ev.event === "Finished")  { updateReady = true; }
      });
    } catch { /* silent */ }
  }

  onMount(async () => {
    appWindow = getCurrentWindow();
    version = await getVersion();
    await invoke("start_tracker");
    unlisten = await listen("tracker-event", ev => {
      try { handleMsg(JSON.parse(ev.payload)); }
      catch { pushLog(String(ev.payload)); }
    });
    setInterval(() => { _tick++; }, 1000);
    checkForUpdate();
    window.addEventListener("mouseup", onWindowMouseUp);
  });

  onDestroy(() => {
    if (unlisten) unlisten();
    stopCamera();
    stopRoiPoll();
    window.removeEventListener("mouseup", onWindowMouseUp);
    if (trackerCameraPaused) send({ type: "resume_camera" });
  });

  $: if (videoEl) videoEl.srcObject = videoStream ?? null;

  afterUpdate(() => { if (wizardOpen) drawRoi(); });

  // ── Reactive computeds ────────────────────────────────────────────────────────
  $: currentScreenName  = SCREEN_NAMES[screenIdx]  ?? "";
  $: currentScreenLabel = SCREEN_LABELS[currentScreenName] ?? currentScreenName;
  $: currentScreenHint  = SCREEN_HINTS[currentScreenName]  ?? "";
  $: selItem = SELECTION_ROIS[selectionIdx];
  $: hudItem = HUD_ROIS[hudIdx];
  $: cameraOk = cameraStatus === "ok";
  $: pythonCameraOk = pythonCameraStatus === "ok" && engineFrame !== null && !trackerCameraPaused;
  $: bothCamerasOk = cameraOk && pythonCameraOk;

  // Start/stop the engine poll based on whether the camera step is active.
  $: if (wizardOpen && wizardStep === "camera") {
    startEnginePoll();
  } else {
    stopEnginePoll();
  }

  // Start/stop the ROI preview poll on editable wizard steps.
  $: if (wizardOpen && ["screens", "selection", "hud"].includes(wizardStep)) {
    startRoiPoll();
  } else {
    stopRoiPoll();
  }

  // Sync threshold slider to the current screen's tell.
  // Called explicitly on navigation — NOT reactive — so template_score responses
  // updating `tells` cannot reset the slider while the user is dragging it.
  function syncThreshToScreen() {
    if (wizardStep === "screens") {
      const _t = tells.find(t => t.screen === SCREEN_NAMES[screenIdx]);
      if (!_t) { currentBinaryThresh = 170; return; }
      if (activeRoiKey === "primary") {
        // binary_thresh === null means Otsu (slider hidden); fall back to 170 if not loaded yet
        currentBinaryThresh = _t.binary_thresh ?? 170;
      } else if (activeRoiKey === "alt") {
        currentBinaryThresh = _t.alt_binary_thresh ?? 170;
      } else if (activeRoiKey.startsWith("and_")) {
        const idx = parseInt(activeRoiKey.slice(4));
        currentBinaryThresh = _t.required_also?.[idx]?.thresh ?? 170;
      } else {
        currentBinaryThresh = 170;
      }
    } else {
      // For selection/hud the slider is just for live preview; reset to 170 per-item.
      currentBinaryThresh = 170;
    }
  }

  $: currentTell = tells.find(t => t.screen === SCREEN_NAMES[screenIdx]) ?? null;

  // Fetch template image whenever the screen or active ROI changes in the screens step.
  $: if (wizardOpen && wizardStep === "screens" && trackerConnected) {
    templateImg = null;
    liveCropImg = null;
    // Reference both so Svelte re-runs when either changes
    const _scr = SCREEN_NAMES[screenIdx];
    const _key = activeRoiKey;
    send({ type: "get_template_images", screen: _scr, roi_key: _key });
  }

  // Keep browser device selection in sync with the tracker's configured device.
  $: if (browserDevices.length > 0 && configuredDevice) {
    const lower = configuredDevice.toLowerCase();
    const match = browserDevices.find(d => d.label.toLowerCase().includes(lower));
    if (match && match.deviceId !== selectedBrowserDeviceId) {
      selectedBrowserDeviceId = match.deviceId;
    }
  }
</script>

<div class="app">

  <!-- ── Status bar / custom titlebar ────────────────────────────────── -->
  <header class="status-bar" data-tauri-drag-region>
    <div class="brand" data-tauri-drag-region>
      <span class="brand-name">MKW Tracker</span>
      {#if version}<span class="brand-ver">v{version}</span>{/if}
    </div>
    <div class="health" data-tauri-drag-region>
      <span class="hb-dot" style="background:{statusDot}"></span>
      {#if trackerConnected && backendAlive}
        <span class="hb-fps">{backendFps} fps</span>
        <span class="hb-sep">·</span>
        <span class="hb-screen">{backendScreen}</span>
      {:else if trackerConnected}
        <span class="hb-warn">backend stalled</span>
      {:else}
        <span class="hb-idle">connecting…</span>
      {/if}
    </div>
    <div class="hdr-actions">
      {#if wizardOpen}
        {#if setupComplete}
          <button class="btn-hdr btn-close" on:click={closeWizard}>✕ Close Setup</button>
        {/if}
      {:else}
        <button class="btn-hdr btn-setup" on:click={openWizard}>⚙ Setup</button>
      {/if}
    </div>
    <div class="win-controls">
      <button class="win-btn" on:click={winMinimize} title="Minimize">&#x2013;</button>
      <button class="win-btn" on:click={winToggleMaximize} title="Maximize">&#x25a1;</button>
      <button class="win-btn win-btn-close" on:click={winClose} title="Close">&#x2715;</button>
    </div>
  </header>

  <!-- ── Normal view ─────────────────────────────────────────────────── -->
  {#if !wizardOpen}
    <div class="normal-view">

      {#if devices.length > 0}
        <div class="device-row">
          <label for="dev-sel">Input</label>
          <select id="dev-sel" on:change={handleDeviceChange}>
            {#if !configuredDevice}
              <option value="" disabled selected>— pick a device —</option>
            {/if}
            {#each devices as d}
              <option value={d} selected={d === configuredDevice}>{d}</option>
            {/each}
          </select>
          {#if restartNeeded}
            <button class="btn-sm" on:click={restartTracker}>Restart</button>
          {/if}
        </div>
      {/if}

      {#if updateVersion}
        <div class="update-strip">
          <span class="upd-label">
            {updateReady ? `v${updateVersion} ready` : `v${updateVersion} ${downloadPercent !== null ? `${downloadPercent}%` : "…"}`}
          </span>
          {#if !updateReady}
            <div class="upd-track">
              <div class="upd-fill" style="width:{downloadPercent ?? 0}%"></div>
            </div>
          {:else}
            <button class="btn-sm" on:click={applyUpdate}>Restart to apply</button>
          {/if}
        </div>
      {/if}

      <div class="log" bind:this={logEl}>
        {#each logs as line}
          <div class="log-line">{line}</div>
        {/each}
        {#if logs.length === 0}
          <div class="log-empty">Waiting for events…</div>
        {/if}
      </div>

    </div>

  <!-- ── Wizard view ──────────────────────────────────────────────────── -->
  {:else}
    <div class="wizard">

      <nav class="wiz-tabs">
        {#each STEPS as s}
          <button class="wiz-tab" class:active={wizardStep === s}
            on:click={() => goStep(s)}>{STEP_LABELS[s]}</button>
        {/each}
      </nav>

      <div class="wiz-body">

        <!-- WELCOME -->
        {#if wizardStep === "welcome"}
          <div class="step-centred">
            <h2>First-Time Setup</h2>
            <p>This wizard calibrates the tracker for your capture card, display settings, language, and brightness.</p>
            <p>You will verify or recapture templates for:</p>
            <ul>
              <li><strong>17 game screens</strong> — menu and race state detection</li>
              <li><strong>4 selection areas</strong> — character, kart, costume, and course names</li>
              <li><strong>6 HUD areas</strong> — laps, coins, timestamp, finish, mushrooms</li>
            </ul>
            <p>Keep your game open during setup. A live preview of your camera feed appears throughout.</p>
            <button class="btn-primary btn-lg" on:click={() => goStep("camera")}>Start Setup →</button>
          </div>

        <!-- CAMERA -->
        {:else if wizardStep === "camera"}
          <div class="cam-setup">
            <div class="cam-dual">

              <!-- Browser feed -->
              <div class="cam-pane">
                <div class="cam-pane-label">Browser / App Input</div>
                <div class="preview-wrapper">
                  {#if cameraOk}
                    <video bind:this={videoEl} autoplay playsinline muted class="preview-video"></video>
                  {:else if cameraStatus === "requesting"}
                    <div class="preview-placeholder">
                      <span class="spin">◌</span><span>Opening…</span>
                    </div>
                  {:else if cameraStatus === "busy"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Blocked — device in exclusive use</span>
                    </div>
                  {:else if cameraStatus === "error"}
                    <div class="preview-placeholder">
                      <span class="preview-icon">⊗</span>
                      <span class="cam-pane-err-label">Camera error</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder">
                      <span class="spin">◌</span><span>Waiting…</span>
                    </div>
                  {/if}
                </div>
                <div class="cam-pane-status"
                     class:cam-status-ok={cameraOk}
                     class:cam-status-err={cameraStatus === "busy" || cameraStatus === "error"}>
                  <span class="cam-dot"></span>
                  {cameraOk ? "Connected" : cameraStatus === "requesting" ? "Opening…" : cameraStatus === "busy" ? "Blocked" : cameraStatus === "error" ? "Error" : "Waiting"}
                </div>
              </div>

              <!-- Python engine feed -->
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
                      <span class="cam-pane-err-label">Can't access device{pythonCameraError ? `: ${pythonCameraError}` : ""}</span>
                    </div>
                  {:else}
                    <div class="preview-placeholder">
                      <span class="spin">◌</span>
                      <span>{pythonCameraStatus === "opening" ? "Opening and verifying…" : !trackerConnected ? "Connecting to engine…" : "Waiting for camera…"}</span>
                    </div>
                  {/if}
                </div>
                <div class="cam-pane-status"
                     class:cam-status-ok={pythonCameraOk}
                     class:cam-status-err={pythonCameraStatus === "error"}
                     class:cam-status-warn={trackerCameraPaused}>
                  <span class="cam-dot"></span>
                  {pythonCameraOk ? "Connected" : trackerCameraPaused ? "Released" : pythonCameraStatus === "error" ? "Error" : pythonCameraStatus === "opening" ? "Opening…" : "Waiting"}
                </div>
              </div>

            </div><!-- /cam-dual -->

            <!-- Controls below both panes -->
            <div class="cam-below">
              {#if browserDevices.length > 0}
                <div class="device-row">
                  <label for="wiz-cam">Camera</label>
                  <select id="wiz-cam" on:change={handleCameraDeviceChange}>
                    {#each browserDevices as d}
                      <option value={d.deviceId} selected={d.deviceId === selectedBrowserDeviceId}>
                        {d.label || `Camera ${d.deviceId.slice(0,6)}…`}
                      </option>
                    {/each}
                  </select>
                  {#if restartNeeded}
                    <button class="btn-sm" on:click={restartTracker}>Restart</button>
                  {/if}
                </div>
              {/if}

              <!-- Troubleshoot: context-sensitive, only shown when relevant -->
              {#if pythonCameraOk && cameraStatus === "busy"}
                <!-- Python works, browser blocked: device doesn't allow shared access -->
                <div class="cam-troubleshoot">
                  <span class="cam-troubleshoot-title">Your capture card is blocking simultaneous access</span>
                  <p class="cam-troubleshoot-body">
                    The engine feed above confirms the device works. Windows is preventing the app from
                    opening it at the same time. This is a one-time fix:
                  </p>
                  <ol class="cam-steps">
                    <li>Click <strong>Release engine &amp; open settings →</strong> below</li>
                    <li>In the settings page, find your capture card and click it</li>
                    <li>Scroll to <strong>Advanced camera options</strong> → <strong>Edit</strong></li>
                    <li>Turn on <strong>"Allow multiple apps to use camera at the same time"</strong></li>
                    <li>Return here and click <strong>Retry</strong></li>
                  </ol>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={releaseAndOpenSettings}>
                      Release engine &amp; open settings →
                    </button>
                  </div>
                </div>
              {:else if trackerCameraPaused}
                <!-- Python released, waiting for user to retry after settings change -->
                <div class="cam-troubleshoot cam-troubleshoot-neutral">
                  <span class="cam-troubleshoot-title">Engine camera released</span>
                  <p class="cam-troubleshoot-body">
                    Change the Windows setting if you haven't yet, then click <strong>Retry</strong>.
                    The engine will reopen first, then the app feed will follow.
                  </p>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={retryNow}>Retry</button>
                  </div>
                </div>
              {:else if pythonCameraStatus === "error" || cameraStatus === "error"}
                <!-- One or both hard-errored -->
                <div class="cam-troubleshoot">
                  <span class="cam-troubleshoot-title">Can't access capture card</span>
                  <p class="cam-troubleshoot-body">
                    Check that your capture card is connected and not in use by another app.
                    {#if pythonCameraError}<span class="cam-err-detail">{pythonCameraError}</span>{/if}
                  </p>
                  <div class="cam-troubleshoot-actions">
                    <button class="btn-primary" on:click={retryNow}>Retry</button>
                  </div>
                </div>
              {/if}

              <div class="cam-actions">
                <p class="hint">Both feeds must show your capture card output before you can continue.</p>
                <button class="btn-primary" disabled={!bothCamerasOk}
                  on:click={() => goStep("screens")}>
                  Next: Screen Detection →
                </button>
              </div>
            </div><!-- /cam-below -->
          </div><!-- /cam-setup -->

        <!-- SCREENS -->
        {:else if wizardStep === "screens"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={videoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown}
                    on:mousemove={onCanvasMouseMove}
                  ></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem"
                      on:click={() => goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to reposition ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{screenIdx + 1} / {SCREEN_NAMES.length}</span>
                <h3>{currentScreenLabel}</h3>
              </div>
              <p class="hint">{currentScreenHint}</p>
              {#if TELL_GROUP_NOTES[SCREEN_NAMES[screenIdx]]}
                <p class="hint tell-group-note">{TELL_GROUP_NOTES[SCREEN_NAMES[screenIdx]]}</p>
              {/if}
              {#if currentTell}
                {@const allRois = getAllRoisForTell(currentTell)}
                <div class="roi-tabs">
                  {#each allRois as roiEntry}
                    <button
                      class="roi-tab"
                      class:active={activeRoiKey === roiEntry.key}
                      class:roi-tab-and={roiEntry.type === "and"}
                      class:roi-tab-or={roiEntry.type === "or"}
                      on:click={() => { activeRoiKey = roiEntry.key; syncThreshToScreen(); drawRoi(); }}
                    >{roiEntry.label}</button>
                    {#if activeRoiKey === roiEntry.key && roiEntry.type !== "primary"}
                      <button class="roi-tab-remove"
                        title="Remove this ROI"
                        on:click={() => roiEntry.type === "and"
                          ? removeRequiredAlso(parseInt(roiEntry.key.slice(4)))
                          : removeAlt()}>×</button>
                    {/if}
                  {/each}
                  {#if !currentTell.required_also?.length}
                    <button class="roi-tab roi-tab-add roi-tab-and"
                      title="Add an AND condition — both ROIs must match"
                      on:click={addRequiredAlso}>+ AND</button>
                  {/if}
                  {#if !currentTell.alt_image_path}
                    <button class="roi-tab roi-tab-add roi-tab-or"
                      title="Add an OR alternative — either ROI can match"
                      on:click={addAlt}>+ OR Alt</button>
                  {/if}
                </div>
                {@const activeRoiEntry = allRois.find(r => r.key === activeRoiKey)}
                {#if activeRoiEntry?.roi}
                  {@const r = activeRoiEntry.roi}
                  <div class="roi-chip">
                    ({r[0]}, {r[1]}) → ({r[2]}, {r[3]})
                    <span class="roi-size">{r[2]-r[0]} × {r[3]-r[1]} px</span>
                  </div>
                {/if}
              {/if}
              {#if currentScore}
                <div class="score-box" class:good={currentScore.matched} class:bad={!currentScore.matched}>
                  <span class="score-icon">{currentScore.matched ? "✓" : "✗"}</span>
                  <span class="score-val">{currentScore.score.toFixed(3)}</span>
                  <span class="score-thr">/ {currentScore.threshold.toFixed(2)}</span>
                  <span class="score-lbl">{currentScore.matched ? "Detected" : "Not detected"}</span>
                </div>
              {:else if capturingTemplate}
                <p class="score-msg">Saving new template…</p>
              {:else}
                <p class="score-msg">Updating live score…</p>
              {/if}
              <!-- Threshold slider: primary uses tell default (null = Otsu, hidden);
                   AND/OR ROIs always have an independent slider -->
              {#if activeRoiKey !== "primary" || currentTell?.binary_thresh != null}
                <div class="thresh-row">
                  <label class="thresh-label">Binarize</label>
                  <input type="range" min="0" max="255" step="1"
                    bind:value={currentBinaryThresh}
                    on:input={onThreshChange}
                    class="thresh-slider" />
                  <span class="thresh-val">{currentBinaryThresh}</span>
                </div>
              {:else}
                <p class="hint" style="font-size:.65rem">Auto threshold (Otsu)</p>
              {/if}
              <div class="btn-row">
                <button class="btn-secondary" on:click={captureScreen} disabled={capturingTemplate}>
                  {capturingTemplate ? "Saving…" : "Capture New Template"}
                </button>
              </div>
              <p class="capture-note"><strong>Capture</strong> crops the current frame to this ROI and saves it as the new template.</p>

              <!-- Template comparison -->
              <div class="tmpl-compare">
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">Saved Template</div>
                  {#if templateImg}
                    <img src={templateImg} alt="Saved template" class="tmpl-img" />
                  {:else}
                    <div class="tmpl-empty">—</div>
                  {/if}
                </div>
                <div class="tmpl-pane">
                  <div class="tmpl-pane-label">Live ROI Crop</div>
                  {#if liveCropImg}
                    <img src={liveCropImg} alt="Live crop" class="tmpl-img" />
                  {:else}
                    <div class="tmpl-empty">Live…</div>
                  {/if}
                </div>
              </div>
            </div>
          </div>

        <!-- SELECTION -->
        {:else if wizardStep === "selection"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={videoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown}
                    on:mousemove={onCanvasMouseMove}
                  ></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem"
                      on:click={() => goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to adjust ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{selectionIdx + 1} / {SELECTION_ROIS.length}</span>
                <h3>{selItem?.label}</h3>
              </div>
              <p class="hint">{selItem?.hint}</p>
              {#if rois[selItem?.key]}
                {@const r = rois[selItem.key]}
                <div class="roi-chip">
                  ({r[0]}, {r[1]}) → ({r[2]}, {r[3]})
                  <span class="roi-size">{r[2]-r[0]} × {r[3]-r[1]} px</span>
                </div>
              {/if}
              <!-- Threshold slider + live crop -->
              <div class="thresh-row">
                <label class="thresh-label">Binarize</label>
                <input type="range" min="0" max="255" step="1"
                  bind:value={currentBinaryThresh}
                  class="thresh-slider" />
                <span class="thresh-val">{currentBinaryThresh}</span>
              </div>
              <div class="tmpl-pane">
                <div class="tmpl-pane-label">Live Crop</div>
                {#if liveRoiCrop}
                  <img src={liveRoiCrop} alt="Live ROI crop" class="tmpl-img" />
                {:else}
                  <div class="tmpl-empty">Live…</div>
                {/if}
              </div>
            </div>
          </div>

        <!-- HUD -->
        {:else if wizardStep === "hud"}
          <div class="step-two-col">
            <div class="preview-col">
              <div class="preview-wrapper">
                {#if cameraOk}
                  <video bind:this={videoEl} autoplay playsinline muted class="preview-video"></video>
                  <canvas bind:this={canvasEl} class="preview-canvas roi-canvas"
                    on:mousedown={onCanvasMouseDown}
                    on:mousemove={onCanvasMouseMove}
                  ></canvas>
                {:else}
                  <div class="preview-placeholder">
                    <span>Camera unavailable</span>
                    <button class="btn-secondary" style="font-size:.7rem;margin-top:.4rem"
                      on:click={() => goStep("camera")}>← Fix Camera</button>
                  </div>
                {/if}
              </div>
              <p class="preview-cap">Live feed · drag handles to adjust ROI</p>
            </div>
            <div class="info-col">
              <div class="item-header">
                <span class="item-num">{hudIdx + 1} / {HUD_ROIS.length}</span>
                <h3>{hudItem?.label}</h3>
              </div>
              <p class="hint">{hudItem?.hint}</p>
              {#if rois[hudItem?.key]}
                {@const r = rois[hudItem.key]}
                <div class="roi-chip">
                  ({r[0]}, {r[1]}) → ({r[2]}, {r[3]})
                  <span class="roi-size">{r[2]-r[0]} × {r[3]-r[1]} px</span>
                </div>
              {/if}
              <!-- Threshold slider + live crop -->
              <div class="thresh-row">
                <label class="thresh-label">Binarize</label>
                <input type="range" min="0" max="255" step="1"
                  bind:value={currentBinaryThresh}
                  class="thresh-slider" />
                <span class="thresh-val">{currentBinaryThresh}</span>
              </div>
              <div class="tmpl-pane">
                <div class="tmpl-pane-label">Live Crop</div>
                {#if liveRoiCrop}
                  <img src={liveRoiCrop} alt="Live ROI crop" class="tmpl-img" />
                {:else}
                  <div class="tmpl-empty">Live…</div>
                {/if}
              </div>
            </div>
          </div>

        <!-- DONE -->
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

      {#if ["screens", "selection", "hud"].includes(wizardStep)}
        <footer class="wiz-footer">
          <button class="btn-nav" on:click={prevItem}>← Back</button>
          <div class="dot-row">
            {#if wizardStep === "screens"}
              {#each SCREEN_NAMES as _, i}
                <span class="nav-dot" class:active={i === screenIdx}></span>
              {/each}
            {:else if wizardStep === "selection"}
              {#each SELECTION_ROIS as _, i}
                <span class="nav-dot nav-dot-lg" class:active={i === selectionIdx}></span>
              {/each}
            {:else}
              {#each HUD_ROIS as _, i}
                <span class="nav-dot nav-dot-lg" class:active={i === hudIdx}></span>
              {/each}
            {/if}
          </div>
          <button class="btn-nav" on:click={nextItem}>Next →</button>
        </footer>
      {/if}

    </div><!-- /wizard -->
  {/if}

</div><!-- /app -->

<style>
  :global(body) {
    margin: 0;
    background: #0d0d1a;
    color: #e8e8f0;
    font-family: 'Consolas', 'Courier New', monospace;
    height: 100vh;
    overflow: hidden;
    scrollbar-width: thin;
    scrollbar-color: #1e1e2e #05050e;
  }
  :global(::-webkit-scrollbar)       { width: 5px; height: 5px; background: #05050e; }
  :global(::-webkit-scrollbar-track) { background: #05050e; }
  :global(::-webkit-scrollbar-thumb) { background: #1e1e2e; border-radius: 3px; }
  :global(::-webkit-scrollbar-thumb:hover) { background: #2a2a4a; }
  .app { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

  /* ── Status bar ──────────────────────────────────────── */
  .status-bar {
    display: flex; align-items: center; padding: 0 0 0 1rem;
    height: 40px; min-height: 40px;
    background: #08080f; border-bottom: 1px solid #1e1e2e;
    gap: 0.75rem; flex-shrink: 0;
    user-select: none;
  }
  /* ── Custom window controls ──────────────────────────────── */
  .win-controls { display: flex; flex-shrink: 0; margin-left: auto; }
  .win-btn {
    background: transparent; border: none; color: #555;
    width: 46px; height: 40px; font-size: 0.8rem; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.1s, color 0.1s; flex-shrink: 0;
    -webkit-app-region: no-drag;
  }
  .win-btn:hover { background: #1e1e2e; color: #e8e8f0; }
  .win-btn-close:hover { background: #c42b1c; color: #fff; }
  .brand { display: flex; align-items: baseline; gap: 0.4rem; flex-shrink: 0; }
  .brand-name { font-size: 0.88rem; font-weight: bold; color: #7eb8f7; letter-spacing: 0.02em; }
  .brand-ver  { font-size: 0.68rem; color: #444; }
  .health { flex: 1; display: flex; align-items: center; gap: 0.45rem; font-size: 0.72rem; min-width: 0; }
  .hb-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; transition: background 0.6s; }
  .hb-fps    { color: #7eb8f7; }
  .hb-sep    { color: #333; }
  .hb-screen { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hb-warn   { color: #f59e0b; }
  .hb-idle   { color: #444; font-style: italic; }
  .hdr-actions { flex-shrink: 0; -webkit-app-region: no-drag; }
  .btn-hdr {
    background: #111122; border-radius: 4px; padding: 0.22rem 0.65rem;
    font-family: inherit; font-size: 0.72rem; cursor: pointer; white-space: nowrap;
    transition: background 0.15s;
  }
  .btn-setup { color: #7eb8f7; border: 1px solid #2a2a5a; }
  .btn-setup:hover { background: #1a1a3a; }
  .btn-close { color: #888; border: 1px solid #2a2a3a; }
  .btn-close:hover { color: #e8e8f0; background: #1e1e2e; }

  /* ── Normal view ─────────────────────────────────────── */
  .normal-view {
    flex: 1; display: flex; flex-direction: column;
    padding: 0.75rem 1rem; gap: 0.45rem; overflow: hidden; min-height: 0;
  }
  .device-row { display: flex; align-items: center; gap: 0.45rem; font-size: 0.78rem; flex-shrink: 0; }
  .device-row label { color: #666; flex-shrink: 0; }
  select {
    flex: 1; min-width: 0;
    background: #080810; color: #e8e8f0;
    border: 1px solid #1e1e2e; border-radius: 3px;
    padding: 0.18rem 0.3rem; font-family: inherit; font-size: 0.72rem;
  }
  .btn-sm {
    background: #111122; color: #7eb8f7; border: 1px solid #2a2a5a; border-radius: 3px;
    padding: 0.18rem 0.5rem; font-family: inherit; font-size: 0.72rem;
    cursor: pointer; flex-shrink: 0;
  }
  .btn-sm:hover { background: #1a1a3a; }
  .update-strip { display: flex; align-items: center; gap: 0.45rem; font-size: 0.72rem; flex-shrink: 0; }
  .upd-label { color: #4caf50; flex-shrink: 0; }
  .upd-track { flex: 1; height: 3px; background: #111122; border-radius: 2px; overflow: hidden; }
  .upd-fill  { height: 100%; background: #4caf50; transition: width 0.2s; }
  .log {
    flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden;
    background: #05050e; border: 1px solid #1a1a2e; border-radius: 4px;
    padding: 0.4rem 0.5rem; scrollbar-width: thin; scrollbar-color: #1e1e2e #05050e;
  }
  .log-line  { font-size: 0.7rem; color: #7a9db8; white-space: pre-wrap; word-break: break-all; line-height: 1.45; }
  .log-empty { font-size: 0.7rem; color: #333; font-style: italic; }

  /* ── Wizard ──────────────────────────────────────────── */
  .wizard { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
  .wiz-tabs {
    display: flex; flex-shrink: 0; background: #06060c;
    border-bottom: 1px solid #1a1a2e; overflow-x: auto; scrollbar-width: none;
  }
  .wiz-tab {
    background: transparent; color: #555;
    border: none; border-right: 1px solid #111120;
    padding: 0.38rem 0.9rem; font-family: inherit; font-size: 0.72rem;
    cursor: pointer; white-space: nowrap; transition: color 0.15s, background 0.15s;
  }
  .wiz-tab:hover { background: #0d0d1a; color: #999; }
  .wiz-tab.active { background: #0d0d1a; color: #7eb8f7; border-bottom: 2px solid #7eb8f7; margin-bottom: -1px; }
  .wiz-body { flex: 1; overflow: auto; padding: 1rem; min-height: 0; }

  /* Two-column layout */
  .step-two-col { display: flex; gap: 1rem; align-items: flex-start; }
  .preview-col  { flex: 3; min-width: 0; display: flex; flex-direction: column; gap: 0.3rem; }
  .info-col     { flex: 2; min-width: 180px; display: flex; flex-direction: column; gap: 0.65rem; }

  /* Video + canvas */
  .preview-wrapper {
    position: relative; width: 100%; aspect-ratio: 16/9;
    background: #000; border: 1px solid #1a1a2e; border-radius: 4px; overflow: hidden;
  }
  .preview-video { width: 100%; height: 100%; display: block; object-fit: contain; }
  .preview-canvas {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;
  }
  .roi-canvas { pointer-events: auto; }
  .preview-placeholder {
    width: 100%; height: 100%; position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 0.35rem; font-size: 0.78rem; color: #444;
  }
  .preview-icon { font-size: 1.6rem; line-height: 1; }
  .spin { animation: spin 1.2s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .preview-cap { font-size: 0.63rem; color: #333; margin: 0; }

  .cam-steps {
    margin: 0.25rem 0 0.1rem; padding-left: 1.3rem;
    font-size: 0.71rem; color: #666; line-height: 1.8; text-align: left;
  }
  .cam-steps strong { color: #9ab; }
  .cam-retry-now  { padding: 0.22rem 0.55rem; font-size: 0.7rem; }

  .info-col h3  { margin: 0; font-size: 0.9rem; color: #7eb8f7; }
  .item-header  { display: flex; align-items: baseline; gap: 0.5rem; }
  .item-num     { font-size: 0.67rem; color: #444; flex-shrink: 0; }
  .hint         { font-size: 0.73rem; color: #777; margin: 0; line-height: 1.55; }
  .future-note  { color: #444; font-style: italic; }

  .score-box {
    display: flex; align-items: center; gap: 0.4rem;
    padding: 0.4rem 0.55rem; border-radius: 4px; border: 1px solid #2a2a3a; font-size: 0.78rem;
  }
  .score-box.good { border-color: rgba(76,175,80,.5); background: rgba(76,175,80,.07); }
  .score-box.bad  { border-color: rgba(239,68,68,.5); background: rgba(239,68,68,.07); }
  .score-icon { font-size: 0.95rem; }
  .good .score-icon { color: #4caf50; }
  .bad  .score-icon { color: #ef4444; }
  .score-val { font-size: 1rem; font-weight: bold; color: #e8e8f0; }
  .score-thr { color: #444; font-size: 0.7rem; }
  .score-lbl { color: #888; font-size: 0.7rem; margin-left: auto; }
  .score-msg { font-size: 0.72rem; color: #444; font-style: italic; margin: 0; }

  .btn-row { display: flex; gap: 0.5rem; flex-wrap: wrap; }
  .btn-primary {
    background: #142a55; color: #7eb8f7; border: 1px solid #1e4a9e; border-radius: 4px;
    padding: 0.32rem 0.75rem; font-family: inherit; font-size: 0.75rem;
    cursor: pointer; white-space: nowrap; transition: background 0.15s;
  }
  .btn-primary:hover:not(:disabled) { background: #1e3a75; }
  .btn-primary:disabled { opacity: 0.35; cursor: default; }
  .btn-primary.btn-lg { padding: 0.5rem 1.2rem; font-size: 0.88rem; margin-top: 0.75rem; }
  .btn-secondary {
    background: #0d0d1a; color: #888; border: 1px solid #1e1e2e; border-radius: 4px;
    padding: 0.32rem 0.75rem; font-family: inherit; font-size: 0.75rem;
    cursor: pointer; white-space: nowrap; transition: background 0.15s;
  }
  .btn-secondary:hover:not(:disabled) { background: #161626; color: #e8e8f0; }
  .btn-secondary:disabled { opacity: 0.4; cursor: default; }
  .capture-note { font-size: 0.67rem; color: #444; margin: 0; }

  /* Template comparison */
  .tmpl-compare {
    display: flex; gap: 0.5rem; margin-top: 0.25rem;
  }
  .tmpl-pane {
    flex: 1; min-width: 0;
    display: flex; flex-direction: column; gap: 0.25rem;
  }
  .tmpl-pane-label {
    font-size: 0.62rem; color: #444; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .tmpl-img {
    display: block; width: 100%; height: auto;
    border: 1px solid #1a1a2e; border-radius: 3px;
    background: #000; image-rendering: pixelated;
  }
  .tmpl-empty {
    height: 3rem; border: 1px dashed #1a1a2e; border-radius: 3px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.68rem; color: #333; font-style: italic;
  }

  .roi-chip {
    background: #050510; border: 1px solid #1a1a2e; border-radius: 3px;
    padding: 0.28rem 0.5rem; font-size: 0.7rem; color: #5a8ab0;
    display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem;
  }
  .roi-size { color: #444; }
  .tell-group-note { color: #7a7a50; font-style: italic; font-size: 0.66rem; }
  .roi-tabs {
    display: flex; gap: 0.3rem; flex-wrap: wrap; margin-bottom: 0.25rem;
  }
  .roi-tab {
    background: #0a0a18; border: 1px solid #2a2a4a; border-radius: 3px;
    color: #888; padding: 0.18rem 0.5rem; font-family: inherit; font-size: 0.68rem;
    cursor: pointer; transition: background 0.1s, color 0.1s;
  }
  .roi-tab.active { background: #1a1a3a; color: #ffffff; border-color: #5a5a8a; }
  .roi-tab-and.active { color: #ffcc00; border-color: #888830; }
  .roi-tab-or.active  { color: #00ccff; border-color: #308888; }
  .roi-tab:hover:not(.active) { background: #0f0f22; color: #bbb; }
  .roi-tab-add { opacity: 0.55; }
  .roi-tab-add:hover { opacity: 1; }
  .roi-tab-remove {
    background: transparent; border: none; color: #884444; padding: 0 0.25rem;
    font-size: 0.85rem; cursor: pointer; line-height: 1; margin-left: -0.2rem;
    transition: color 0.1s;
  }
  .roi-tab-remove:hover { color: #ff6666; }

  .step-centred { max-width: 580px; margin: 0 auto; padding: 0.5rem 0; }
  .step-centred h2 { color: #7eb8f7; margin: 0 0 0.75rem; font-size: 1.1rem; }
  .step-centred p  { font-size: 0.8rem; color: #888; line-height: 1.65; margin: 0 0 0.65rem; }
  .step-centred ul { font-size: 0.8rem; color: #888; line-height: 1.8; margin: 0 0 0.75rem; padding-left: 1.2rem; }
  .step-centred strong { color: #c8c8e0; }
  .done-check { font-size: 2.5rem; color: #4caf50; margin-bottom: 0.5rem; }

  /* ── Camera dual-pane setup step ────────────────────────────── */
  .cam-setup { display: flex; flex-direction: column; gap: 0.9rem; }
  .cam-dual  { display: flex; gap: 0.75rem; }
  .cam-pane  {
    flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.3rem;
  }
  .cam-pane-label {
    font-size: 0.68rem; color: #555; text-transform: uppercase; letter-spacing: 0.06em;
  }
  .cam-pane-status {
    display: flex; align-items: center; gap: 0.35rem;
    font-size: 0.68rem; color: #444;
  }
  .cam-pane-status .cam-dot {
    width: 7px; height: 7px; border-radius: 50%; background: #333; flex-shrink: 0;
  }
  .cam-status-ok { color: #4caf50; }
  .cam-status-ok .cam-dot { background: #4caf50; }
  .cam-status-err { color: #ef4444; }
  .cam-status-err .cam-dot { background: #ef4444; }
  .cam-pane-err-label { font-size: 0.75rem; color: #666; }

  .cam-below { display: flex; flex-direction: column; gap: 0.7rem; }
  .cam-actions { display: flex; flex-direction: column; gap: 0.35rem; }

  .cam-troubleshoot {
    padding: 0.6rem 0.75rem; border-radius: 4px;
    background: rgba(239,68,68,.05); border: 1px solid rgba(239,68,68,.2);
    display: flex; flex-direction: column; gap: 0.35rem;
  }
  .cam-troubleshoot-title { font-size: 0.75rem; color: #c8c8e0; }
  .cam-troubleshoot-body  { font-size: 0.71rem; color: #666; margin: 0; line-height: 1.55; }
  .cam-troubleshoot-actions { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.2rem; }
  .cam-troubleshoot-neutral { background: rgba(126,184,247,.04); border-color: rgba(126,184,247,.18); }
  .cam-status-warn .cam-dot { background: #888; }
  .cam-status-warn { color: #888; }
  .cam-err-detail { display: block; font-size: 0.67rem; color: #555; margin-top: 0.25rem; font-style: italic; }

  /* Threshold slider */
  .thresh-row {
    display: flex; align-items: center; gap: 0.45rem; flex-shrink: 0;
  }
  .thresh-label { font-size: 0.65rem; color: #555; flex-shrink: 0; }
  .thresh-slider {
    flex: 1; min-width: 0; accent-color: #7eb8f7;
    cursor: pointer; height: 3px;
  }
  .thresh-val { font-size: 0.65rem; color: #7eb8f7; min-width: 2.2em; text-align: right; flex-shrink: 0; }

  .wiz-footer {
    display: flex; align-items: center; padding: 0.45rem 1rem;
    background: #06060c; border-top: 1px solid #1a1a2e; flex-shrink: 0; gap: 0.75rem;
  }
  .btn-nav {
    background: #111122; color: #7eb8f7; border: 1px solid #1a1a3a; border-radius: 4px;
    padding: 0.28rem 0.75rem; font-family: inherit; font-size: 0.75rem;
    cursor: pointer; min-width: 72px; flex-shrink: 0; transition: background 0.15s;
  }
  .btn-nav:hover { background: #1a1a3a; }
  .dot-row { flex: 1; display: flex; flex-wrap: wrap; gap: 4px; justify-content: center; align-items: center; }
  .nav-dot { width: 5px; height: 5px; border-radius: 50%; background: #1e1e2e; transition: background 0.2s; }
  .nav-dot-lg { width: 7px; height: 7px; }
  .nav-dot.active { background: #7eb8f7; }
</style>
