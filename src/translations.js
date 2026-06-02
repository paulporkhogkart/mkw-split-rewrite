/**
 * UI translations for pbenguin.
 * All languages fall back to English for any missing key.
 */

const EN = {
  // ── Status bar ───────────────────────────────────────────────────────────
  "status.connecting":       "connecting…",
  "status.backend_stalled":  "backend stalled",

  // ── Header buttons ───────────────────────────────────────────────────────
  "btn.setup":               "⚙ Setup",
  "btn.close_setup":         "✕ Close Setup",

  // ── Normal view ──────────────────────────────────────────────────────────
  "log.waiting":             "Waiting for events…",
  "device.label":            "Input",
  "btn.restart":             "Restart",
  "btn.restart_to_apply":    "Restart to apply",

  // ── Wizard tabs ──────────────────────────────────────────────────────────
  "tab.language":            "Language",
  "tab.camera":              "Camera",
  "tab.screens":             "Screens",
  "tab.selection":           "Selection",
  "tab.hud":                 "HUD",
  "tab.templates":           "Templates",
  "tab.done":                "Done",

  // ── Language step ────────────────────────────────────────────────────────
  "lang.title":              "Language Settings",
  "lang.desc":               "Choose your application language and your Nintendo Switch 2 system language. These can be changed later via Setup.",
  "lang.app_label":          "Application Language",
  "lang.sw2_label":          "Nintendo Switch 2 System Language",
  "lang.sw2_hint":           "Determines which image templates are used for detection (characters, courses, menus, etc.).",
  "lang.continue":           "Continue",

  // ── Camera step ──────────────────────────────────────────────────────────
  "camera.browser_label":    "Browser / App Input",
  "camera.engine_label":     "Python Engine Input",
  "camera.select_label":     "Camera",
  "camera.hint":             "Both feeds must show your capture card output before you can continue.",
  "camera.next":             "Next: Screen Detection →",
  "camera.fix":              "← Fix Camera",
  "camera.connected":        "Connected",
  "camera.opening":          "Opening…",
  "camera.blocked":          "Blocked",
  "camera.error":            "Error",
  "camera.waiting":          "Waiting",
  "camera.released":         "Released",
  "camera.busy_title":       "Your capture card is blocking simultaneous access",
  "camera.busy_body":        "The engine feed above confirms the device works. Windows is preventing the app from opening it at the same time. This is a one-time fix:",
  "camera.busy_step1":       "Click Release engine & open settings → below",
  "camera.busy_step2":       "In the settings page, find your capture card and click it",
  "camera.busy_step3":       "Scroll to Advanced camera options → Edit",
  "camera.busy_step4":       "Turn on \"Allow multiple apps to use camera at the same time\"",
  "camera.busy_step5":       "Return here and click Retry",
  "camera.release_btn":      "Release engine & open settings →",
  "camera.released_title":   "Engine camera released",
  "camera.released_body":    "Change the Windows setting if you haven't yet, then click Retry. The engine will reopen first, then the app feed will follow.",
  "camera.error_title":      "Can't access capture card",
  "camera.error_body":       "Check that your capture card is connected and not in use by another app.",
  "camera.retry":            "Retry",
  "camera.live_hint":        "Live feed · drag handles to reposition ROI",
  "camera.unavailable":      "Camera unavailable",

  // ── Screens step ────────────────────────────────────────────────────────
  "screens.roi_primary":     "Primary",
  "screens.roi_and":         "AND",
  "screens.roi_or":          "OR Alt",
  "screens.add_and":         "+ AND",
  "screens.add_or":          "+ OR Alt",
  "screens.binarize":        "Binarize",
  "screens.auto_thresh":     "Auto threshold (Otsu)",
  "screens.capture":         "Capture New Template",
  "screens.capturing":       "Saving…",
  "screens.capture_note":    "Capture crops the current frame to this ROI and saves it as the new template.",
  "screens.saved_tmpl":      "Saved Template",
  "screens.live_crop":       "Live ROI Crop",
  "screens.score_detected":  "Detected",
  "screens.score_not_det":   "Not detected",
  "screens.score_saving":    "Saving new template…",
  "screens.score_live":      "Updating live score…",

  // ── Selection step ───────────────────────────────────────────────────────
  "sel.edge_note":           "Edge detection (Canny) - background-agnostic",
  "sel.live_edges":          "Live Crop (edges)",
  "sel.live_crop":           "Live Crop",

  // ── Templates step ───────────────────────────────────────────────────────
  "tmpl.no_template":        "No template",
  "tmpl.capture":            "Capture Template",
  "tmpl.capturing":          "Saving…",
  "tmpl.capture_note":       "Capture saves the live crop as the new template for",
  "tmpl.live_from":          "Live feed · ROI from",
  "tmpl.live_step":          "step",

  // ── Done step ────────────────────────────────────────────────────────────
  "done.title":              "Setup Complete",
  "done.body1":              "Your templates are saved and ready. The tracker will use them for screen detection immediately.",
  "done.body2":              "Re-run Setup anytime if detection quality degrades.",
  "done.close":              "Close Setup",
};

/** Language entries: only override strings that differ from English. */
const LANGS = {
  en_uk: EN,
  en_us: EN,
  fr_fr: {},
  fr_ca: {},
  de:    {},
  es_es: {},
  es_la: {},
  it:    {},
  nl:    {},
  pt_pt: {},
  pt_br: {},
  ru:    {},
  ja:    {},
  zh_tw: {},
  zh_cn: {},
  ko:    {},
  pl:    {},
  th:    {},
};

/**
 * Look up a translation key for the given language, falling back to English.
 * @param {string} key
 * @param {string} lang
 * @returns {string}
 */
export function t(key, lang = "en_uk") {
  const dict = LANGS[lang] || {};
  return dict[key] ?? EN[key] ?? key;
}
