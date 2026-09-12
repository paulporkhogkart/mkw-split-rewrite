import { describe, it, expect } from "vitest";
import { cleanBrowserLabel, matchPythonDevice, matchBrowserDevice } from "./devices.js";

// Real labels from Paul's machine: the webview appends a "(vid:pid)" suffix to USB
// devices; DirectShow (ffmpeg -list_devices) reports the bare friendly name.
const BROWSER = [
  { deviceId: "aaa", label: "OBS Virtual Camera" },
  { deviceId: "bbb", label: "Elgato 4K X (0fd9:009b)" },
];
const PYTHON = ["Elgato 4K X", "OBS Virtual Camera"];

describe("cleanBrowserLabel", () => {
  it("strips the trailing (vid:pid) suffix and whitespace", () => {
    expect(cleanBrowserLabel("Elgato 4K X (0fd9:009b)")).toBe("Elgato 4K X");
    expect(cleanBrowserLabel("  Elgato 4K X (0fd9:009b)  ")).toBe("Elgato 4K X");
  });
  it("leaves labels without a suffix alone", () => {
    expect(cleanBrowserLabel("OBS Virtual Camera")).toBe("OBS Virtual Camera");
  });
  it("tolerates missing labels", () => {
    expect(cleanBrowserLabel("")).toBe("");
    expect(cleanBrowserLabel(undefined)).toBe("");
  });
});

describe("matchPythonDevice", () => {
  it("maps a browser label to the DirectShow name of the SAME physical device", () => {
    expect(matchPythonDevice("Elgato 4K X (0fd9:009b)", PYTHON)).toBe("Elgato 4K X");
    expect(matchPythonDevice("OBS Virtual Camera", PYTHON)).toBe("OBS Virtual Camera");
  });
  it("never cross-matches two different devices", () => {
    expect(matchPythonDevice("Elgato 4K X (0fd9:009b)", ["OBS Virtual Camera"])).toBeNull();
  });
  it("is case-insensitive and substring-tolerant either way", () => {
    expect(matchPythonDevice("elgato 4k x", ["Elgato 4K X"])).toBe("Elgato 4K X");
    expect(matchPythonDevice("Elgato 4K X (0fd9:009b)", ["Elgato 4K X (Elgato)"])).toBe("Elgato 4K X (Elgato)");
  });
  it("prefers an exact match over a substring match", () => {
    expect(matchPythonDevice("Elgato 4K X (0fd9:009b)", ["Elgato 4K X Audio", "Elgato 4K X"])).toBe("Elgato 4K X");
  });
  it("returns null with no python devices", () => {
    expect(matchPythonDevice("Elgato 4K X (0fd9:009b)", [])).toBeNull();
  });
});

describe("matchBrowserDevice", () => {
  it("finds the browser device for a DirectShow name", () => {
    expect(matchBrowserDevice("Elgato 4K X", BROWSER)?.deviceId).toBe("bbb");
    expect(matchBrowserDevice("OBS Virtual Camera", BROWSER)?.deviceId).toBe("aaa");
  });
  it("never cross-matches", () => {
    expect(matchBrowserDevice("Elgato 4K X", [BROWSER[0]])).toBeNull();
  });
  it("returns null for an empty name", () => {
    expect(matchBrowserDevice("", BROWSER)).toBeNull();
  });
});
