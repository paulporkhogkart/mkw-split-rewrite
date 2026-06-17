import { describe, it, expect } from "vitest";
import { hitStyle, spriteStyle, spriteUrl, baseUrl, manifestUrl } from "./map.js";

describe("map helpers", () => {
  it("hitStyle formats a normalized rect as percentages", () => {
    expect(hitStyle({ x: 0.5, y: 0.25, w: 0.1, h: 0.2 }))
      .toBe("left:50.000%;top:25.000%;width:10.000%;height:20.000%");
  });

  it("spriteStyle positions the sprite relative to its hit box", () => {
    const hit = { x: 0.4, y: 0.4, w: 0.1, h: 0.1 };
    expect(spriteStyle(hit, hit)).toBe("left:0.000%;top:0.000%;width:100.000%;height:100.000%");
    const spr = { x: 0.38, y: 0.36, w: 0.14, h: 0.18 };
    expect(spriteStyle(hit, spr)).toBe("left:-20.000%;top:-40.000%;width:140.000%;height:180.000%");
  });

  it("URL builders point at the public /map assets", () => {
    expect(spriteUrl("rainbow_road")).toBe("/map/sprites/rainbow_road.png");
    expect(baseUrl()).toBe("/map/base.jpg");
    expect(manifestUrl()).toBe("/map/manifest.json");
  });
});
