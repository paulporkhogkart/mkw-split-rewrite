import { describe, it, expect } from "vitest";
import { overlayVisibility } from "./overlay.js";

describe("overlayVisibility", () => {
  it("shows everything when nothing is hidden", () => {
    expect(overlayVisibility({ hidden: false, roiHidden: false }))
      .toEqual({ showRois: true, showMinimap: true });
  });
  it("ROI-off hides only the boxes; minimap dots stay", () => {
    expect(overlayVisibility({ hidden: false, roiHidden: true }))
      .toEqual({ showRois: false, showMinimap: true });
  });
  it("display-off hides both, regardless of the ROI toggle", () => {
    expect(overlayVisibility({ hidden: true, roiHidden: false }))
      .toEqual({ showRois: false, showMinimap: false });
    expect(overlayVisibility({ hidden: true, roiHidden: true }))
      .toEqual({ showRois: false, showMinimap: false });
  });
});
