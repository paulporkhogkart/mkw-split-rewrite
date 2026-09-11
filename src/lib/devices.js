// devices.js - map camera devices between the two things that use the camera.
//
// INVARIANT: the webview preview (getUserMedia) and the Python engine (DirectShow via
// ffmpeg/cv2) must ALWAYS be on the same physical camera. The browser labels a USB device
// "Elgato 4K X (0fd9:009b)"; DirectShow reports the bare friendly name "Elgato 4K X".
// These helpers are the single place that translation happens, so every UI path
// (setup view + Settings modal) resolves the engine's device the same way.

const SUFFIX_RE = /\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$/i;

/** Strip the browser's trailing "(vid:pid)" suffix and surrounding whitespace. */
export function cleanBrowserLabel(label) {
  return String(label ?? "").replace(SUFFIX_RE, "").trim();
}

function sameDevice(a, b) {
  const x = a.toLowerCase(), y = b.toLowerCase();
  return x === y || x.includes(y) || y.includes(x);
}

/**
 * The DirectShow name (from the engine's devices_list) for a browser device label,
 * or null when no engine device is the same physical camera. Exact match wins over
 * substring containment so "Elgato 4K X" beats "Elgato 4K X Audio".
 */
export function matchPythonDevice(browserLabel, pythonDevices) {
  const clean = cleanBrowserLabel(browserLabel);
  if (!clean) return null;
  const names = (pythonDevices ?? []).filter((n) => typeof n === "string" && n);
  const exact = names.find((n) => n.toLowerCase() === clean.toLowerCase());
  if (exact) return exact;
  return names.find((n) => sameDevice(n, clean)) ?? null;
}

/**
 * The browser device ({deviceId,label}) that is the same physical camera as a
 * DirectShow name, or null.
 */
export function matchBrowserDevice(pythonName, browserDevices) {
  const name = String(pythonName ?? "").trim();
  if (!name) return null;
  const list = browserDevices ?? [];
  const exact = list.find((d) => cleanBrowserLabel(d.label).toLowerCase() === name.toLowerCase());
  if (exact) return exact;
  return list.find((d) => sameDevice(cleanBrowserLabel(d.label), name)) ?? null;
}
