# Functionality Inventory — regression guard for the frontend redesign

Every user-facing behavior + IPC touchpoint in the **current** `src/App.svelte`, captured before the rebuild. The redesigned app must preserve every item. Final task (7.2) ticks each box against the rebuilt UI. Symbols are current `App.svelte` references; IPC per `docs/ipc-protocol.md`.

> Legend: **sym** = current App.svelte function/state · **→** = outbound IPC command · **←** = inbound event.

## 1. Window & process
- [ ] Minimize / maximize-toggle / close — **sym** `winMinimize`,`winToggleMaximize`,`winClose` (`appWindow`).
- [ ] Drag region on title bar (`data-tauri-drag-region`).
- [ ] Sidecar spawn + readiness — **sym** `trackerSpawned`,`trackerConnected` (Rust spawns sidecar, forwards stdout as `tracker-event`).

## 2. Updater (Tauri plugin-updater)
- [ ] Detect available update — **sym** `pendingUpdate`,`updateVersion`.
- [ ] Download progress — **sym** `downloadTotal`,`downloadReceived`.
- [ ] Ready → install/relaunch — **sym** `updateReady` (plugin-process relaunch).

## 3. Connection / health
- [ ] Connection state + heartbeat staleness — **sym** `trackerConnected`,`lastHeartbeatTs`,`_tick`; health dot logic.
- [ ] FPS / current screen / live score / resolution — **sym** `backendFps`,`backendScreen`,`liveScore`,`pythonFrameW/H` ← `state`.
- [ ] `get_state` request on demand — → `get_state`.

## 4. Live readout (the monitor data)
- [ ] Screen name (+ change) — ← `screen_change`; **sym** `backendScreen`,`prevBackendScreen`.
- [ ] Per-screen candidate scores — **sym** `candidateScores` ← `state` (old Candidates panel).
- [ ] Selection: character / costume / kart / course + confidences — **sym** `selChar/selCharConf`,`selCostume/…`,`selKart/…`,`selCourse/…` ← `selection_update`.
- [ ] Lap current/total — **sym** `curLap`,`totLap` ← `lap_update`.
- [ ] Coins — **sym** `coins` ← `coin_update`.
- [ ] Mushrooms — **sym** `mushrooms` ← `state`.
- [ ] Lap splits (per-lap) — **sym** `raceSplits` ← `lap_update.split`.
- [ ] Finish total time — **sym** `raceFinishTime` ← `finish`.
- [ ] PB achieved / export — ← `pb_achieved`; → `export_pb` ← `pb_export`.

## 5. Event log
- [ ] Scrolling event log — **sym** `logs`,`logEl` (auto-scroll).

## 6. Screen-graph navigation
- [ ] Pan / zoom / fit — **sym** `gZoom`,`gPanX/Y`,`onGraphWheel`,`onGraphDown/Move/Up`,`fitGraph`,`GRAPH_W/H`.
- [ ] Click node → open editor for that screen — **sym** `nodeClick`,`openNode`,`selectedNode`; ignores click ending a pan-drag.
- [ ] Reachable-transitions layout — `TRANSITIONS` graph (nodes + edges).

## 7. Detection (tell) editing
- [ ] Load tells — → `list_tells` ← `tells_list`; **sym** `tells`,`editTell`.
- [ ] Boolean tree: groups (AND) of regions (OR) render — **sym** `editRois`.
- [ ] Select a region — **sym** `selectRegion`,`activeRegion`,`activeRegionObj`.
- [ ] Add / remove region — **sym** `addRegion`,`removeActiveRegion` → `add_region`/`remove_region`.
- [ ] Add / remove group — **sym** `addGroup`,(remove) → `add_group`/`remove_group`.
- [ ] Change region kind (template / dark_loading, incl. `icon_roi`) — **sym** `onKindChange` → `update_region`.
- [ ] Edit region ROI/threshold/grayscale — → `update_region`.
- [ ] Capture region template — **sym** `recaptureRegion` → `capture_region_template` ← `template_saved`.
- [ ] Test region (live score) — → `test_region` ← `template_score`; **sym** `currentScore`.
- [ ] Stored template + live crop thumbnails — → `get_region_images` ← `template_images`; **sym** `templateImg`,`liveCropImg`.
- [ ] Reset detection to defaults (confirm gate) — **sym** `resetDetection`,`detResetPending` → `reset_tell`.

## 8. Selection / HUD ROI editing (the "Readout" tab)
- [ ] Tabs per node — **sym** `tabsForNode`,`setTab`,`activeTab`; `NODE_SELECTION`,`NODE_HUD`.
- [ ] List config ROIs for node — **sym** `editTabRois`,`selectRoiName`,`activeRoiName`; → `list_rois` ← `rois_list`; **sym** `rois`.
- [ ] ROI canvas: zoom/pan — **sym** `fZoom`,`fPanX/Y`,`_fPanning`.
- [ ] ROI drag/resize handles — **sym** `dragging`,`dragHandle`,`hoveredHandle`,`HANDLE_HIT_RADIUS`,`dragStartRoi`.
- [ ] Engine-frame preview poll while editing — **sym** `_roiPollTimer`,`startRoiPoll`/`stopRoiPoll`,`engineFrame`.
- [ ] Per-item template capture (characters/costumes/karts/courses/mushrooms) — **sym** `selectTplItem`,`templateCategory`,`ASSET_*` → `capture_asset_template`/`get_asset_template`; **sym** `assetTemplateImg`,`assetLiveCrop` (costume = edges).
- [ ] Reset one ROI to default (confirm gate) — **sym** `resetActiveRoi`,`_activeRoiConfigKey`,`roiResetPending` → `reset_roi`.

## 9. Settings / setup
- [ ] Open settings (⚙) — **sym** `openSettings`→`openWizard`; `wizardOpen`,`wizardStep`.
- [ ] First-run gating (setup → monitor) — **sym** `setupComplete` (null/false/true), `screenIdx`,`selectionIdx`,`hudIdx`.
- [ ] Dual-feed source check: browser `<video>` + Python `engineFrame`, each with status — **sym** `mainVideoEl`,`wizVideoEl`,`cameraOk`,`cameraStatus`,`pythonCameraOk`,`pythonCameraStatus`,`trackerCameraPaused`.
- [ ] Enumerate devices (video + audio) — **sym** `loadBrowserDevices`,`browserDevices`,`audioDevices` (`enumerateDevices`).
- [ ] Select video device → restart tracker — **sym** `startCamera`,`handleCameraDeviceChange`,`selectedBrowserDeviceId`,`restartNeeded`,`restartTracker`,`deviceSwitching`.
- [ ] Select audio device — **sym** `selectedAudioDeviceId`.
- [ ] Application language — **sym** `appLanguage`,`LANGUAGES`,`tr()` → persisted (`update_config`).
- [ ] Switch system language — **sym** `switch2Language` → persisted (drives localized matching).
- [ ] (Vestigial `lang-dialog` `langDlg*` — to be removed; ensure language still reachable via Settings.)

## 10. Feed controls
- [ ] Mute / volume — **sym** `feedMuted`,`feedVolume`,`fc-btn`.
- [ ] Hide/show feed — **sym** `feedVideoHidden`.
- [ ] Feed placeholder states (connecting / opening camera / waiting / hidden) — feed-placeholder block.

## 11. Misc reactive
- [ ] `mainVideoEl.srcObject` bound to `videoStream` only when `setupComplete` — **sym** reactive at App.svelte:1625.
- [ ] Debug overlay toggle — → `toggle_debug` (if surfaced).
- [ ] force_screen / set_seed / set_roi — → `force_screen`,`set_seed`,`set_roi` (calibration/minimap config; verify still reachable or intentionally unused).

---
**Verification (Task 7.2):** tick every box via `npm run tauri dev` (engine live or `--video temp/aiden.mp4`); then `grep -rn "send(" src/` and confirm every `docs/ipc-protocol.md` command remains reachable.
